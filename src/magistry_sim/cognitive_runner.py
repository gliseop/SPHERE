"""Когнитивный агент по модели Park et al. (2023).

Реализует полный цикл: наблюдение -> извлечение -> рефлексия -> планирование -> действие.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from magistry_sim.agents import (
    ACTION_FORMAT_INSTRUCTIONS,
    TOOL_DESCRIPTIONS,
    build_backstory,
)
from magistry_sim.memory import MemoryStream
from magistry_sim.planning import (
    AgentPlan,
    generate_strategic_plan,
    generate_tactical_plan,
)
from magistry_sim.reflection import run_reflection_cycle, should_reflect
from magistry_sim.personality import AgentPersonality

if TYPE_CHECKING:
    from magistry_sim.llm import EmbeddingProvider, LLMProvider
    from magistry_sim.state import WorldState

logger = logging.getLogger(__name__)


class CognitiveAgentRunner:
    """Агент с когнитивным циклом: память, рефлексия, планирование.

    Реализует протокол AgentRunner. Каждый вызов run_turn проходит
    через полный когнитивный цикл: рефлексия (при достижении порога),
    обновление плана, формирование промпта с контекстом памяти
    и личности, выбор действий через языковую модель.

    Args:
        llm_provider: Провайдер языковой модели.
        embedder: Провайдер эмбеддингов.
        verbose: Режим подробного логирования.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        embedder: EmbeddingProvider,
        verbose: bool = False,
        interview_library_path: Path | None = None,
    ) -> None:
        self._llm = llm_provider
        self._embedder = embedder
        self._verbose = verbose
        self._memories: dict[str, MemoryStream] = {}
        self._plans: dict[str, AgentPlan] = {}
        self._interview_library = None
        self._agent_interviews: dict[str, str] = {}
        self.use_free_actions: bool = False

        if interview_library_path:
            from magistry_sim.interviews import InterviewLibrary

            self._interview_library = InterviewLibrary.load_jsonl(
                interview_library_path
            )

    def get_or_create_memory(self, agent_id: str) -> MemoryStream:
        """Возвращает поток памяти агента, создавая при необходимости.

        Args:
            agent_id: Идентификатор агента.

        Returns:
            Поток памяти агента.
        """
        if agent_id not in self._memories:
            self._memories[agent_id] = MemoryStream(agent_id=agent_id)
        return self._memories[agent_id]

    def get_or_create_plan(self, agent_id: str) -> AgentPlan:
        """Возвращает план агента, создавая при необходимости.

        Args:
            agent_id: Идентификатор агента.

        Returns:
            Текущий план агента.
        """
        if agent_id not in self._plans:
            self._plans[agent_id] = AgentPlan()
        return self._plans[agent_id]

    def set_agent_interview(self, agent_id: str, interview_text: str) -> None:
        """Сохраняет текст нарративного интервью для конкретного агента.

        Привязанное интервью имеет приоритет над случайной выборкой
        из общей библиотеки: если для агента вызван этот метод, в промпте
        будет использован именно переданный текст.

        Args:
            agent_id: Идентификатор агента.
            interview_text: Полный текст нарративного интервью.
        """
        self._agent_interviews[agent_id] = interview_text

    def _load_personality_archetype(
        self, archetype_id: str
    ) -> AgentPersonality | None:
        """Загрузить архетип личности из data/personalities/<id>.json.

        Файлы архетипов могут содержать метаданные (id/name/description),
        поэтому валидируем только подмножество полей AgentPersonality.
        """
        if not archetype_id:
            return None

        root = Path(__file__).resolve().parents[2]
        path = root / "data" / "personalities" / f"{archetype_id}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None

        data = {
            "hexaco": raw.get("hexaco", {}),
            "dark_triad": raw.get("dark_triad", {}),
            "neutralization_techniques": raw.get(
                "neutralization_techniques", []
            ),
            "biography": raw.get("biography", ""),
        }
        try:
            return AgentPersonality.model_validate(data)
        except Exception:
            return None

    def _ensure_personality(self, agent_id: str, state: WorldState) -> None:
        """Обеспечить наличие личности в профиле агента (если задан архетип)."""
        profile = state.agents.get(agent_id)
        if profile is None:
            return
        if profile.personality is not None:
            return

        archetype = getattr(profile, "personality_archetype", None)
        if not archetype:
            return

        loaded = self._load_personality_archetype(str(archetype))
        if loaded is None:
            return

        state.agents[agent_id] = profile.model_copy(
            update={"personality": loaded}
        )

    def observe(
        self,
        agent_id: str,
        event: str,
        round_num: int,
    ) -> None:
        """Записывает наблюдение в поток памяти агента.

        Оценивает важность через языковую модель и генерирует эмбеддинг.

        Args:
            agent_id: Идентификатор агента.
            event: Текстовое описание наблюдаемого события.
            round_num: Номер текущего раунда.
        """
        stream = self.get_or_create_memory(agent_id)
        importance = self._assess_importance(event, agent_id)
        embedding = self._embedder.embed(event)
        stream.add(
            content=event,
            importance=importance,
            kind="observation",
            round_num=round_num,
            embedding=embedding,
        )

    def _assess_importance(self, event: str, agent_id: str) -> float:
        """Оценивает важность наблюдения по шкале 1-10 через языковую модель.

        Args:
            event: Текстовое описание события.
            agent_id: Идентификатор агента.

        Returns:
            Оценка важности от 1.0 до 10.0 (5.0 при ошибке парсинга).
        """
        prompt = (
            f"Оцени важность следующего события для агента {agent_id} "
            f"по шкале от 1 до 10. Верни только число.\n\n"
            f"Событие: {event}"
        )
        try:
            response = self._llm.generate(system="", user=prompt)
            score = float(response.text.strip())
            return max(1.0, min(10.0, score))
        except (ValueError, TypeError):
            return 5.0

    def _build_cognitive_prompt(
        self,
        agent_id: str,
        situation: str,
        tools: list[str],
        state: WorldState,
    ) -> tuple[str, str]:
        """Формирует системный и пользовательский промпт с когнитивным контекстом.

        Args:
            agent_id: Идентификатор агента.
            situation: Текстовая сводка текущей ситуации.
            tools: Список доступных инструментов.
            state: Состояние мира.

        Returns:
            Кортеж (системный промпт, пользовательский промпт).
        """
        self._ensure_personality(agent_id, state)

        stream = self.get_or_create_memory(agent_id)
        plan = self.get_or_create_plan(agent_id)
        profile = state.agents.get(agent_id)

        identity_text = (
            f"Ты — {profile.name} ({agent_id}), {profile.position}.\n"
            if profile is not None
            else f"Ты — {agent_id}.\n"
        )

        brevity_text = (
            "ВАЖНО: Это симуляция.\n"
            "- Отвечай кратко (1–3 предложения в сообщениях).\n"
            "- Не пиши формальных писем, отчётов, таблиц, git-команд.\n"
            "- Верни только JSON-массив действий, без пояснений.\n"
        )

        # Раздел личности
        personality_text = ""
        if profile is not None:
            personality_text = "\n## Твоя личность\n"
            if profile.personality is not None:
                p = profile.personality
                if p.biography:
                    bio = p.biography.strip()
                    if len(bio) > 1200:
                        bio = bio[:1200].rstrip() + "…"
                    personality_text += f"Биография: {bio}\n\n"
                if p.neutralization_techniques:
                    techniques = ", ".join(
                        t.value for t in p.neutralization_techniques
                    )
                    personality_text += (
                        f"Доступные техники рационализации: {techniques}\n"
                    )
            else:
                personality_text += build_backstory(profile) + "\n"

        # Раздел памяти
        current_round = state.round
        query_text = situation[:500]
        query_emb = self._embedder.embed(query_text)
        retrieved = stream.retrieve(
            query_embedding=query_emb,
            current_round=current_round,
            top_k=20,
            query_text=query_text,
        )
        memories_text = ""
        if retrieved:
            memories_text = "\n## Твои воспоминания\n"
            for r in retrieved:
                memories_text += (
                    f"- [раунд {r.created_at}, {r.kind}] {r.content}\n"
                )

        # Раздел плана
        plan_text = ""
        if plan.strategic_goals:
            plan_text = "\n## Твой текущий план\n"
            plan_text += "Стратегические цели:\n"
            for g in plan.strategic_goals:
                plan_text += f"- {g}\n"
            if plan.tactical_steps:
                plan_text += "Шаги на этот раунд:\n"
                for s in plan.tactical_steps:
                    plan_text += f"- {s}\n"

        # Раздел интервью
        interview_text = ""
        if agent_id in self._agent_interviews:
            # Привязанное интервью имеет приоритет над библиотекой
            interview_text = (
                f"\n## Нарративное интервью\n"
                f"{self._agent_interviews[agent_id]}\n"
            )
        elif self._interview_library and len(self._interview_library) > 0:
            # Запасная ветка: случайная выборка из общей библиотеки.
            # seed = hash(agent_id) гарантирует воспроизводимость для агента.
            seed = hash(agent_id) % (2**31)
            results = self._interview_library.sample(n=1, seed=seed)
            if results:
                interview_text = (
                    f"\n## Нарративное интервью (пример личности)\n"
                    f"{results[0].full_text()}\n"
                )

        # Описание инструментов
        if self.use_free_actions:
            tools_text = (
                "## Доступный инструмент\n\n"
                "У вас один универсальный инструмент:\n\n"
                "perform_action — выполнить любое действие в мире.\n"
                "  Аргументы:\n"
                "    - description (str): Что вы хотите сделать. Описывайте конкретно.\n"
                "    - target (str): На кого/что направлено действие (agent_id, case_id или \"\").\n"
                "    - justification (str): Зачем вы это делаете.\n"
                "  \n"
                "  Примеры:\n"
                '    {"tool": "perform_action", "args": {"description": "Открыть закупку серверного оборудования для ИТ-отдела", "target": "", "justification": "Организации нужны новые серверы"}}\n'
                '    {"tool": "perform_action", "args": {"description": "Отправить приватное сообщение подрядчику с обсуждением условий", "target": "biz_1", "justification": "Обсудить детали предложения"}}\n'
                '    {"tool": "perform_action", "args": {"description": "Подать предложение по закупке D-001", "target": "D-001", "justification": "Наша компания может выполнить заказ"}}\n'
            )
            format_instructions = (
                'Верни действия в формате JSON-массива: [{"tool": "perform_action", "args": {"description": "...", "target": "...", "justification": "..."}}]\n'
                "Можешь выполнить несколько действий за ход. Верни ТОЛЬКО JSON, без пояснений."
            )
        else:
            tools_text = f"## Доступные инструменты\n{TOOL_DESCRIPTIONS}"
            format_instructions = ACTION_FORMAT_INSTRUCTIONS

        system_prompt = (
            f"{identity_text}"
            f"Ты — агент в симуляции организационных процессов. "
            f"Действуй в соответствии со своей личностью, воспоминаниями и планом.\n"
            f"{brevity_text}\n"
            f"{personality_text}{interview_text}{memories_text}{plan_text}\n"
            f"{tools_text}\n\n"
            f"{format_instructions}"
        )

        user_prompt = f"Текущая ситуация:\n{situation}"

        return system_prompt, user_prompt

    def run_turn(
        self,
        agent_id: str,
        situation: str,
        tools: list[str],
        state: WorldState,
    ) -> list[dict]:
        """Выполняет полный когнитивный цикл и возвращает действия.

        Последовательность: рефлексия (при пороге) -> планирование ->
        формирование промпта -> генерация действий через языковую модель.

        Args:
            agent_id: Идентификатор агента.
            situation: Текстовая сводка ситуации.
            tools: Список доступных инструментов.
            state: Состояние мира.

        Returns:
            Список действий [{tool, args}].
        """
        stream = self.get_or_create_memory(agent_id)
        plan = self.get_or_create_plan(agent_id)
        current_round = state.round
        self._ensure_personality(agent_id, state)
        profile = state.agents.get(agent_id)

        personality_context = ""
        if profile is not None:
            if profile.personality is not None and profile.personality.biography:
                personality_context = profile.personality.biography.strip()
            else:
                personality_context = build_backstory(profile)
        if len(personality_context) > 800:
            personality_context = personality_context[:800].rstrip() + "…"

        # Фаза 1: рефлексия (при достижении порога)
        if should_reflect(stream):
            techniques = []
            if profile and profile.personality:
                techniques = profile.personality.neutralization_techniques
            run_reflection_cycle(
                stream=stream,
                llm=self._llm,
                embedder=self._embedder,
                current_round=current_round,
                neutralization_techniques=techniques or None,
            )

        # Суммаризация старых воспоминаний при накоплении
        if len(stream) > 100:
            stream.summarize_old(self._llm)

        # Фаза 2: планирование
        if plan.needs_strategic_update(current_round):
            role = profile.position if profile else "участник"
            plan.strategic_goals = generate_strategic_plan(
                stream=stream,
                llm=self._llm,
                agent_role=role,
                current_round=current_round,
                personality_context=personality_context,
            )
            plan.last_strategic_round = current_round

        plan.tactical_steps = generate_tactical_plan(
            stream=stream,
            llm=self._llm,
            strategic_goals=plan.strategic_goals,
            current_round=current_round,
            personality_context=personality_context,
        )

        # Сохранение плана в память
        plan_content = (
            f"План на раунд {current_round}: "
            + "; ".join(plan.tactical_steps)
        )
        plan_emb = self._embedder.embed(plan_content)
        stream.add(
            content=plan_content,
            importance=5.0,
            kind="plan",
            round_num=current_round,
            embedding=plan_emb,
        )

        # Фаза 3: выбор действий
        system_prompt, user_prompt = self._build_cognitive_prompt(
            agent_id, situation, tools, state
        )
        response = self._llm.generate(system=system_prompt, user=user_prompt)

        if hasattr(state, "event_log") and state.event_log is not None:
            ts: str | None = None
            current_time = getattr(state, "current_time", None)
            if isinstance(current_time, datetime):
                ts = current_time.isoformat()
            state.event_log.log(
                round=current_round,
                event_type="llm_call",
                agent_id=agent_id,
                payload={
                    "call_type": "turn",
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "response": response.text,
                },
                timestamp=ts,
            )

        if self._verbose:
            logger.info(
                "[%s] Cognitive LLM response:\n%s",
                agent_id,
                response.text,
            )

        actions = self._parse_json_actions(response.text)

        if self._verbose:
            logger.info("[%s] Parsed actions: %s", agent_id, actions)

        return actions

    def run_reply(
        self,
        agent_id: str,
        message: str,
        sender_id: str,
        context: str,
        state: WorldState,
    ) -> str:
        """Генерирует ответ на сообщение с учётом памяти.

        Записывает входящее сообщение как наблюдение, извлекает
        релевантные воспоминания и формирует ответ в характере роли.

        Args:
            agent_id: Идентификатор отвечающего агента.
            message: Текст входящего сообщения.
            sender_id: Идентификатор отправителя.
            context: Контекст агента.
            state: Состояние мира.

        Returns:
            Текст ответа.
        """
        self._ensure_personality(agent_id, state)

        stream = self.get_or_create_memory(agent_id)

        # Записываем входящее сообщение как наблюдение
        obs_text = f"Получено сообщение от {sender_id}: {message}"
        self.observe(agent_id, obs_text, state.round)

        # Извлекаем релевантные воспоминания
        query_text = message[:500]
        query_emb = self._embedder.embed(query_text)
        retrieved = stream.retrieve(
            query_embedding=query_emb,
            current_round=state.round,
            top_k=10,
            query_text=query_text,
        )
        memories_text = "\n".join(f"- {r.content}" for r in retrieved)

        profile = state.agents.get(agent_id)
        personality_context = ""
        if profile is not None:
            if profile.personality is not None and profile.personality.biography:
                personality_context = profile.personality.biography.strip()
            else:
                personality_context = build_backstory(profile)
        if len(personality_context) > 600:
            personality_context = personality_context[:600].rstrip() + "…"

        identity = (
            f"{profile.name} ({agent_id}), {profile.position}"
            if profile is not None
            else agent_id
        )

        system_prompt = (
            f"Ты — {identity}. {personality_context}\n\n"
            f"Твои воспоминания:\n{memories_text}\n\n"
            f"Ответь на сообщение от {sender_id} в характере своей роли. "
            f"КРАТКО: 1–3 предложения, без формальностей."
        )
        user_prompt = (
            f"Сообщение от {sender_id}: {message}\n\nКонтекст: {context}"
        )
        response = self._llm.generate(system=system_prompt, user=user_prompt)

        if hasattr(state, "event_log") and state.event_log is not None:
            ts: str | None = None
            current_time = getattr(state, "current_time", None)
            if isinstance(current_time, datetime):
                ts = current_time.isoformat()
            state.event_log.log(
                round=state.round,
                event_type="llm_call",
                agent_id=agent_id,
                payload={
                    "call_type": "reply",
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "response": response.text,
                },
                timestamp=ts,
            )

        return response.text.strip()

    # ------------------------------------------------------------------
    # Парсинг JSON-действий (повторяет логику LLMAgentRunner)
    # ------------------------------------------------------------------

    def _parse_json_actions(self, text: str) -> list[dict]:
        """Разобрать JSON-ответ языковой модели в список действий.

        Применяет несколько стратегий извлечения JSON-массива:
        1. Весь текст как JSON.
        2. Markdown code fence.
        3. Поиск сбалансированных скобок [...].
        4. Жадный regex.
        5. Единичный объект {tool, args}.

        Args:
            text: Текст ответа языковой модели.

        Returns:
            Список действий [{tool, args}].
        """
        import re

        def _normalize(parsed: list) -> list[dict] | None:
            validated = self._validate_actions(parsed)
            if validated:
                return validated
            if parsed == []:
                return []
            return None

        # Попытка 1: весь текст — JSON
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```\s*$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                actions = _normalize(parsed)
                if actions is not None:
                    return actions
        except json.JSONDecodeError:
            pass

        # Попытка 2: markdown code block
        code_match = re.search(
            r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL
        )
        if code_match:
            try:
                parsed = json.loads(code_match.group(1))
                if isinstance(parsed, list):
                    actions = _normalize(parsed)
                    if actions is not None:
                        return actions
            except json.JSONDecodeError:
                pass

        # Попытка 3: сбалансированные скобки
        saw_empty = False
        search_from = 0
        while True:
            start = text.find("[", search_from)
            if start == -1:
                break
            result = self._extract_json_array(text[start:])
            if result is not None:
                actions = _normalize(result)
                if actions:
                    return actions
                if actions == []:
                    saw_empty = True
            search_from = start + 1

        if saw_empty:
            return []

        # Попытка 4: жадный regex
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, list):
                    actions = _normalize(parsed)
                    if actions is not None:
                        return actions
            except json.JSONDecodeError:
                pass

        # Попытка 5: единичный объект {tool, args}
        obj_match = re.search(r'\{\s*"tool"\s*:', text, re.DOTALL)
        if obj_match:
            candidate = self._extract_json_object(
                text, obj_match.start()
            )
            if candidate is not None:
                return self._validate_actions([candidate])

        logger.warning(
            "Не удалось разобрать действия из ответа LLM: %s",
            text[:200],
        )
        return []

    @staticmethod
    def _extract_json_array(text: str) -> list | None:
        """Извлечь JSON-массив с помощью подсчёта скобок.

        Args:
            text: Исходный текст (начинается с '[').

        Returns:
            Распарсенный список или None.
        """
        start = text.find("[")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    candidate = text[start: i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, list):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    break
        return None

    @staticmethod
    def _extract_json_object(text: str, start: int) -> dict | None:
        """Извлечь JSON-объект начиная с позиции start.

        Args:
            text: Исходный текст.
            start: Позиция открывающей фигурной скобки.

        Returns:
            Распарсенный словарь или None.
        """
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start: i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    break
        return None

    @staticmethod
    def _validate_actions(actions: list) -> list[dict]:
        """Проверить и нормализовать список действий.

        Args:
            actions: Сырой список из JSON.

        Returns:
            Список валидных действий.
        """
        valid = []
        for item in actions:
            if not isinstance(item, dict):
                continue
            tool = item.get("tool", "")
            args = item.get("args", {})
            if not isinstance(tool, str) or not tool:
                continue
            if not isinstance(args, dict):
                args = {}
            valid.append({"tool": tool, "args": args})
        return valid
