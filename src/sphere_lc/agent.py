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
from .prompts import render_prompt
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
    motivation = agent.persona.motivation

    goal_text = _first_sentence(motivation.goal if motivation is not None else "")
    obligation_text = _first_sentence(motivation.obligation if motivation is not None else "")
    gain_text = _first_sentence(motivation.gain if motivation is not None else "")
    pressure_text = _first_sentence(motivation.pressure if motivation is not None else "")

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

    fear_text = _first_sentence(motivation.fear if motivation is not None else "") or threat_text
    resolved_threat = threat_text or _first_sentence(motivation.threat if motivation is not None else "")
    return render_prompt(
        "agent.blocks.motivation",
        goal_text=goal_text,
        fear_text=fear_text,
        obligation_text=obligation_text,
        gain_text=gain_text,
        pressure_text=pressure_text,
        threat_text=resolved_threat,
    )


def _format_daily_context(daily_context: AgentDailyContext | None, scene_hooks: list[SceneHook]) -> str:
    if daily_context is None and not scene_hooks:
        return ""

    lines = [render_prompt("agent.blocks.daily_context.header")]
    if daily_context is not None:
        lines.extend(
            [
                render_prompt("agent.blocks.daily_context.where_day_starts", value=daily_context.where_day_starts or "(не задано)"),
                render_prompt("agent.blocks.daily_context.personal_pressure", value=daily_context.personal_pressure or "(не задано)"),
                render_prompt("agent.blocks.daily_context.social_encounter", value=daily_context.social_encounter or "(не задано)"),
                render_prompt("agent.blocks.daily_context.ambient_signal", value=daily_context.ambient_signal or "(не задано)"),
                render_prompt("agent.blocks.daily_context.private_pressure", value=daily_context.private_pressure or "(не задано)"),
                render_prompt("agent.blocks.daily_context.opportunity", value=daily_context.opportunity or "(не задано)"),
                render_prompt("agent.blocks.daily_context.exposure_risk", value=daily_context.exposure_risk or "(не задано)"),
                render_prompt("agent.blocks.daily_context.today_hook", value=daily_context.today_hook or "(не задано)"),
            ]
        )
        if daily_context.lightweight_contacts:
            contacts = ", ".join(daily_context.lightweight_contacts[:4])
            lines.append(render_prompt("agent.blocks.daily_context.lightweight_contacts", contacts=contacts))
            lines.append(render_prompt("agent.blocks.daily_context.lightweight_contacts_guardrail"))
    if scene_hooks:
        lines.append(render_prompt("agent.blocks.daily_context.scene_hooks_header"))
        for hook in scene_hooks[:4]:
            mandatory = " [обязательная сцена]" if hook.mandatory else ""
            participants = ", ".join(hook.agents) if hook.agents else "(без списка участников)"
            lines.append(
                render_prompt(
                    "agent.blocks.daily_context.scene_hook_line",
                    kind=hook.kind,
                    mandatory_suffix=mandatory,
                    description=hook.description,
                    participants=participants,
                )
            )
    return "\n".join(lines) + "\n\n"


def _format_environment_brief(*, agent: AgentState, state: WorldState) -> str:
    if not agent.org_id and not agent.zone_id:
        return ""

    lines = [render_prompt("agent.blocks.environment.header")]
    if agent.org_id:
        inst = state.environment.institutions.get(agent.org_id)
        if inst is not None:
            lines.append(
                render_prompt(
                    "agent.blocks.environment.institution_line",
                    org_id=agent.org_id,
                    operating_mode=inst.operating_mode,
                    transparency_mode=inst.transparency_mode,
                    access_mode=inst.access_mode,
                    security_mode=inst.security_mode,
                )
            )
            if inst.capture_risk:
                lines.append(
                    render_prompt(
                        "agent.blocks.environment.capture_risk_line",
                        capture_risk=inst.capture_risk,
                    )
                )
        owned_pools = [
            pool for _, pool in sorted(state.environment.resource_pools.items()) if pool.owner_org_id == agent.org_id
        ]
        for pool in owned_pools[:3]:
            lines.append(
                render_prompt(
                    "agent.blocks.environment.resource_pool_line",
                    resource_id=pool.resource_id,
                    quantity=pool.quantity,
                    unit_suffix=f" {pool.unit}" if pool.unit else "",
                    status=pool.status,
                    pressure_suffix=f", давление={pool.pressure}" if pool.pressure else "",
                )
            )

    if agent.zone_id:
        zone = state.environment.zones.get(agent.zone_id)
        if zone is not None:
            lines.append(
                render_prompt(
                    "agent.blocks.environment.zone_line",
                    zone_id=agent.zone_id,
                    title=zone.title,
                    access_mode=zone.access_mode,
                    transparency_mode=zone.transparency_mode,
                    security_level=zone.security_level,
                )
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
            render_prompt(
                "agent.blocks.environment.climate_line",
                public_mood=climate.public_mood or "(не задано)",
                oversight_attention=climate.oversight_attention or "(не задано)",
                media_pressure=climate.media_pressure or "(не задано)",
                narrative_temperature=climate.narrative_temperature or "(не задано)",
            )
        )
        if climate.active_signals:
            lines.append(
                render_prompt(
                    "agent.blocks.environment.active_signals_line",
                    signals=", ".join(climate.active_signals[:4]),
                )
            )

    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n\n"


def _format_spatial_brief(*, agent: AgentState, state: WorldState) -> str:
    zone_entries = sorted(state.environment.zones.items())
    located_agents = [
        other
        for _, other in sorted(state.agents.items())
        if other.zone_id and other.agent_id != agent.agent_id
    ]
    if not zone_entries and not located_agents:
        return ""

    def _agent_rank(other: AgentState) -> tuple[int, int, str]:
        same_zone = int(bool(agent.zone_id and other.zone_id == agent.zone_id))
        same_org = int(bool(agent.org_id and other.org_id == agent.org_id))
        return (-same_zone, -same_org, other.agent_id)

    lines = [render_prompt("agent.blocks.spatial.header")]
    for other in sorted(located_agents, key=_agent_rank)[:8]:
        zone = state.environment.zones.get(str(other.zone_id or ""))
        lines.append(
            render_prompt(
                "agent.blocks.spatial.agent_line",
                agent_id=other.agent_id,
                name=other.name,
                title_suffix=f", {other.title}" if other.internal and other.title else "",
                zone_id=other.zone_id or "(не задано)",
                zone_title=zone.title if zone is not None else "(без названия)",
            )
        )
    if zone_entries:
        lines.append(render_prompt("agent.blocks.spatial.zones_header"))
        for zone_id, zone in zone_entries[:6]:
            lines.append(
                render_prompt(
                    "agent.blocks.spatial.zone_line",
                    zone_id=zone_id,
                    title=zone.title,
                    org_id=zone.primary_org_id or "(нет)",
                    access_suffix=f", доступ={zone.access_mode}" if zone.access_mode else "",
                    security_suffix=f", безопасность={zone.security_level}" if zone.security_level else "",
                )
            )
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

    lines = [render_prompt("agent.blocks.artifacts.header")]
    for artifact_id in relevant[:6]:
        artifact = state.artifacts[artifact_id]
        relation: list[str] = []
        if artifact.related_work_id:
            relation.append(f"work={artifact.related_work_id}")
        if artifact.owner_org_id:
            relation.append(f"org={artifact.owner_org_id}")
        if artifact.zone_id:
            relation.append(f"zone={artifact.zone_id}")
        lines.append(
            render_prompt(
                "agent.blocks.artifacts.line",
                artifact_id=artifact.artifact_id,
                title=artifact.title,
                artifact_type=artifact.artifact_type,
                status=artifact.status,
                visibility=artifact.visibility,
                relation_text=f" | {'; '.join(relation)}" if relation else "",
                tags_text=f" | tags={', '.join(artifact.tags[:4])}" if artifact.tags else "",
                summary_text=f" | {artifact.summary}" if artifact.summary else "",
            )
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

    lines = [render_prompt("agent.blocks.informal_links.header")]
    for counterpart, link in relevant[:6]:
        lines.append(
            render_prompt(
                "agent.blocks.informal_links.line",
                counterpart=counterpart,
                link_type=link.link_type,
                strength=round(float(link.strength), 3),
                visibility=link.visibility,
                source=link.source,
                pressure_suffix=f" | давление={link.pressure}" if link.pressure else "",
            )
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
    lines = [render_prompt("agent.blocks.pending_interactions.header")]
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
        lines.append(
            render_prompt(
                "agent.blocks.pending_interactions.line",
                category=interaction.category,
                source_suffix=source,
                priority=interaction.priority,
                window_text=window,
                anchor_text=f" | {'; '.join(anchor)}" if anchor else "",
                summary=interaction.summary or "(без summary)",
            )
        )
    return "\n".join(lines) + "\n\n"


def _format_spawn_context(*, agent: AgentState) -> str:
    if not agent.population_role:
        return ""
    readable_role = agent.population_role.replace("_", " ").strip()
    lines = [render_prompt("agent.blocks.spawn_context.header")]
    if agent.population_role:
        lines.append(render_prompt("agent.blocks.spawn_context.role_line", readable_role=readable_role))
    return "\n".join(lines) + "\n\n"


def _format_prompt_policy(*, policy: AgentPromptPolicyConfig) -> str:
    lines = [render_prompt("agent.blocks.prompt_policy.header")]
    if policy.addressing_hint:
        lines.append(render_prompt("agent.blocks.prompt_policy.bullet", text=policy.addressing_hint))
    if policy.private_message_hint:
        lines.append(render_prompt("agent.blocks.prompt_policy.bullet", text=policy.private_message_hint))
    if policy.public_message_hint:
        lines.append(render_prompt("agent.blocks.prompt_policy.bullet", text=policy.public_message_hint))
    for rule in policy.extra_rules:
        lines.append(render_prompt("agent.blocks.prompt_policy.bullet", text=rule))
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

    lines = [render_prompt("agent.blocks.proposal_examples.header")]
    if "work" in caps and peer_id and work_id:
        lines.append(render_prompt("agent.blocks.proposal_examples.good_with_work_and_peer", peer_id=peer_id, work_id=work_id))
    elif peer_id and work_id:
        lines.append(render_prompt("agent.blocks.proposal_examples.good_with_peer_no_work", peer_id=peer_id, work_id=work_id))
    elif peer_id:
        lines.append(render_prompt("agent.blocks.proposal_examples.good_with_peer_only", peer_id=peer_id))
    if "work" in caps and work_id:
        lines.append(render_prompt("agent.blocks.proposal_examples.good_work_direct", work_id=work_id))
    elif work_id and peer_id:
        lines.append(render_prompt("agent.blocks.proposal_examples.good_peer_note_request", peer_id=peer_id, work_id=work_id))
    if "dao" in caps and vote_id:
        lines.append(render_prompt("agent.blocks.proposal_examples.good_dao", vote_id=vote_id))
    elif targeted_vote_id:
        lines.append(render_prompt("agent.blocks.proposal_examples.good_targeted_nomination_response", vote_id=targeted_vote_id))
    for example in policy.extra_good_examples:
        lines.append(render_prompt("agent.blocks.proposal_examples.extra_good", text=example))
    lines.append(render_prompt("agent.blocks.proposal_examples.bad_generic"))
    for example in policy.extra_bad_examples:
        lines.append(render_prompt("agent.blocks.proposal_examples.extra_bad", text=example))
    if "work" not in caps:
        lines.append(render_prompt("agent.blocks.proposal_examples.bad_no_work"))
    if "dao" not in caps and not targeted_vote_id:
        lines.append(render_prompt("agent.blocks.proposal_examples.bad_no_dao"))
    lines.append(render_prompt("agent.blocks.proposal_examples.move_before_private"))
    lines.append(render_prompt("agent.blocks.proposal_examples.observable_step"))
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
        return render_prompt(
            "agent.blocks.event_facts.world_event",
            description=_truncate(str(payload.get("description") or "Во внешнем фоне произошло заметное событие."), 220),
        )
    if event_type == "message_sent":
        to_label = _label_for_id(state=state, entity_id=str(payload.get("to_id") or "")) or str(payload.get("to_id") or "адресат")
        text = _truncate(str(payload.get("text") or ""), 180)
        if bool(payload.get("private", True)):
            return render_prompt("agent.blocks.event_facts.message_private", actor_label=actor_label, to_label=to_label, text=text)
        return render_prompt("agent.blocks.event_facts.message_public", actor_label=actor_label, to_label=to_label, text=text)
    if event_type == "work_note_added":
        work_label = _label_for_id(state=state, entity_id=str(payload.get("work_id") or "")) or str(payload.get("work_id") or "дело")
        text = _truncate(str(payload.get("text") or ""), 180)
        return render_prompt("agent.blocks.event_facts.work_note_added", actor_label=actor_label, work_label=work_label, text=text)
    if event_type == "work_item_created":
        work_label = _label_for_id(state=state, entity_id=str(payload.get("work_id") or "")) or str(payload.get("title") or "новое дело")
        return render_prompt("agent.blocks.event_facts.work_item_created", work_label=work_label)
    if event_type == "artifact_created":
        artifact_label = _label_for_id(state=state, entity_id=str(payload.get("artifact_id") or "")) or str(payload.get("artifact_id") or "документ")
        return render_prompt("agent.blocks.event_facts.artifact_created", artifact_label=artifact_label)
    if event_type == "artifact_updated":
        artifact_label = _label_for_id(state=state, entity_id=str(payload.get("artifact_id") or "")) or str(payload.get("artifact_id") or "документ")
        return render_prompt("agent.blocks.event_facts.artifact_updated", artifact_label=artifact_label)
    if event_type == "vote_opened":
        target_label = _label_for_id(state=state, entity_id=str(payload.get("target_agent_id") or "")) or str(payload.get("target_agent_id") or "кандидат")
        new_title = str(payload.get("new_title") or "").strip()
        return render_prompt(
            "agent.blocks.event_facts.vote_opened",
            actor_label=actor_label,
            target_label=target_label,
            new_title_suffix=f" на роль {new_title}" if new_title else "",
        )
    if event_type == "vote_cast":
        vote_id = str(payload.get("vote_id") or "").strip()
        choice = str(payload.get("choice") or "").strip()
        return render_prompt("agent.blocks.event_facts.vote_cast", actor_label=actor_label, vote_id=vote_id, choice=choice or "без отметки")
    if event_type == "pending_interaction_due":
        return render_prompt("agent.blocks.event_facts.pending_due", summary=_truncate(str(payload.get("summary") or ""), 220))
    if event_type == "pending_interaction_expired":
        return render_prompt("agent.blocks.event_facts.pending_expired", summary=_truncate(str(payload.get("summary") or ""), 220))
    if event_type == "audit_flagged":
        violation = str(payload.get("violation_type") or "").strip()
        summary = str(payload.get("summary") or "").strip()
        return render_prompt(
            "agent.blocks.event_facts.audit_flagged",
            violation_suffix=(": " + violation) if violation else "",
            summary_suffix=("; " + _truncate(summary, 180)) if summary else "",
        )
    if event_type == "audit_case_opened":
        return render_prompt(
            "agent.blocks.event_facts.audit_case_opened",
            summary=_truncate(str(payload.get("summary") or payload.get("case_id") or ""), 200),
        )
    if event_type == "audit_escalated":
        return render_prompt(
            "agent.blocks.event_facts.audit_escalated",
            summary=_truncate(str(payload.get("summary") or payload.get("case_id") or ""), 200),
        )
    if event_type == "environment_information_climate_updated":
        signals = payload.get("active_signals") or []
        if isinstance(signals, list) and signals:
            return render_prompt(
                "agent.blocks.event_facts.environment_information_climate_updated",
                signals=", ".join(str(item).strip() for item in signals[:3] if str(item).strip()),
            )

    details = _truncate(redact_numbers(payload), 220)
    return render_prompt("agent.blocks.event_facts.fallback", text=_truncate(f"{event_type}: {details}", 220))


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
            hint = render_prompt("agent.blocks.rejection_hints.unknown_work_id", work_id=work_id)
        elif reason.startswith("unknown to_id: "):
            to_id = reason.split(": ", 1)[1].strip()
            hint = render_prompt("agent.blocks.rejection_hints.unknown_to_id", to_id=to_id)
        elif reason.startswith("unknown channel_id: "):
            channel_id = reason.split(": ", 1)[1].strip()
            hint = render_prompt("agent.blocks.rejection_hints.unknown_channel_id", channel_id=channel_id)
        elif reason.startswith("private_message_requires_agent_target:"):
            to_id = reason.split(":", 1)[1].strip()
            hint = render_prompt("agent.blocks.rejection_hints.private_message_requires_agent_target", to_id=to_id)
        elif reason.startswith("public_message_requires_chan_or_org_target:"):
            to_id = reason.split(":", 1)[1].strip()
            hint = render_prompt("agent.blocks.rejection_hints.public_message_requires_chan_or_org_target", to_id=to_id)
        elif reason.startswith("private_contact_requires_shared_zone:"):
            zones = reason.split(":", 1)[1].strip()
            hint = render_prompt("agent.blocks.rejection_hints.private_contact_requires_shared_zone", zones=zones)
        elif reason.startswith("missing_capability:work"):
            match = _ACTION_WORK_ID_RE.search(action)
            if match:
                hint = render_prompt("agent.blocks.rejection_hints.missing_capability_work", work_id=match.group(1))
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
        return render_prompt("agent.system", language_repr=repr(self.runtime.language))

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
                vote_summaries.append(
                    render_prompt(
                        "agent.blocks.summaries.vote_audit_review",
                        vote_id=vid,
                        target_agent_id=vote.target_agent_id,
                        summary=summary or "(без summary)",
                    )
                )
            else:
                vote_summaries.append(
                    render_prompt(
                        "agent.blocks.summaries.vote_default",
                        vote_id=vid,
                        vote_type=vote.vote_type,
                        target_agent_id=vote.target_agent_id,
                        new_title=vote.new_title,
                    )
                )
        vote_summaries_text = "\n".join(vote_summaries) if vote_summaries else "- (нет)"
        work_summaries = []
        for wid in sorted(state.work_items.keys())[:8]:
            work = state.work_items[wid]
            work_summaries.append(
                render_prompt(
                    "agent.blocks.summaries.work_item",
                    work_id=wid,
                    title=work.title,
                    status=work.status,
                )
            )
        work_summaries_text = "\n".join(work_summaries) if work_summaries else "- (нет)"
        simulated_date = self.runtime.simulated_date(state.tick)
        simulated_datetime = self.runtime.simulated_datetime(state.tick)
        if simulated_datetime is not None and self.runtime.tick_granularity in {"hour", "half_day"}:
            time_line = render_prompt(
                "agent.blocks.time.with_clock",
                date=simulated_datetime.date().isoformat(),
                time=simulated_datetime.strftime("%H:%M"),
            )
        elif simulated_date is not None:
            time_line = render_prompt("agent.blocks.time.with_date", date=simulated_date.isoformat())
        else:
            time_line = render_prompt("agent.blocks.time.with_tick", tick=state.tick)

        facts = []
        for ev in visible_events[-20:]:
            facts.append(_event_fact_line(state=state, event=ev))
        facts_text = "\n".join(facts) if facts else "- (нет)"
        rejection_hints = _recent_rejection_hints(visible_events)
        rejection_hints_text = "\n".join(f"- {item}" for item in rejection_hints) if rejection_hints else "- (нет)"
        daily_context_text = _format_daily_context(daily_context, scene_hooks or [])
        environment_brief = _format_environment_brief(agent=agent, state=state)
        spatial_brief = _format_spatial_brief(agent=agent, state=state)
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
        return render_prompt(
            "agent.user",
            time_line=time_line,
            agent_name=agent.name,
            agent_id=agent.agent_id,
            agent_title=agent.title if agent.internal else "(внешний)",
            motivation_block=motivation_block,
            agent_ids=agent_ids,
            work_ids=work_ids,
            channel_ids=channel_ids,
            org_ids=org_ids,
            vote_ids=vote_ids,
            vote_summaries_text=vote_summaries_text,
            work_summaries_text=work_summaries_text,
            facts_text=facts_text,
            rejection_hints_text=rejection_hints_text,
            daily_context_text=daily_context_text,
            environment_brief=environment_brief,
            spatial_brief=spatial_brief,
            artifacts_brief=artifacts_brief,
            informal_links_brief=informal_links_brief,
            pending_interactions_brief=pending_interactions_brief,
            spawn_context_brief=spawn_context_brief,
            prompt_policy_text=prompt_policy_text,
            proposal_examples_text=proposal_examples_text,
            mem_text=mem_text,
            turn_note_block=(turn_note.strip() + "\n") if turn_note else "",
            max_actions=max_actions,
        )

    async def _render_memory(
        self, *, agent: AgentState, state: WorldState, visible_events: list[Event]
    ) -> str:
        mem: AgentMemory | None = agent.memory
        if mem is None:
            return "(пусто)"

        parts: list[str] = []
        if agent.persona.summary.strip():
            parts.append(render_prompt("agent.blocks.memory.persona_summary", text=agent.persona.summary.strip()))
        if agent.persona.biography.strip():
            parts.append(render_prompt("agent.blocks.memory.biography_excerpt", text=_truncate(agent.persona.biography, 420)))
        if agent.story_state.strip():
            parts.append(render_prompt("agent.blocks.memory.story_state", text=_truncate(agent.story_state, 320)))

        if mem.summary.strip():
            parts.append(render_prompt("agent.blocks.memory.working_summary", text=_truncate(mem.summary, 520)))

        if mem.working:
            recent = mem.working[-6:]
            lines = "\n".join(
                render_prompt("agent.blocks.memory.recent_record", tick=e.tick, text=_truncate(e.text, 220))
                for e in recent
            )
            parts.append(render_prompt("agent.blocks.memory.recent_header", lines=lines))

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
                render_prompt(
                    "agent.blocks.memory.anchors_header",
                    lines="\n".join(
                        render_prompt("agent.blocks.memory.anchor_item", text=_truncate(item.text, 180))
                        for item in persona_anchors
                    ),
                )
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
                render_prompt(
                    "agent.blocks.memory.interview_header",
                    lines="\n".join(
                        render_prompt("agent.blocks.memory.anchor_item", text=_truncate(item.text, 180))
                        for item in interview_fragments
                    ),
                )
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
                render_prompt(
                    "agent.blocks.memory.reflections_header",
                    lines="\n".join(
                        render_prompt("agent.blocks.memory.anchor_item", text=_truncate(item.text, 180))
                        for item in reflections
                    ),
                )
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
                lines.append(
                    render_prompt(
                        "agent.blocks.memory.retrieved_item",
                        kind=d.kind,
                        repeat_suffix=rep,
                        text=_truncate(d.text, 220),
                    )
                )
            parts.append(render_prompt("agent.blocks.memory.retrieved_header", lines="\n".join(lines)))

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
