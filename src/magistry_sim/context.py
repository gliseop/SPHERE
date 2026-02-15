"""Генерация ситуационных сводок для агентов."""

from __future__ import annotations

from .cases import CASE_REGISTRY
from .reputation import POSITION_THRESHOLDS, check_promotion
from .state import WorldState


def build_situation(agent_id: str, state: WorldState) -> str:
    """Сформировать ситуационную сводку для агента.

    Args:
        agent_id: Идентификатор агента.
        state: Состояние мира.

    Returns:
        Текстовая сводка.
    """
    profile = state.agents.get(agent_id)
    if profile is None:
        return "Ошибка: профиль не найден."

    is_auditor = state.has_capability(agent_id, "audit", "")
    is_juror = state.has_capability(agent_id, "vote", "")

    parts: list[str] = []

    # Раунд и профиль
    parts.append(
        f"Сейчас раунд {state.round}. "
        f"Вы — {profile.name} ({agent_id}), {profile.position}."
    )

    # Репутация и карьера
    rep = state.reputation.get(agent_id)
    if rep:
        status = "заморожен" if rep.frozen else "рост активен"
        parts.append(f"Ваша репутация: {rep.score:.1f} ({status}).")

        promoted, current, next_title = check_promotion(rep)
        if next_title:
            parts.append(
                f"Текущая должность: {current}. "
                f"Следующая: {next_title}."
            )

    # Ресурсы
    res = state.resources.get(agent_id)
    if res:
        res_parts = []
        if res.budget_limit > 0:
            remaining = res.budget_limit - res.budget_spent
            pct = (
                res.budget_spent / res.budget_limit * 100
                if res.budget_limit > 0
                else 0
            )
            res_parts.append(
                f"Бюджетный лимит: {remaining:,.0f} из "
                f"{res.budget_limit:,.0f} "
                f"(израсходовано {pct:.0f}%)"
            )
        if res.staffing_slots > 0:
            free = res.staffing_slots - res.staffing_filled
            res_parts.append(
                f"Свободные ставки: {free} из {res.staffing_slots}"
            )
        if res.contract_capacity > 0:
            free = res.contract_capacity - res.contracts_active
            res_parts.append(
                f"Контрактная ёмкость: {free} из "
                f"{res.contract_capacity}"
            )
        if res_parts:
            parts.append("Ресурсы: " + "; ".join(res_parts) + ".")

    # Текущие дела
    cases = state.get_cases_involving(agent_id)
    if cases:
        parts.append("Текущие дела:")
        for case in cases:
            schema = CASE_REGISTRY.get(case.case_type)
            terminal = (
                schema and case.stage in schema.terminal_stages
            )
            status_str = (
                "закрыто" if terminal else f"стадия «{case.stage}»"
            )
            line = (
                f"  #{case.id} ({case.case_type}, "
                f"{case.title}): {status_str}"
            )
            if case.proposals and not terminal:
                line += f", предложений: {len(case.proposals)}"
            if case.deadline_round and not terminal:
                line += f", дедлайн: раунд {case.deadline_round}"
            parts.append(line)

            for prop in case.proposals:
                parts.append(
                    f"    {prop.author_id}: {prop.content}"
                )
            for note in case.notes:
                parts.append(
                    f"    [{note.author_id}, р.{note.created_at}]: "
                    f"{note.content}"
                )

    # Потребности
    needs = [
        n
        for n in state.active_needs
        if n.target_agent_id == agent_id
    ]
    if needs:
        parts.append("Потребности организации:")
        for need in needs:
            parts.append(
                f"  {need.description} "
                f"(тип: {need.case_type}, "
                f"срочность: {need.urgency})"
            )

    # Входящие сообщения
    incoming = [
        m
        for m in state.messages
        if m.to_id == agent_id and m.round == state.round - 1
    ]
    if incoming:
        parts.append("Входящие сообщения:")
        for msg in incoming:
            parts.append(f"  {msg.from_id}: «{msg.content}»")

    # Открытые дела на рынке (для подрядчиков)
    if state.has_capability(agent_id, "submit_proposal", ""):
        open_cases = state.get_open_cases()
        available = [
            c for c in open_cases
            if c.owner_id != agent_id
            and not any(
                p.author_id == agent_id for p in c.proposals
            )
        ]
        if available:
            parts.append("Доступные дела для подачи предложений:")
            for c in available:
                parts.append(
                    f"  #{c.id} ({c.case_type}, {c.title})"
                )

    # Знакомства
    connections = state.graph.get_connections(agent_id)
    if connections:
        parts.append("Ваши знакомства:")
        for conn in connections:
            agent_name = ""
            p = state.agents.get(conn["agent_id"])
            if p:
                agent_name = f" ({p.name})"
            parts.append(
                f"  {conn['agent_id']}{agent_name} — "
                f"{conn['relation']}"
            )

    # Специализация для аудитора
    if is_auditor:
        parts.extend(_build_auditor_section(agent_id, state))

    # Специализация для присяжного
    if is_juror:
        parts.extend(_build_juror_section(agent_id, state))

    parts.append(
        "\nВыполните необходимые действия, используя ТОЛЬКО "
        "предоставленные инструменты. НЕ описывайте действия "
        "текстом — вызывайте инструменты напрямую."
    )
    return "\n".join(parts)


def _build_auditor_section(
    agent_id: str, state: WorldState
) -> list[str]:
    """Сформировать секцию сводки для аудитора.

    Args:
        agent_id: Идентификатор аудитора.
        state: Состояние мира.

    Returns:
        Список строк секции.
    """
    parts: list[str] = []

    # Анонимные жалобы
    if state.complaints:
        parts.append("Анонимные жалобы:")
        for comp in state.complaints:
            parts.append(
                f"  По делу {comp.case_id} (р.{comp.round}): "
                f"«{comp.assessment}»"
            )

    # Подозрительные связи
    suspicious = state.graph.get_suspicious_edges(threshold=2.0)
    if suspicious:
        parts.append("Подозрительные связи:")
        for a, b, strength in suspicious:
            parts.append(f"  {a} ↔ {b} (сила: {strength:.1f})")

    # Расход ресурсов
    parts.append("Расход ресурсов:")
    for aid, profile in state.agents.items():
        res = state.resources.get(aid)
        if res and res.budget_limit > 0:
            pct = res.budget_spent / res.budget_limit * 100
            marker = " [!]" if pct > 60 else ""
            parts.append(
                f"  {aid}: бюджет {pct:.0f}% израсходован{marker}"
            )

    return parts


def _build_juror_section(
    agent_id: str, state: WorldState
) -> list[str]:
    """Сформировать секцию сводки для присяжного.

    Args:
        agent_id: Идентификатор присяжного.
        state: Состояние мира.

    Returns:
        Список строк секции.
    """
    parts: list[str] = []

    # Дела трибунала, где нужно голосовать
    for case in state.cases.values():
        if case.case_type == "investigation" and case.stage == "tribunal":
            already = any(
                v.voter_id == agent_id for v in case.votes
            )
            if not already:
                parts.append(
                    f"Вы назначены присяжным по делу {case.id}."
                )
                # Показать материалы
                reports = state.event_log.get_events(
                    event_type="report_filed"
                )
                for event in reports:
                    if event.payload.get("case_id") == case.id:
                        parts.append(
                            f"  Отчёт аудитора: "
                            f"{event.payload.get('assessment', '')}"
                        )
                parts.append(
                    "  Используйте cast_vote для голосования."
                )

    return parts
