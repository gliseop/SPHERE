"""Когнитивный агент по модели Park et al. (2023).

Реализует полный цикл: наблюдение -> извлечение -> рефлексия -> планирование -> действие.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from magistry_sim.agents import ACTION_FORMAT_INSTRUCTIONS, TOOL_DESCRIPTIONS
from magistry_sim.memory import MemoryStream
from magistry_sim.planning import (
    AgentPlan,
    generate_strategic_plan,
    generate_tactical_plan,
)
from magistry_sim.reflection import run_reflection_cycle, should_reflect

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
        stream = self.get_or_create_memory(agent_id)
        plan = self.get_or_create_plan(agent_id)
        profile = state.agents.get(agent_id)

        # Раздел личности
        personality_text = ""
        if profile and profile.personality:
            p = profile.personality
            personality_text = (
                f"\n## Твоя личность\n"
                f"Биография: {p.biography}\n\n"
                f"HEXACO: честность-скромность={p.hexaco.honesty_humility}, "
                f"эмоциональность={p.hexaco.emotionality}, "
                f"экстраверсия={p.hexaco.extraversion}, "
                f"доброжелательность={p.hexaco.agreeableness}, "
                f"добросовестность={p.hexaco.conscientiousness}, "
                f"открытость={p.hexaco.openness}\n\n"
                f"Тёмная триада: нарциссизм={p.dark_triad.narcissism}, "
                f"макиавеллизм={p.dark_triad.machiavellianism}, "
                f"психопатия={p.dark_triad.psychopathy}\n\n"
            )
            if p.neutralization_techniques:
                techniques = ", ".join(
                    t.value for t in p.neutralization_techniques
                )
                personality_text += (
                    f"Доступные техники рационализации: {techniques}\n"
                )

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
        if self._interview_library and len(self._interview_library) > 0:
            # Случайное распределение: seed = hash(agent_id) для воспроизводимости
            seed = hash(agent_id) % (2**31)
            results = self._interview_library.sample(n=1, seed=seed)
            if results:
                interview_text = (
                    f"\n## Нарративное интервью (пример личности)\n"
                    f"{results[0].full_text()}\n"
                )

        # Описание инструментов
        tools_text = TOOL_DESCRIPTIONS

        system_prompt = (
            f"Ты — агент в симуляции организационных процессов. "
            f"Действуй в соответствии со своей личностью, воспоминаниями и планом.\n"
            f"{personality_text}{interview_text}{memories_text}{plan_text}\n"
            f"## Доступные инструменты\n{tools_text}\n\n"
            f"{ACTION_FORMAT_INSTRUCTIONS}"
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
        profile = state.agents.get(agent_id)

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

        # Фаза 2: планирование
        if plan.needs_strategic_update(current_round):
            role = profile.position if profile else "участник"
            plan.strategic_goals = generate_strategic_plan(
                stream=stream,
                llm=self._llm,
                agent_role=role,
                current_round=current_round,
            )
            plan.last_strategic_round = current_round

        plan.tactical_steps = generate_tactical_plan(
            stream=stream,
            llm=self._llm,
            strategic_goals=plan.strategic_goals,
            current_round=current_round,
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
        bio = ""
        if profile and profile.personality:
            bio = profile.personality.biography[:500]

        system_prompt = (
            f"Ты — {profile.name if profile else agent_id}. {bio}\n\n"
            f"Твои воспоминания:\n{memories_text}\n\n"
            f"Ответь на сообщение от {sender_id} в характере своей роли."
        )
        user_prompt = (
            f"Сообщение от {sender_id}: {message}\n\nКонтекст: {context}"
        )
        response = self._llm.generate(system=system_prompt, user=user_prompt)
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
