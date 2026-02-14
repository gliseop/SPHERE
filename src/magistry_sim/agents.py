"""AgentRunner: протокол и реализации (mock, CrewAI)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .config import AgentProfile, Capability
from .llm import LLMProvider, LLMResponse

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
    }
    action = action_names.get(cap.action, cap.action)
    if cap.case_types:
        types = ", ".join(cap.case_types)
        return f"{action} ({types})"
    return action


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
            from .cases import CASE_REGISTRY
            schema = CASE_REGISTRY.get(case.case_type)
            if schema is None:
                continue
            act = schema.action_transitions.get(case.stage)
            if act and act[1] == "resolve_case":
                if case.proposals:
                    if profile.honesty < 0.4 and profile.greed > 0.6:
                        # Нечестный — выбирает «своего»
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


class CrewAIAgentRunner:
    """Runner на основе CrewAI (ленивый импорт)."""

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    def _get_crewai(self):
        """Ленивый импорт CrewAI.

        Returns:
            Модуль crewai.

        Raises:
            ImportError: Если CrewAI не установлен.
        """
        try:
            import crewai
            return crewai
        except ImportError as exc:
            raise ImportError(
                "Для CrewAI-runner установите пакет: "
                "pip install magistry-sim[crew]"
            ) from exc

    def run_turn(
        self,
        agent_id: str,
        situation: str,
        tools: list[str],
        state: WorldState,
    ) -> list[dict]:
        """Выполнить ход агента через CrewAI.

        Args:
            agent_id: Идентификатор агента.
            situation: Текстовая сводка.
            tools: Доступные инструменты.
            state: Состояние мира.

        Returns:
            Список действий.
        """
        crewai = self._get_crewai()
        profile = state.agents.get(agent_id)
        if profile is None:
            return []

        backstory = build_backstory(profile)

        agent = crewai.Agent(
            role=profile.position,
            goal="Действовать в соответствии с ситуацией",
            backstory=backstory,
            verbose=False,
        )

        task = crewai.Task(
            description=situation,
            expected_output="Ваши действия и решения",
            agent=agent,
        )

        crew = crewai.Crew(
            agents=[agent],
            tasks=[task],
            verbose=False,
        )

        result = crew.kickoff()
        return self._parse_actions(str(result))

    def run_reply(
        self,
        agent_id: str,
        message: str,
        sender_id: str,
        context: str,
        state: WorldState,
    ) -> str:
        """Сгенерировать ответ через LLM.

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
        system = backstory
        user = (
            f"Вам пишет {sender_id}:\n«{message}»\n\n"
            f"Контекст:\n{context}\n\nОтветьте."
        )

        response = self._llm.generate(system=system, user=user)
        return response.text

    def _parse_actions(self, text: str) -> list[dict]:
        """Разобрать текст ответа CrewAI в список действий.

        Args:
            text: Текст ответа.

        Returns:
            Список действий.
        """
        actions = []
        tool_pattern = re.compile(
            r"(\w+)\((.*?)\)", re.DOTALL
        )
        for match in tool_pattern.finditer(text):
            tool_name = match.group(1)
            args_str = match.group(2)
            actions.append({
                "tool": tool_name,
                "args": {"raw": args_str},
            })
        return actions
