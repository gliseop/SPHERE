"""Агентный слой MAGISTRY-LC (1 LLM-вызов на ход)."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Iterable

from pydantic import TypeAdapter, ValidationError

from .actions import Action, ActionType, actions_json_schema
from .config import MemoryConfig
from .config import RuntimeConfig
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
_ACTION_TO_ID_RE = re.compile(r"'to_id': '([^']+)'")


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", text.strip())


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
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
    fear_text = threat_text or story_state or "Потерять влияние, доверие или контроль над развитием ситуации."
    incentive_text = biography_hint or summary
    return (
        "Твоя ситуация прямо сейчас:\n"
        f"- Твои цели: {summary}\n"
        f"- Твои страхи: {fear_text}\n"
        f"- Кому и чему ты обязан: ответственность за {role_obligation}; также учитывай свои личные связи и обязательства\n"
        f"- Что тебе выгодно: {incentive_text}\n"
        f"- Что тебе угрожает: {threat_text or 'ошибка в выборе, потеря репутации, внешний шум или чужая инициатива'}\n"
    )


def _format_daily_context(daily_context: AgentDailyContext | None, scene_hooks: list[SceneHook]) -> str:
    if daily_context is None and not scene_hooks:
        return ""

    lines = ["Контекст начала дня:"]
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
            lines.append(f"- Лёгкие контакты дня: {contacts}")
            lines.append(
                "- Лёгкие контакты не имеют typed-id; не адресуй им действия напрямую, если они не стали сущностью мира."
            )
    if scene_hooks:
        lines.append("Сценовые поводы:")
        for hook in scene_hooks[:4]:
            mandatory = " [обязательная сцена]" if hook.mandatory else ""
            participants = ", ".join(hook.agents) if hook.agents else "(без списка участников)"
            lines.append(f"- {hook.kind}{mandatory}: {hook.description} | участники: {participants}")
    return "\n".join(lines) + "\n\n"


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
        elif reason.startswith("missing_capability:work"):
            match = _ACTION_WORK_ID_RE.search(action)
            if match:
                hint = (
                    f"у тебя нет capability work; не пытайся работать с {match.group(1)} "
                    "через create_work_item/add_work_note/submit_work_proposal"
                )
        elif reason.startswith("missing_capability:message"):
            match = _ACTION_TO_ID_RE.search(action)
            if match:
                hint = f"у тебя нет capability message; не пытайся отправлять сообщения в {match.group(1)}"
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

    def _build_system(self, agent: AgentState) -> str:
        lang = self.runtime.language
        return (
            "Ты — автономный участник организационного процесса.\n"
            "Действуй в рамках своей роли, наблюдений, памяти и здравого смысла.\n"
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
    ) -> str:
        # Антифантомы: показываем только те ID, которые агенту допустимо использовать напрямую.
        agent_ids = ", ".join(sorted(state.agents.keys())) or "(нет)"
        work_ids = ", ".join(sorted(state.work_items.keys())) or "(нет)"
        channel_ids = ", ".join(state.registry.list_ids(EntityKind.CHANNEL)) or "(нет)"
        org_ids = ", ".join(state.registry.list_ids(EntityKind.ORG)) or "(нет)"
        open_votes = [vid for vid, v in state.votes.items() if v.status == "open"]
        vote_ids = ", ".join(sorted(open_votes)) or "(нет)"
        work_summaries = []
        for wid in sorted(state.work_items.keys())[:12]:
            work = state.work_items[wid]
            work_summaries.append(f"- {wid}: {work.title} [{work.status}]")
        work_summaries_text = "\n".join(work_summaries) if work_summaries else "- (нет)"
        simulated_date = self.runtime.simulated_date(state.tick)
        simulated_datetime = self.runtime.simulated_datetime(state.tick)
        if simulated_datetime is not None and self.runtime.tick_granularity in {"hour", "half_day"}:
            time_line = (
                f"Текущее время мира: тик {state.tick}, дата {simulated_datetime.date().isoformat()}, "
                f"время {simulated_datetime.strftime('%H:%M')} (1 tick = {self.runtime.tick_duration_label()})\n"
            )
        elif simulated_date is not None:
            time_line = (
                f"Текущее время мира: тик {state.tick}, дата {simulated_date.isoformat()} "
                f"(1 tick = {self.runtime.tick_duration_label()})\n"
            )
        else:
            time_line = f"Текущее время мира: тик {state.tick}\n"

        # Для MVP даём события как короткие факты.
        facts = []
        for ev in visible_events[-50:]:
            # не показываем сырые числа репутации и т.п.
            facts.append(f"- [{ev.event_type}] {redact_numbers(ev.payload)}")
        facts_text = "\n".join(facts) if facts else "- (нет)"
        rejection_hints = _recent_rejection_hints(visible_events)
        rejection_hints_text = "\n".join(f"- {item}" for item in rejection_hints) if rejection_hints else "- (нет)"
        daily_context_text = _format_daily_context(daily_context, scene_hooks or [])
        motivation_block = _motivation_block(agent, visible_events)

        # Инструкция по действиям.
        max_actions = self.runtime.max_actions_per_turn
        votes_line = f"- Open votes: {vote_ids}\n"

        # Строим список доступных типов действий на основе capabilities.
        action_types: list[str] = []
        if "message" in agent.capabilities:
            action_types.append("send_message (to_id, text, private) — отправить приватное сообщение агенту (private=true) или публичное в канал/орг (private=false, to_id=chan:*/org:*)")
            action_types.append("publish (channel_id, text) — опубликовать сообщение в канале")
        if "work" in agent.capabilities:
            action_types.append("create_work_item (work_type, title, description, participants) — создать дело/задачу")
            action_types.append("add_work_note (work_id, text) — добавить заметку к делу")
            action_types.append("submit_work_proposal (work_id, text) — подать предложение по делу")
        if "dao" in agent.capabilities:
            action_types.append("nominate_position_change (target_agent_id, new_title, reason) — номинировать на должность")
            action_types.append("cast_vote (vote_id, choice: yes/no/abstain) — проголосовать")
        action_types.append("respond_nomination (vote_id, accept: true/false) — принять/отклонить номинацию")
        if "audit" in agent.capabilities:
            action_types.append("add_work_note (work_id, text) — добавить аудиторскую заметку")
        if "spawn" in agent.capabilities:
            action_types.append(
                "spawn_agent (slug, name, internal, persona_hint, capabilities) — ввести нового участника с базовой персоной"
            )
        action_types.append("perform (description, target_id) — свободное действие (когда нет подходящего типа выше)")
        if agent.internal or not self.runtime.request_entity_internal_only:
            action_types.append("request_entity (kind: org/chan, slug, description) — запросить создание организации/канала")
        action_types.append("noop — пропустить ход")
        actions_block = "\n".join(f"  - {a}" for a in action_types)
        request_entity_rule = (
            "- не выдумывай новые ID; если нужна новая организация/канал — используй request_entity\n"
            if (agent.internal or not self.runtime.request_entity_internal_only)
            else ""
        )

        return (
            f"Раунд (tick): {state.tick}\n"
            f"Ты: {agent.name} ({agent.agent_id}).\n"
            f"{time_line}"
            f"Твоя должность: {agent.title if agent.internal else '(внешний)'}.\n\n"
            f"{motivation_block}\n"
            "Доступные сущности (используй только эти ID):\n"
            f"- Agents: {agent_ids}\n"
            f"- Work items: {work_ids}\n"
            f"- Channels: {channel_ids}\n"
            f"- Orgs: {org_ids}\n"
            f"{votes_line}\n"
            "Открытые/известные дела (кратко):\n"
            f"{work_summaries_text}\n\n"
            "Наблюдения (последние события, доступные тебе):\n"
            f"{facts_text}\n\n"
            "Недавние недопустимые действия / ID:\n"
            f"{rejection_hints_text}\n\n"
            f"{daily_context_text}"
            f"Память:\n{mem_text}\n\n"
            "Доступные типы действий:\n"
            f"{actions_block}\n\n"
            "Сгенерируй действия на этот тик.\n"
            f"Правила:\n"
            f"- максимум {max_actions} действий\n"
            "- если упоминаешь даты или сроки, не противоречь канонической дате мира\n"
            "- структурированные действия — это формальные каналы, но они не обязательны во всех ситуациях\n"
            "- если реальный шаг лучше описывается неформально (намёк, давление, просьба, скрытая договорённость, обходной ход), используй perform\n"
            f"{request_entity_rule}"
            "- если у тебя есть capability spawn, создавай новых агентов только через spawn_agent и с кратким persona_hint\n"
            "- самономинация на должность запрещена; инициировать голосование можно только за другого агента\n"
            "- цель голосования не голосует сама за себя; если тебя номинировали, используй respond_nomination\n"
            "- если контекст дня и формальная процедура конфликтуют, ты вправе выбрать любой правдоподобный путь\n"
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
            parts.append("Биография (начало):\n" + _truncate(agent.persona.biography, 600))
        if agent.story_state.strip():
            parts.append("Личная линия (story state):\n" + _truncate(agent.story_state, 500))

        if mem.summary.strip():
            parts.append("Сводка (рабочая память):\n" + _truncate(mem.summary, 900))

        if mem.working:
            recent = mem.working[-10:]
            lines = "\n".join(f"- (t{e.tick}) {_truncate(e.text, 220)}" for e in recent)
            parts.append("Последние записи:\n" + lines)

        # Hybrid retrieval: по последним наблюдениям как query.
        query_text = "\n".join(
            f"{ev.event_type} {redact_numbers(ev.payload)}" for ev in visible_events[-15:]
        )
        query_embedding: list[float] | None = None
        if self.embedder is not None and query_text.strip():
            try:
                vecs = await asyncio.to_thread(self.embedder.embed_batch, [query_text])
                query_embedding = list(vecs[0]) if vecs else []
            except Exception:
                query_embedding = None
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
                + "\n".join(f"- {_truncate(item.text, 220)}" for item in persona_anchors)
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
                + "\n".join(f"- {_truncate(item.text, 220)}" for item in interview_fragments)
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
                + "\n".join(f"- {_truncate(item.text, 220)}" for item in reflections)
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
    ) -> list[Action]:
        """Сгенерировать список действий агента на тик."""
        schema = actions_json_schema(max_actions=self.runtime.max_actions_per_turn)
        system = self._build_system(agent)
        mem_text = await self._render_memory(agent=agent, state=state, visible_events=visible_events)
        user = self._build_user(
            agent=agent,
            state=state,
            visible_events=visible_events,
            mem_text=mem_text,
            daily_context=daily_context,
            scene_hooks=scene_hooks,
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
            raw = raw.get("actions", [])
        if not isinstance(raw, list):
            return []

        validated: list[Action] = []
        for item in raw[: self.runtime.max_actions_per_turn]:
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
