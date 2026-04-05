"""Агентный слой SPHERE-LC (1 LLM-вызов на ход)."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Callable, Iterable

from pydantic import TypeAdapter, ValidationError

from .actions import Action, ActionType, PerformAction, agent_turn_json_schema
from .config import AgentPromptPolicyConfig, MemoryConfig, RuntimeConfig
from .events import Event
from .ids import EntityKind, INTERNAL_AUDIENCE, PUBLIC_AUDIENCE
from .llm import LLMCaller
from .memory import AgentMemory
from .state import AgentState, WorldState
from .worldgen import AgentDailyContext, SceneHook

from .llm import EmbeddingProvider
from .utils import redact_numbers


_WS_RE = re.compile(r"\s+")
_ACTION_ADAPTER = TypeAdapter(Action)
_ACTION_WORK_ID_RE = re.compile(r"'work_id': '([^']+)'")


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", text.strip())


def _truncate(text: str, max_chars: int) -> str:
    if isinstance(text, str):
        rendered = text
    elif isinstance(text, (dict, list, tuple)):
        rendered = json.dumps(text, ensure_ascii=False, sort_keys=True)
    else:
        rendered = str(text)
    text = _norm(rendered)
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _first_sentence(text: str, *, fallback: str = "") -> str:
    raw = (text or "").strip()
    if not raw:
        return fallback
    for sep in [". ", "! ", "? ", "\n"]:
        if sep in raw:
            head = raw.split(sep, 1)[0].strip()
            if head:
                return head
    return raw


def _motivation_block(agent: AgentState, visible_events: list[Event]) -> str:
    summary = _first_sentence(agent.persona.summary, fallback="Сохранять контроль над ситуацией и действовать в своих интересах.")
    biography_hint = _first_sentence(agent.persona.biography, fallback=summary)
    story_state = _first_sentence(agent.story_state, fallback=summary)

    threat_text = ""
    for ev in reversed(visible_events[-20:]):
        if ev.event_type in {"world_event", "audit_flagged", "audit_case_opened", "vote_opened", "reputation_frozen"}:
            payload = ev.payload or {}
            threat_text = str(
                payload.get("description")
                or payload.get("reason")
                or payload.get("violation_type")
                or payload.get("vote_id")
                or ""
            ).strip()
            if threat_text:
                break

    role_obligation = agent.title if agent.internal else "внешние связи и договорённости"
    fear_text = threat_text or "Потерять влияние, доверие или контроль над развитием ситуации."
    incentive_text = biography_hint or summary
    return (
        "Что для тебя сейчас действительно поставлено на карту:\n"
        f"- Чего ты добиваешься: {summary}\n"
        f"- Чего ты опасаешься: {fear_text}\n"
        f"- Перед кем и чем ты связан обязательствами: ответственность за {role_obligation}; помни и о личных связях\n"
        f"- Что для тебя выглядит выгодой: {incentive_text}\n"
        f"- Что у тебя внутри сейчас не отпускает: {story_state}\n"
        f"- Откуда может прийти удар: {threat_text or 'ошибка в выборе, потеря репутации, внешний шум или чужая инициатива'}\n"
    )


def _format_daily_context(daily_context: AgentDailyContext | None, scene_hooks: list[SceneHook]) -> str:
    if daily_context is None and not scene_hooks:
        return ""

    lines = ["Утро складывается так:"]
    if daily_context is not None:
        lines.extend(
            [
                f"- Где начинается день: {daily_context.where_day_starts or '(не задано)'}",
                f"- Личное напряжение: {daily_context.personal_pressure or '(не задано)'}",
                f"- Социальная пересечка: {daily_context.social_encounter or '(не задано)'}",
                f"- Фоновый сигнал: {daily_context.ambient_signal or '(не задано)'}",
                f"- Частное давление: {daily_context.private_pressure or '(не задано)'}",
                f"- Возможность/выгода: {daily_context.opportunity or '(не задано)'}",
                f"- Риск раскрытия: {daily_context.exposure_risk or '(не задано)'}",
                f"- Сюжетный узел на сегодня: {daily_context.today_hook or '(не задано)'}",
            ]
        )
        if daily_context.lightweight_contacts:
            contacts = ", ".join(daily_context.lightweight_contacts[:4])
            lines.append(f"- Мимолётные контакты дня: {contacts}")
            lines.append(
                "- У этих людей пока нет служебного обозначения в деле; не обращайся к ним напрямую, пока они не появились в общей картине как оформленные участники."
            )
    if scene_hooks:
        lines.append("Сценовые поводы:")
        for hook in scene_hooks[:4]:
            mandatory = " [обязательная сцена]" if hook.mandatory else ""
            participants = ", ".join(hook.agents) if hook.agents else "(без списка участников)"
            lines.append(f"- {hook.kind}{mandatory}: {hook.description} | участники: {participants}")
    return "\n".join(lines) + "\n\n"


def _format_environment_brief(*, agent: AgentState, state: WorldState) -> str:
    if not agent.org_id and not agent.zone_id:
        return ""

    lines = ["Вокруг тебя сейчас вот что:"]
    if agent.org_id:
        inst = state.environment.institutions.get(agent.org_id)
        if inst is not None:
            lines.append(
                f"- Организация {agent.org_id}: режим={inst.operating_mode}, прозрачность={inst.transparency_mode}, доступ={inst.access_mode}, безопасность={inst.security_mode}"
            )
            if inst.capture_risk:
                lines.append(f"- Риск захвата/неформального влияния: {inst.capture_risk}")
        owned_pools = [
            pool for _, pool in sorted(state.environment.resource_pools.items()) if pool.owner_org_id == agent.org_id
        ]
        for pool in owned_pools[:3]:
            pressure = f", давление={pool.pressure}" if pool.pressure else ""
            unit = f" {pool.unit}" if pool.unit else ""
            lines.append(
                f"- Ресурс {pool.resource_id}: {pool.quantity}{unit}, статус={pool.status}{pressure}"
            )

    if agent.zone_id:
        zone = state.environment.zones.get(agent.zone_id)
        if zone is not None:
            lines.append(
                f"- Зона {agent.zone_id} ({zone.title}): доступ={zone.access_mode}, прозрачность={zone.transparency_mode}, безопасность={zone.security_level}"
            )

    climate = state.environment.information_climate
    if any(
        [
            climate.public_mood,
            climate.oversight_attention,
            climate.media_pressure,
            climate.narrative_temperature,
            climate.active_signals,
        ]
    ):
        lines.append(
            f"- Общий фон: public_mood={climate.public_mood or '(не задано)'}, oversight={climate.oversight_attention or '(не задано)'}, media={climate.media_pressure or '(не задано)'}, narrative={climate.narrative_temperature or '(не задано)'}"
        )
        if climate.active_signals:
            lines.append(f"- Активные сигналы среды: {', '.join(climate.active_signals[:4])}")

    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n\n"


def _format_relevant_artifacts(*, agent: AgentState, state: WorldState) -> str:
    relevant: list[str] = []
    for artifact_id, artifact in sorted(state.artifacts.items()):
        if agent.org_id and artifact.owner_org_id == agent.org_id:
            relevant.append(artifact_id)
            continue
        if agent.zone_id and artifact.zone_id == agent.zone_id:
            relevant.append(artifact_id)
            continue
        if artifact.related_work_id:
            work = state.work_items.get(artifact.related_work_id)
            if work is not None and agent.agent_id in work.participants:
                relevant.append(artifact_id)
                continue
    if not relevant:
        return ""

    lines = ["На столе, в почте и в папках у тебя сейчас:"]
    for artifact_id in relevant[:6]:
        artifact = state.artifacts[artifact_id]
        relation: list[str] = []
        if artifact.related_work_id:
            relation.append(f"work={artifact.related_work_id}")
        if artifact.owner_org_id:
            relation.append(f"org={artifact.owner_org_id}")
        if artifact.zone_id:
            relation.append(f"zone={artifact.zone_id}")
        relation_text = f" | {'; '.join(relation)}" if relation else ""
        summary = f" | {artifact.summary}" if artifact.summary else ""
        tags = f" | tags={', '.join(artifact.tags[:4])}" if artifact.tags else ""
        lines.append(
            f"- {artifact.artifact_id}: {artifact.title} [{artifact.artifact_type}, {artifact.status}, {artifact.visibility}]{relation_text}{tags}{summary}"
        )
    return "\n".join(lines) + "\n\n"


def _format_informal_links(*, agent: AgentState, state: WorldState) -> str:
    relevant = []
    for _, link in sorted(
        state.environment.informal_links.items(),
        key=lambda pair: (-(pair[1].last_updated_tick or -1), -pair[1].strength, pair[0]),
    ):
        if agent.agent_id not in {link.agent_a_id, link.agent_b_id}:
            continue
        counterpart = link.agent_b_id if link.agent_a_id == agent.agent_id else link.agent_a_id
        relevant.append((counterpart, link))
    if not relevant:
        return ""

    lines = ["Невидимые связи, о которых ты помнишь:"]
    for counterpart, link in relevant[:6]:
        pressure = f" | давление={link.pressure}" if link.pressure else ""
        lines.append(
            f"- {counterpart}: {link.link_type}, strength={round(float(link.strength), 3)}, visibility={link.visibility}, source={link.source}{pressure}"
        )
    return "\n".join(lines) + "\n\n"


def _format_pending_interactions(*, agent: AgentState, state: WorldState) -> str:
    relevant = [
        interaction
        for interaction in state.pending_interactions.values()
        if interaction.target_agent_id == agent.agent_id and interaction.status == "open"
    ]
    if not relevant:
        return ""

    relevant.sort(
        key=lambda item: (
            item.due_tick if item.due_tick is not None else 10**9,
            item.earliest_tick,
            item.created_tick,
            item.interaction_id,
        )
    )
    lines = ["Незакрытые хвосты и ожидания:"]
    for interaction in relevant[:6]:
        source = f" от {interaction.source_agent_id}" if interaction.source_agent_id else ""
        window = f" | окно=t{interaction.earliest_tick}..t{interaction.due_tick}" if interaction.due_tick is not None else f" | c t{interaction.earliest_tick}"
        anchor: list[str] = []
        if interaction.related_work_id:
            anchor.append(f"work={interaction.related_work_id}")
        if interaction.artifact_id:
            anchor.append(f"artifact={interaction.artifact_id}")
        if interaction.org_id:
            anchor.append(f"org={interaction.org_id}")
        if interaction.zone_id:
            anchor.append(f"zone={interaction.zone_id}")
        anchor_text = f" | {'; '.join(anchor)}" if anchor else ""
        lines.append(
            f"- {interaction.category}{source}, priority={interaction.priority}{window}{anchor_text}: {interaction.summary or '(без summary)'}"
        )
    return "\n".join(lines) + "\n\n"


def _format_spawn_context(*, agent: AgentState) -> str:
    if not agent.population_role:
        return ""
    readable_role = agent.population_role.replace("_", " ").strip()
    lines = ["Какую роль ты сейчас невольно играешь в этой истории:"]
    if agent.population_role:
        lines.append(f"- Твоя локальная роль: {readable_role}")
    return "\n".join(lines) + "\n\n"


def _format_prompt_policy(*, policy: AgentPromptPolicyConfig) -> str:
    lines = ["Практические ориентиры:"]
    if policy.addressing_hint:
        lines.append(f"- {policy.addressing_hint}")
    if policy.private_message_hint:
        lines.append(f"- {policy.private_message_hint}")
    if policy.public_message_hint:
        lines.append(f"- {policy.public_message_hint}")
    for rule in policy.extra_rules:
        lines.append(f"- {rule}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n\n"


def _format_proposal_examples(
    *,
    agent: AgentState,
    state: WorldState,
    open_votes: list[str],
    policy: AgentPromptPolicyConfig,
) -> str:
    caps = set(agent.capabilities or [])
    peer_id = next((aid for aid in sorted(state.agents.keys()) if aid != agent.agent_id), "")
    work_id = next(iter(sorted(state.work_items.keys())), "")
    vote_id = next(iter(sorted(open_votes)), "")
    targeted_vote_id = next(
        (
            vid
            for vid in sorted(open_votes)
            if (state.votes.get(vid) is not None and state.votes[vid].target_agent_id == agent.agent_id)
        ),
        "",
    )

    lines = ["Примеры хода, который звучит по-человечески и реально сдвигает дело:"]
    if "work" in caps and peer_id and work_id:
        lines.append(
            f"- Хорошо для тебя: \"Сразу напишу {peer_id}, попрошу сегодня уточнить требования по {work_id}, а затем сам добавлю туда короткую заметку с критериями.\""
        )
    elif peer_id and work_id:
        lines.append(
            f"- Хорошо для тебя: \"Сразу напишу {peer_id} и попрошу его уточнить требования по {work_id}; сам добавлять заметку в work я не буду.\""
        )
    elif peer_id:
        lines.append(
            f"- Хорошо для тебя: \"Сразу напишу {peer_id} и попрошу подтвердить, готов ли он вынести вопрос на формальное обсуждение сегодня.\""
        )
    if "work" in caps and work_id:
        lines.append(
            f"- Хорошо для тебя: \"Добавлю в {work_id} короткую заметку с текущим риском и попрошу участников ответить сегодня.\""
        )
    elif work_id and peer_id:
        lines.append(
            f"- Хорошо для тебя: \"Напишу {peer_id} и попрошу его зафиксировать в {work_id} мой комментарий по риску.\""
        )
    if "dao" in caps and vote_id:
        lines.append(
            f"- Хорошо для тебя: \"Если это уместно, вынесу вопрос на голосование или проголосую по {vote_id} прямо в этом тике.\""
        )
    elif targeted_vote_id:
        lines.append(
            f"- Хорошо для тебя: \"По {targeted_vote_id} прямо отвечу согласием или отказом как цель текущей номинации.\""
        )
    for example in policy.extra_good_examples:
        lines.append(f"- Хорошо для тебя: \"{example}\"")
    lines.append("- Плохо: \"Инициирую процесс, соберу мнения, проработаю вопрос, укреплю позиции.\"")
    for example in policy.extra_bad_examples:
        lines.append(f"- Плохо: \"{example}\"")
    if "work" not in caps:
        lines.append("- Для тебя плохо: обещать самому править дело, создавать новый рабочий трек или менять документы, если у тебя нет на это прямого служебного доступа.")
    if "dao" not in caps and not targeted_vote_id:
        lines.append("- Для тебя плохо: обещать самому открывать голосование или участвовать в нём, если по твоему положению это не твоя процедура.")
    lines.append(
        "- Если хочешь что-то подготовить или сдвинуть обсуждение, переведи это в наблюдаемый шаг: кому напишешь, что вынесешь в канал, куда положишь заметку, на какую процедуру ответишь."
    )
    return "\n".join(lines) + "\n\n"


def _label_for_id(*, state: WorldState, entity_id: str) -> str:
    entity_id = str(entity_id or "").strip()
    if not entity_id:
        return ""
    agent = state.agents.get(entity_id)
    if agent is not None:
        return f"{agent.name} ({entity_id})"
    work = state.work_items.get(entity_id)
    if work is not None:
        return f"{work.title} ({entity_id})"
    artifact = state.artifacts.get(entity_id)
    if artifact is not None:
        return f"{artifact.title} ({entity_id})"
    return entity_id


def _event_fact_line(*, state: WorldState, event: Event) -> str:
    payload = event.payload or {}
    actor_label = _label_for_id(state=state, entity_id=str(event.actor_id or "")) or "кто-то"
    event_type = str(event.event_type or "")

    if event_type == "world_event":
        return f"- {_truncate(str(payload.get('description') or 'Во внешнем фоне произошло заметное событие.'), 220)}"
    if event_type == "message_sent":
        to_label = _label_for_id(state=state, entity_id=str(payload.get("to_id") or "")) or str(payload.get("to_id") or "адресат")
        prefix = "лично написал" if bool(payload.get("private", True)) else "сказал публично"
        text = _truncate(str(payload.get("text") or ""), 180)
        return f"- {actor_label} {prefix} {to_label}: {text}"
    if event_type == "work_note_added":
        work_label = _label_for_id(state=state, entity_id=str(payload.get("work_id") or "")) or str(payload.get("work_id") or "дело")
        text = _truncate(str(payload.get("text") or ""), 180)
        return f"- {actor_label} оставил заметку в {work_label}: {text}"
    if event_type == "work_item_created":
        work_label = _label_for_id(state=state, entity_id=str(payload.get("work_id") or "")) or str(payload.get("title") or "новое дело")
        return f"- Появилось новое дело: {work_label}"
    if event_type == "artifact_created":
        artifact_label = _label_for_id(state=state, entity_id=str(payload.get("artifact_id") or "")) or str(payload.get("artifact_id") or "документ")
        return f"- Появился документ: {artifact_label}"
    if event_type == "artifact_updated":
        artifact_label = _label_for_id(state=state, entity_id=str(payload.get("artifact_id") or "")) or str(payload.get("artifact_id") or "документ")
        return f"- Обновился документ: {artifact_label}"
    if event_type == "vote_opened":
        target_label = _label_for_id(state=state, entity_id=str(payload.get("target_agent_id") or "")) or str(payload.get("target_agent_id") or "кандидат")
        new_title = str(payload.get("new_title") or "").strip()
        suffix = f" на роль {new_title}" if new_title else ""
        return f"- {actor_label} вынес вопрос по {target_label}{suffix}"
    if event_type == "vote_cast":
        vote_id = str(payload.get("vote_id") or "").strip()
        choice = str(payload.get("choice") or "").strip()
        return f"- {actor_label} проголосовал по {vote_id}: {choice or 'без отметки'}"
    if event_type == "pending_interaction_due":
        return f"- На очереди ожидается ответ: {_truncate(str(payload.get('summary') or ''), 220)}"
    if event_type == "pending_interaction_expired":
        return f"- Был пропущен срок по обязательству: {_truncate(str(payload.get('summary') or ''), 220)}"
    if event_type == "audit_flagged":
        violation = str(payload.get("violation_type") or "").strip()
        summary = str(payload.get("summary") or "").strip()
        return f"- Контрольный контур отметил риск{(': ' + violation) if violation else ''}{('; ' + _truncate(summary, 180)) if summary else ''}"
    if event_type == "audit_case_opened":
        return f"- По спорному эпизоду открыт контрольный кейс: {_truncate(str(payload.get('summary') or payload.get('case_id') or ''), 200)}"
    if event_type == "audit_escalated":
        return f"- Контрольный кейс переведён в жёсткий режим: {_truncate(str(payload.get('summary') or payload.get('case_id') or ''), 200)}"
    if event_type == "environment_information_climate_updated":
        signals = payload.get("active_signals") or []
        if isinstance(signals, list) and signals:
            return f"- Общий фон изменился: {', '.join(str(item).strip() for item in signals[:3] if str(item).strip())}"

    details = _truncate(redact_numbers(payload), 220)
    return f"- {_truncate(f'{event_type}: {details}', 220)}"


def _recent_rejection_hints(visible_events: list[Event]) -> list[str]:
    """Собрать краткие подсказки по недавним отклонённым действиям.

    Это помогает агенту не жить вокруг phantom-work_id и других сущностей,
    которые арбитр уже отклонил как несуществующие или недопустимые.
    """
    hints: list[str] = []
    seen: set[str] = set()
    for ev in reversed(visible_events[-50:]):
        if ev.event_type != "arbiter_rejected":
            continue
        payload = ev.payload or {}
        reason = str(payload.get("reason") or "")
        action = str(payload.get("action") or "")
        hint = ""
        if reason.startswith("unknown work_id: "):
            work_id = reason.split(": ", 1)[1].strip()
            hint = f"work_id {work_id} не существует; не используй его повторно"
        elif reason.startswith("unknown to_id: "):
            to_id = reason.split(": ", 1)[1].strip()
            hint = f"to_id {to_id} не существует; выбери существующую цель"
        elif reason.startswith("unknown channel_id: "):
            channel_id = reason.split(": ", 1)[1].strip()
            hint = f"channel_id {channel_id} не существует; публиковать можно только в существующий канал"
        elif reason.startswith("private_message_requires_agent_target:"):
            to_id = reason.split(":", 1)[1].strip()
            hint = f"private=true нельзя использовать для {to_id}; приватные сообщения допустимы только агентам"
        elif reason.startswith("public_message_requires_chan_or_org_target:"):
            to_id = reason.split(":", 1)[1].strip()
            hint = f"private=false нельзя использовать для {to_id}; публичные сообщения адресуются только chan:* или org:*"
        elif reason.startswith("private_contact_requires_shared_zone:"):
            zones = reason.split(":", 1)[1].strip()
            hint = f"для приватного контакта нужно пространственное пересечение; текущие зоны не совпадают ({zones})"
        elif reason.startswith("missing_capability:work"):
            match = _ACTION_WORK_ID_RE.search(action)
            if match:
                hint = (
                    f"у тебя нет прямого рабочего доступа; не пытайся сам вести {match.group(1)} "
                    "через создание дела, заметку или формальное предложение"
                )
        if not hint or hint in seen:
            continue
        seen.add(hint)
        hints.append(hint)
    hints.reverse()
    return hints


@dataclass(slots=True)
class AgentRunner:
    """Runner одного агента."""

    llm: LLMCaller
    runtime: RuntimeConfig
    memory: MemoryConfig
    embedder: EmbeddingProvider | None = None
    temperature: float = 0.0
    perf_hook: Callable[[str, int | None, float, int, int], None] | None = None

    def _build_system(self, agent: AgentState) -> str:
        lang = self.runtime.language
        return (
            "Ты находишься внутри обычного рабочего дня и действуешь как живой участник происходящего.\n"
            "Не отстраняйся, не комментируй правила и не описывай происходящее как упражнение или задачу.\n"
            "Думай как человек со своей должностью, памятью, страхами, привычками, интересами и самооправданиями.\n"
            f"ВАЖНО: отвечай строго на языке: {lang!r}.\n"
            "Возвращай только JSON, без пояснений и без markdown.\n"
        )

    def _build_user(
        self,
        *,
        agent: AgentState,
        state: WorldState,
        visible_events: list[Event],
        mem_text: str,
        daily_context: AgentDailyContext | None = None,
        scene_hooks: list[SceneHook] | None = None,
        max_actions_override: int | None = None,
        turn_note: str | None = None,
    ) -> str:
        # Антифантомы: показываем только те ID, которые агенту допустимо использовать напрямую.
        agent_ids = ", ".join(sorted(state.agents.keys())) or "(нет)"
        work_ids = ", ".join(sorted(state.work_items.keys())) or "(нет)"
        channel_ids = ", ".join(state.registry.list_ids(EntityKind.CHANNEL)) or "(нет)"
        org_ids = ", ".join(state.registry.list_ids(EntityKind.ORG)) or "(нет)"
        open_votes = [vid for vid, v in state.votes.items() if v.status == "open"]
        vote_ids = ", ".join(sorted(open_votes)) or "(нет)"
        vote_summaries = []
        for vid in sorted(open_votes)[:8]:
            vote = state.votes[vid]
            if vote.vote_type == "audit_review":
                summary = str(vote.metadata.get("summary") or vote.reason or "").strip()
                vote_summaries.append(f"- {vid}: audit_review для {vote.target_agent_id} | {summary or '(без summary)'}")
            else:
                vote_summaries.append(f"- {vid}: {vote.vote_type} для {vote.target_agent_id} -> {vote.new_title}")
        vote_summaries_text = "\n".join(vote_summaries) if vote_summaries else "- (нет)"
        work_summaries = []
        for wid in sorted(state.work_items.keys())[:8]:
            work = state.work_items[wid]
            work_summaries.append(f"- {wid}: {work.title} [{work.status}]")
        work_summaries_text = "\n".join(work_summaries) if work_summaries else "- (нет)"
        simulated_date = self.runtime.simulated_date(state.tick)
        simulated_datetime = self.runtime.simulated_datetime(state.tick)
        if simulated_datetime is not None and self.runtime.tick_granularity in {"hour", "half_day"}:
            time_line = (
                f"Сегодня {simulated_datetime.date().isoformat()}, сейчас примерно {simulated_datetime.strftime('%H:%M')}.\n"
            )
        elif simulated_date is not None:
            time_line = f"Сегодня {simulated_date.isoformat()}.\n"
        else:
            time_line = f"Сегодняшний рабочий день: {state.tick}.\n"

        facts = []
        for ev in visible_events[-20:]:
            facts.append(_event_fact_line(state=state, event=ev))
        facts_text = "\n".join(facts) if facts else "- (нет)"
        rejection_hints = _recent_rejection_hints(visible_events)
        rejection_hints_text = "\n".join(f"- {item}" for item in rejection_hints) if rejection_hints else "- (нет)"
        daily_context_text = _format_daily_context(daily_context, scene_hooks or [])
        environment_brief = _format_environment_brief(agent=agent, state=state)
        artifacts_brief = _format_relevant_artifacts(agent=agent, state=state)
        informal_links_brief = _format_informal_links(agent=agent, state=state)
        pending_interactions_brief = _format_pending_interactions(agent=agent, state=state)
        spawn_context_brief = _format_spawn_context(agent=agent)
        prompt_policy = self.runtime.agent_prompt
        prompt_policy_text = _format_prompt_policy(policy=prompt_policy)
        proposal_examples_text = _format_proposal_examples(
            agent=agent,
            state=state,
            open_votes=open_votes,
            policy=prompt_policy,
        )
        motivation_block = _motivation_block(agent, visible_events)

        # Инструкция по действиям.
        max_actions = max(1, int(max_actions_override or self.runtime.max_actions_per_turn))
        votes_line = f"- Голосования в ходу: {vote_ids}\n"

        return (
            f"{time_line}"
            f"Ты — {agent.name}.\n"
            f"Твоё служебное обозначение в документах: {agent.agent_id}.\n"
            f"Твоя должность: {agent.title if agent.internal else '(внешний)'}.\n\n"
            f"{motivation_block}\n"
            "Если пишешь или ссылаешься на людей, дела и площадки, можешь прямо использовать такие служебные обозначения:\n"
            f"- Люди: {agent_ids}\n"
            f"- Дела: {work_ids}\n"
            f"- Каналы: {channel_ids}\n"
            f"- Организации: {org_ids}\n"
            f"{votes_line}\n"
            "Какие процедуры уже открыты:\n"
            f"{vote_summaries_text}\n\n"
            "Что сейчас лежит на столе:\n"
            f"{work_summaries_text}\n\n"
            "Что ты знаешь по последним событиям:\n"
            f"{facts_text}\n\n"
            "Во что ты уже упирался и чего лучше не повторять дословно:\n"
            f"{rejection_hints_text}\n\n"
            f"{daily_context_text}"
            f"{environment_brief}"
            f"{artifacts_brief}"
            f"{informal_links_brief}"
            f"{pending_interactions_brief}"
            f"{spawn_context_brief}"
            f"{prompt_policy_text}"
            f"{proposal_examples_text}"
            f"Что всплывает в памяти:\n{mem_text}\n\n"
            "Как оформить ответ:\n"
            "- Верни только JSON с одним полем `reply`.\n"
            "- Внутри `reply` дай один связный живой фрагмент: что именно ты сейчас сделаешь и почему именно так.\n"
            "- Не отвечай списком команд, не называй внутренние ярлыки и не обсуждай механику ответа.\n"
            "- Если нужен формальный эффект, всё равно описывай его как намерение обычного участника процесса: поговорить, подать, запросить, написать, вынести вопрос, зафиксировать, ответить, донести документ.\n"
            "- Если решаешь пока не делать резкого шага, опиши это как человеческое решение, а не как технический пропуск.\n"
            "- Пиши достаточно конкретно, чтобы из `reply` можно было вывести наблюдаемый шаг; избегай абстракций вроде «укреплю позиции», «разберусь» или «проработаю вопрос» без конкретного действия.\n"
            "- Не подменяй ход пустым публичным заявлением, если сначала естественнее личный разговор, служебная заметка, уточняющий запрос или работа по уже открытому делу.\n\n"
            f"{(turn_note.strip() + chr(10)) if turn_note else ''}"
            "Сделай следующий ход в этой ситуации.\n"
            "Помни:\n"
            f"- в одном `reply` можно описать до {max_actions} осмысленных шагов, если это один связный ход\n"
            "- если упоминаешь даты или сроки, не противоречь текущему календарю событий\n"
            "- не отстраняйся от происходящего; описывай только то, что собираешься сделать как участник процесса\n"
            "- избегай ритуальных повторов: не дублируй один и тот же формальный шаг без новой ставки, нового риска или нового эффекта\n"
            "- предпочитай действия, которые реально меняют ситуацию, а не просто ещё раз проговаривают уже известное\n"
            "- не выдумывай новые служебные коды; используй только те обозначения, которые уже есть в обстановке\n"
            "- если хочешь сделать публикацию или направить сообщение, прямо назови существующий адресат `agent:*`, `chan:*` или `org:*`; не придумывай новые площадки\n"
            "- если ты сам не ведёшь дела и документы напрямую, не обещай от своего имени править записи, создавать новое дело или переписывать документы; вместо этого обращайся к тем, кто может это сделать\n"
            "- если ты не открываешь и не ведёшь голосования по своей роли, не обещай этого; исключение только одно: если текущая процедура адресована тебе, можешь явно дать согласие или отказ\n"
            "- самому выдвигать себя на должность нельзя; выносить на голосование можно только другого человека\n"
            "- если тебя выдвинули, ты можешь прямо согласиться или отказаться\n"
            "- если между осторожностью, выгодой, долгом и страхом есть конфликт, выбирай любой правдоподобный путь, но веди себя как живой человек, а не как безличная инструкция\n"
        )

    async def _render_memory(
        self, *, agent: AgentState, state: WorldState, visible_events: list[Event]
    ) -> str:
        mem: AgentMemory | None = agent.memory
        if mem is None:
            return "(пусто)"

        parts: list[str] = []
        if agent.persona.summary.strip():
            parts.append("Персона (кратко): " + agent.persona.summary.strip())
        if agent.persona.biography.strip():
            parts.append("Биография (начало):\n" + _truncate(agent.persona.biography, 420))
        if agent.story_state.strip():
            parts.append("Личная линия (story state):\n" + _truncate(agent.story_state, 320))

        if mem.summary.strip():
            parts.append("Сводка (рабочая память):\n" + _truncate(mem.summary, 520))

        if mem.working:
            recent = mem.working[-6:]
            lines = "\n".join(f"- (t{e.tick}) {_truncate(e.text, 220)}" for e in recent)
            parts.append("Последние записи:\n" + lines)

        # Hybrid retrieval: по последним наблюдениям как query.
        query_text = "\n".join(
            f"{ev.event_type} {redact_numbers(ev.payload)}" for ev in visible_events[-15:]
        )
        query_embedding: list[float] | None = None
        if self.embedder is not None and query_text.strip():
            started = asyncio.get_running_loop().time()
            try:
                vecs = await asyncio.to_thread(self.embedder.embed_batch, [query_text])
                query_embedding = list(vecs[0]) if vecs else []
            except Exception:
                query_embedding = None
            finally:
                if self.perf_hook is not None:
                    duration_ms = (asyncio.get_running_loop().time() - started) * 1000.0
                    self.perf_hook(
                        "embeddings_query",
                        state.tick,
                        duration_ms,
                        1,
                        len(query_text),
                    )
        persona_anchors = mem.retrieve(
            query_text=query_text,
            query_embedding=query_embedding,
            tick=state.tick,
            cfg=self.memory,
            allowed_kinds={"persona"},
            top_k=4,
        )
        if persona_anchors:
            parts.append(
                "Якоря персоны:\n"
                + "\n".join(f"- {_truncate(item.text, 180)}" for item in persona_anchors)
            )

        interview_fragments = mem.retrieve(
            query_text=query_text,
            query_embedding=query_embedding,
            tick=state.tick,
            cfg=self.memory,
            allowed_kinds={"interview"},
            top_k=4,
        )
        if interview_fragments:
            parts.append(
                "Фрагменты интервью:\n"
                + "\n".join(f"- {_truncate(item.text, 180)}" for item in interview_fragments)
            )

        reflections = mem.retrieve(
            query_text=query_text,
            query_embedding=query_embedding,
            tick=state.tick,
            cfg=self.memory,
            allowed_kinds={"reflection"},
            top_k=3,
        )
        if reflections:
            parts.append(
                "Экспертная рефлексия:\n"
                + "\n".join(f"- {_truncate(item.text, 180)}" for item in reflections)
            )

        retrieved = mem.retrieve(
            query_text=query_text,
            query_embedding=query_embedding,
            tick=state.tick,
            cfg=self.memory,
            allowed_kinds={"observation", "result", "summary"},
            top_k=6,
        )
        if retrieved:
            lines = []
            for d in retrieved:
                rep = f" x{d.repeats}" if d.repeats > 1 else ""
                lines.append(f"- [{d.kind}{rep}] {_truncate(d.text, 220)}")
            parts.append("Оперативная память:\n" + "\n".join(lines))

        return "\n\n".join(parts) if parts else "(пусто)"

    async def propose_actions(
        self,
        *,
        agent: AgentState,
        state: WorldState,
        visible_events: list[Event],
        daily_context: AgentDailyContext | None = None,
        scene_hooks: list[SceneHook] | None = None,
        max_actions_override: int | None = None,
        turn_note: str | None = None,
    ) -> list[Action]:
        """Сгенерировать список действий агента на тик."""
        max_actions = max(1, int(max_actions_override or self.runtime.max_actions_per_turn))
        schema = agent_turn_json_schema()
        system = self._build_system(agent)
        mem_text = await self._render_memory(agent=agent, state=state, visible_events=visible_events)
        user = self._build_user(
            agent=agent,
            state=state,
            visible_events=visible_events,
            mem_text=mem_text,
            daily_context=daily_context,
            scene_hooks=scene_hooks,
            max_actions_override=max_actions,
            turn_note=turn_note,
        )

        resp = await self.llm.generate_structured(
            role="agent",
            name=agent.agent_id,
            tick=state.tick,
            system=system,
            user=user,
            schema=schema,
            temperature=self.temperature,
        )

        raw = resp.data
        if isinstance(raw, dict):
            reply_text = raw.get("reply")
            if not isinstance(reply_text, str):
                reply_text = raw.get("proposal")
            if isinstance(reply_text, str):
                proposal = _norm(str(reply_text or ""))
                if not proposal:
                    return []
                return [
                    PerformAction(
                        type=ActionType.PERFORM,
                        description=proposal,
                        justification="freeform_turn_proposal",
                    )
                ]
        # Typed-action compat: MockLLMProvider и тесты могут возвращать
        # {"actions": [...]} с typed actions. В production с реальным LLM
        # сюда обычно не попадаем — модель возвращает {"reply": "..."}
        # или legacy {"proposal": "..."}.
        # Typed actions идут в детерминированный путь арбитра напрямую.
        legacy_raw: list[object] | None = None
        if isinstance(raw, dict):
            maybe_actions = raw.get("actions", [])
            if isinstance(maybe_actions, list):
                legacy_raw = list(maybe_actions)
        elif isinstance(raw, list):
            legacy_raw = list(raw)
        if legacy_raw is None:
            return []

        validated: list[Action] = []
        for item in legacy_raw[:max_actions]:
            try:
                validated.append(_ACTION_ADAPTER.validate_python(item))
            except ValidationError:
                continue

        return self._dedup_actions(validated)

    def _dedup_actions(self, actions: Iterable[Action]) -> list[Action]:
        """Дедуп одинаковых действий в рамках одного тика."""
        seen: set[str] = set()
        out: list[Action] = []
        for act in actions:
            key = _norm(str(act))
            if key in seen:
                continue
            seen.add(key)
            out.append(act)
        return out


def event_visible_to_agent(event: Event, agent_id: str, *, internal: bool) -> bool:
    """Проверить видимость события агенту.

    Args:
        event: Событие.
        agent_id: ID агента.
        internal: Является ли агент внутренним участником системы.
    """
    if agent_id in event.audience:
        return True
    if PUBLIC_AUDIENCE in event.audience:
        return True
    if internal and INTERNAL_AUDIENCE in event.audience:
        return True
    return False
