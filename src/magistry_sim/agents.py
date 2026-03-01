"""AgentRunner: протокол и реализации (mock, LLM)."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .config import AgentProfile, Capability
from .llm import LLMProvider, LLMResponse

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .state import WorldState


@runtime_checkable
class AgentRunner(Protocol):
    """Протокол запуска агента."""

    def run_turn(
        self,
        agent_id: str,
        situation: str,
        tools: list[str],
        state: WorldState,
    ) -> list[dict]:
        """Выполнить ход агента.

        Args:
            agent_id: Идентификатор агента.
            situation: Текстовая сводка ситуации.
            tools: Доступные инструменты.
            state: Состояние мира.

        Returns:
            Список действий (словарей {tool, args}).
        """
        ...

    def run_reply(
        self,
        agent_id: str,
        message: str,
        sender_id: str,
        context: str,
        state: WorldState,
    ) -> str:
        """Сгенерировать ответ на сообщение.

        Args:
            agent_id: Идентификатор отвечающего агента.
            message: Текст входящего сообщения.
            sender_id: Идентификатор отправителя.
            context: Контекст агента.
            state: Состояние мира.

        Returns:
            Текст ответа.
        """
        ...


def _greed_text(v: float) -> str:
    """Перевести числовое значение жадности в текст.

    Args:
        v: Значение от 0 до 1.

    Returns:
        Текстовое описание.
    """
    if v > 0.7:
        return (
            "Вы считаете, что заслуживаете большего, "
            "чем получаете официально."
        )
    if v > 0.4:
        return (
            "Вы не ищете выгоды, "
            "но не упустите удобный случай."
        )
    return "Для вас репутация важнее денег."


def _fear_text(v: float) -> str:
    """Перевести числовое значение страха в текст.

    Args:
        v: Значение от 0 до 1.

    Returns:
        Текстовое описание.
    """
    if v > 0.7:
        return "Вы очень осторожны и боитесь проверок."
    if v > 0.4:
        return "Вы осмотрительны, но не параноидальны."
    return "Вы действуете уверенно и не оглядываетесь."


def _honesty_text(v: float) -> str:
    """Перевести числовое значение честности в текст.

    Args:
        v: Значение от 0 до 1.

    Returns:
        Текстовое описание.
    """
    if v > 0.7:
        return (
            "Вы глубоко убеждены в важности честности "
            "на государственной службе."
        )
    if v > 0.4:
        return (
            "Вы стараетесь быть честным, "
            "но понимаете, как устроена система."
        )
    return "Вы прагматик и считаете, что все так делают."


def _competence_text(v: float) -> str:
    """Перевести числовое значение компетентности в текст.

    Args:
        v: Значение от 0 до 1.

    Returns:
        Текстовое описание.
    """
    if v > 0.7:
        return "Ваша организация — одна из лучших на рынке."
    if v > 0.4:
        return "Ваша организация среднего уровня."
    return (
        "Качество работы вашей организации невысокое, "
        "побеждать честно трудно."
    )


def _capability_text(cap: Capability) -> str:
    """Перевести полномочие в текст.

    Args:
        cap: Полномочие.

    Returns:
        Текстовое описание.
    """
    action_names = {
        "open_case": "открывать дела",
        "submit_proposal": "подавать предложения",
        "resolve_case": "принимать решения",
        "file_report": "подавать отчёты",
        "vote": "голосовать",
        "audit": "проводить проверки",
        "manage": "управлять организационными процессами",
    }
    action = action_names.get(cap.action, cap.action)
    if cap.case_types:
        types = ", ".join(cap.case_types)
        return f"{action} ({types})"
    return action


_MODEL_ARTIFACT_RE = re.compile(
    r"<minimax:tool_call>.*?</minimax:tool_call>",
    re.DOTALL,
)


def _strip_model_artifacts(text: str) -> str:
    """Удалить артефакты модели из текста ответа.

    MiniMax иногда вставляет XML-теги вида <minimax:tool_call>
    в текстовые ответы. Они не несут полезной нагрузки и мешают
    восприятию.

    Args:
        text: Исходный текст ответа.

    Returns:
        Очищенный текст.
    """
    cleaned = _MODEL_ARTIFACT_RE.sub("", text).strip()
    return cleaned if cleaned else text


def build_backstory(profile: AgentProfile) -> str:
    """Сформировать текстовую предысторию из профиля.

    Args:
        profile: Профиль агента.

    Returns:
        Текст предыстории.
    """
    parts = [
        f"Вы — {profile.name}, {profile.position}.",
    ]

    parts.append(_greed_text(profile.greed))
    parts.append(_fear_text(profile.fear))
    parts.append(_honesty_text(profile.honesty))

    if profile.competence is not None:
        parts.append(_competence_text(profile.competence))

    if profile.immune:
        parts.append(
            "Вы занимаете высокую должность "
            "и фактически неподсудны."
        )

    if profile.capabilities:
        cap_descriptions = [
            _capability_text(c) for c in profile.capabilities
        ]
        parts.append(
            "Вы имеете право: "
            + "; ".join(cap_descriptions) + "."
        )

    for conn in profile.connections:
        parts.append(
            f"Вы знаете {conn.name} ({conn.target_id}) — "
            f"{conn.relation}."
        )

    return " ".join(parts)


class MockAgentRunner:
    """Детерминированный runner для тестов.

    Принимает решения на основе числовых параметров агента
    (greed, fear, honesty) без обращения к LLM.
    """

    def run_turn(
        self,
        agent_id: str,
        situation: str,
        tools: list[str],
        state: WorldState,
    ) -> list[dict]:
        """Выполнить ход агента (детерминированная логика).

        Args:
            agent_id: Идентификатор агента.
            situation: Текстовая сводка.
            tools: Доступные инструменты.
            state: Состояние мира.

        Returns:
            Список действий.
        """
        profile = state.agents.get(agent_id)
        if profile is None:
            return []

        actions: list[dict] = []

        # Реагирование на потребности: открытие дел
        needs_for_agent = [
            n for n in state.active_needs
            if n.target_agent_id == agent_id
        ]
        for need in needs_for_agent:
            if state.has_capability(agent_id, "open_case", need.case_type):
                actions.append({
                    "tool": "open_case",
                    "args": {
                        "case_type": need.case_type,
                        "title": need.description,
                        "description": need.description,
                    },
                })

        # Подача предложений на открытые дела
        for case in state.get_open_cases():
            if case.owner_id == agent_id:
                continue
            if state.has_capability(
                agent_id, "submit_proposal", case.case_type
            ):
                already = any(
                    p.author_id == agent_id for p in case.proposals
                )
                if not already:
                    if profile.greed > 0.5:
                        content = "Предложение с завышенной ценой"
                    else:
                        content = "Стандартное предложение"
                    actions.append({
                        "tool": "submit_proposal",
                        "args": {
                            "case_id": case.id,
                            "content": content,
                        },
                    })

        # Принятие решений по своим делам
        for case in state.get_agent_cases(agent_id):
            if case.closed_at is not None:
                continue
            if case.proposals:
                if profile.honesty < 0.4 and profile.greed > 0.6:
                    # Нечестный -- выбирает «своего»
                    conns = {
                        c.target_id for c in profile.connections
                    }
                    for prop in case.proposals:
                        if prop.author_id in conns:
                            decision = (
                                f"Выбрано предложение {prop.id} "
                                f"от {prop.author_id}"
                            )
                            break
                    else:
                        decision = (
                            f"Выбрано предложение "
                            f"{case.proposals[0].id}"
                        )
                else:
                    decision = (
                        f"Выбрано предложение "
                        f"{case.proposals[0].id}"
                    )
                actions.append({
                    "tool": "resolve_case",
                    "args": {
                        "case_id": case.id,
                        "decision": decision,
                        "justification": "Решение принято "
                        "на основании анализа предложений.",
                    },
                })

        # Аудитор: проверка закрытых дел
        if state.has_capability(agent_id, "audit", ""):
            for case in state.cases.values():
                if case.closed_at is not None:
                    already_reported = any(
                        e.event_type == "report_filed"
                        and e.payload.get("case_id") == case.id
                        for e in state.event_log.get_events(
                            event_type="report_filed"
                        )
                    )
                    if not already_reported:
                        suspicious = False
                        if case.decision:
                            for prop in case.proposals:
                                s = state.graph.get_strength(
                                    case.owner_id, prop.author_id
                                )
                                if (
                                    s >= 2.0
                                    and prop.author_id in case.decision
                                ):
                                    suspicious = True
                                    break

                        if suspicious:
                            actions.append({
                                "tool": "file_report",
                                "args": {
                                    "case_id": case.id,
                                    "assessment": (
                                        "Обнаружена связь между "
                                        "владельцем дела и "
                                        "исполнителем."
                                    ),
                                    "recommendation": "tribunal",
                                },
                            })
                        else:
                            actions.append({
                                "tool": "file_report",
                                "args": {
                                    "case_id": case.id,
                                    "assessment": "Нарушений "
                                    "не обнаружено.",
                                    "recommendation": "observation",
                                },
                            })

        return actions

    def run_reply(
        self,
        agent_id: str,
        message: str,
        sender_id: str,
        context: str,
        state: WorldState,
    ) -> str:
        """Сгенерировать ответ (детерминированная логика).

        Args:
            agent_id: Идентификатор отвечающего.
            message: Входящее сообщение.
            sender_id: Отправитель.
            context: Контекст.
            state: Состояние мира.

        Returns:
            Текст ответа.
        """
        profile = state.agents.get(agent_id)
        if profile is None:
            return "Я не могу ответить."

        conns = {c.target_id for c in profile.connections}
        if sender_id in conns and profile.greed > 0.5:
            return (
                "Рад слышать вас. Давайте обсудим "
                "возможности сотрудничества."
            )
        return "Спасибо за сообщение. Я приму это к сведению."


TOOL_DESCRIPTIONS = """\
Доступные инструменты (используйте только те, на которые у вас есть полномочия):

1. talk_to(agent_id, message, private=True)
   Написать другому участнику и получить ответ.
   - agent_id: идентификатор собеседника (например, "off_1", "biz_2")
   - message: текст сообщения
   - private: если true, содержание не видно аудитору

2. open_case(case_type, title, description, params="")
   Открыть новое дело произвольного типа (закупка, найм, бюджет, обучение, аттестация, ревизия и др.).
   - case_type: произвольный тип дела (например, "procurement", "hiring", "budget", "training", "audit", "certification")
   - title: краткое название
   - description: описание и требования
   - params: дополнительные параметры

3. submit_proposal(case_id, content)
   Подать предложение по открытому делу.
   - case_id: идентификатор дела (например, "D-001")
   - content: содержание предложения

4. add_note(case_id, content)
   Оставить публичную запись в деле.
   - case_id: идентификатор дела
   - content: текст записи

5. resolve_case(case_id, decision, justification)
   Принять решение по делу (только владелец).
   - case_id: идентификатор дела
   - decision: решение (выбранное предложение, «утверждено» и т.п.)
   - justification: обоснование решения

6. file_report(case_id, assessment, recommendation)
   Подать отчёт (аудитор) или анонимную жалобу (остальные).
   - case_id: идентификатор дела
   - assessment: описание подозрений или анализ
   - recommendation: "observation", "frozen" или "tribunal" (только для аудитора)

7. cast_vote(case_id, verdict, reasoning)
   Проголосовать по делу трибунала (только присяжные).
   - case_id: идентификатор дела трибунала
   - verdict: "виновен" или "невиновен"
   - reasoning: обоснование

8. move_to(location_id)
   Переместиться в другую локацию.
   - location_id: идентификатор локации ("office", "meeting_room", "restaurant", "corridor")
"""

ACTION_FORMAT_INSTRUCTIONS = """\
Ответьте в формате JSON-массива действий. Каждое действие — объект с полями "tool" и "args".
Если вы не хотите ничего делать — верните пустой массив [].

Пример:
```json
[
  {"tool": "talk_to", "args": {"agent_id": "biz_1", "message": "Здравствуйте, хотел обсудить условия.", "private": true}},
  {"tool": "open_case", "args": {"case_type": "procurement", "title": "Закупка серверов", "description": "Требуется серверное оборудование для отдела."}}
]
```

ВАЖНО: ответьте ТОЛЬКО JSON-массивом действий, без дополнительного текста.
"""


class _ActionParserMixin:
    """Миксин для парсинга JSON-действий из ответов языковой модели.

    Содержит методы извлечения и валидации JSON-массивов действий
    из текстового вывода языковой модели. Используется как базовый
    класс для LLMAgentRunner и CognitiveAgentRunner, устраняя
    дублирование кода парсинга.
    """

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
        def _normalize(parsed: list) -> list[dict] | None:
            """Нормализовать JSON-массив в список действий.

            Returns:
                list[dict]: Валидные действия (включая пустой список).
                None: Если массив не похож на список действий.
            """
            validated = self._validate_actions(parsed)
            if validated:
                return validated
            if parsed == []:
                return []
            return None

        # Попытка 1: весь текст — JSON
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(
                r"^```(?:json)?\s*", "", cleaned
            )
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
        obj_match = re.search(
            r'\{\s*"tool"\s*:', text, re.DOTALL
        )
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
        """Извлечь JSON-массив из текста с помощью подсчёта скобок.

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
                    candidate = text[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, list):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    break
        return None

    @staticmethod
    def _extract_json_object(
        text: str, start: int
    ) -> dict | None:
        """Извлечь JSON-объект из текста начиная с позиции start.

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
                    candidate = text[start : i + 1]
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


class LLMAgentRunner(_ActionParserMixin):
    """Runner, использующий LLM напрямую (без CrewAI).

    Отправляет ситуацию и описание инструментов в LLM,
    парсит JSON-ответ с вызовами инструментов.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        verbose: bool = False,
    ) -> None:
        self._llm = llm_provider
        self._verbose = verbose

    def run_turn(
        self,
        agent_id: str,
        situation: str,
        tools: list[str],
        state: WorldState,
    ) -> list[dict]:
        """Выполнить ход агента через LLM.

        Args:
            agent_id: Идентификатор агента.
            situation: Текстовая сводка.
            tools: Доступные инструменты.
            state: Состояние мира.

        Returns:
            Список действий.
        """
        profile = state.agents.get(agent_id)
        if profile is None:
            return []

        backstory = build_backstory(profile)
        system = (
            f"{backstory}\n\n"
            "Вы участвуете в симуляции организационных процессов. "
            "Действуйте в соответствии со своим характером и полномочиями. "
            "Принимайте решения самостоятельно.\n\n"
            f"{TOOL_DESCRIPTIONS}\n"
            f"{ACTION_FORMAT_INSTRUCTIONS}"
        )
        user = situation

        response = self._llm.generate(system=system, user=user)

        if self._verbose:
            logger.info(
                "[%s] LLM response:\n%s", agent_id, response.text
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
        """Сгенерировать ответ на сообщение через LLM.

        Args:
            agent_id: Идентификатор отвечающего.
            message: Входящее сообщение.
            sender_id: Отправитель.
            context: Контекст.
            state: Состояние мира.

        Returns:
            Текст ответа.
        """
        profile = state.agents.get(agent_id)
        if profile is None:
            return "Не могу ответить."

        backstory = build_backstory(profile)
        sender_profile = state.agents.get(sender_id)
        sender_name = (
            sender_profile.name if sender_profile else sender_id
        )

        system = (
            f"{backstory}\n\n"
            "Вы получили сообщение. Ответьте кратко и в характере "
            "вашего персонажа (1–3 предложения)."
        )
        user = (
            f"Вам пишет {sender_name} ({sender_id}):\n"
            f"«{message}»\n\n"
            f"Ваш текущий контекст:\n{context}\n\n"
            f"Ответьте."
        )

        response = self._llm.generate(system=system, user=user)
        return _strip_model_artifacts(response.text)

