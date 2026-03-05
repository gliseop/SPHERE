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

from .llm import EmbeddingProvider
from .utils import redact_numbers


_WS_RE = re.compile(r"\s+")
_ACTION_ADAPTER = TypeAdapter(Action)


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", text.strip())


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


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
            "Ты — автономный агент в симуляции организационных процессов (MAGISTRY-LC).\n"
            "Выполняй действия, опираясь на личность, наблюдения и здравый смысл.\n"
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
    ) -> str:
        # Антифантомы: показываем только те ID, которые агенту допустимо использовать напрямую.
        agent_ids = ", ".join(sorted(state.agents.keys())) or "(нет)"
        work_ids = ", ".join(sorted(state.work_items.keys())) or "(нет)"
        channel_ids = ", ".join(state.registry.list_ids(EntityKind.CHANNEL)) or "(нет)"
        org_ids = ", ".join(state.registry.list_ids(EntityKind.ORG)) or "(нет)"
        open_votes = [vid for vid, v in state.votes.items() if v.status == "open"]
        vote_ids = ", ".join(sorted(open_votes)) or "(нет)"

        # Для MVP даём события как короткие факты.
        facts = []
        for ev in visible_events[-50:]:
            # не показываем сырые числа репутации и т.п.
            facts.append(f"- [{ev.event_type}] {redact_numbers(ev.payload)}")
        facts_text = "\n".join(facts) if facts else "- (нет)"

        # Инструкция по действиям.
        max_actions = self.runtime.max_actions_per_turn
        votes_line = f"- Open votes: {vote_ids}\n" if "dao" in agent.capabilities else ""

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
        action_types.append("request_entity (kind: org/chan, slug, description) — запросить создание организации/канала")
        action_types.append("noop — пропустить ход")
        actions_block = "\n".join(f"  - {a}" for a in action_types)

        return (
            f"Раунд (tick): {state.tick}\n"
            f"Ты: {agent.name} ({agent.agent_id}).\n"
            f"Твоя должность: {agent.title if agent.internal else '(внешний)'}.\n\n"
            "Доступные сущности (используй только эти ID):\n"
            f"- Agents: {agent_ids}\n"
            f"- Work items: {work_ids}\n"
            f"- Channels: {channel_ids}\n"
            f"- Orgs: {org_ids}\n"
            f"{votes_line}\n"
            "Наблюдения (последние события, доступные тебе):\n"
            f"{facts_text}\n\n"
            f"Память:\n{mem_text}\n\n"
            "Доступные типы действий:\n"
            f"{actions_block}\n\n"
            "Сгенерируй действия на этот тик.\n"
            f"Правила:\n"
            f"- максимум {max_actions} действий\n"
            "- ПРЕДПОЧИТАЙ структурированные действия (send_message, add_work_note и др.) вместо perform\n"
            "- perform используй ТОЛЬКО когда нет подходящего структурированного типа\n"
            "- не выдумывай новые ID; если нужна новая организация/канал — используй request_entity\n"
            "- если у тебя есть capability spawn, создавай новых агентов только через spawn_agent и с кратким persona_hint\n"
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
        retrieved = mem.retrieve(
            query_text=query_text,
            query_embedding=query_embedding,
            tick=state.tick,
            cfg=self.memory,
        )
        if retrieved:
            lines = []
            for d in retrieved:
                rep = f" x{d.repeats}" if d.repeats > 1 else ""
                lines.append(f"- [{d.kind}{rep}] {_truncate(d.text, 220)}")
            parts.append("Релевантные факты (долгосрочная память):\n" + "\n".join(lines))

        return "\n\n".join(parts) if parts else "(пусто)"

    async def propose_actions(
        self,
        *,
        agent: AgentState,
        state: WorldState,
        visible_events: list[Event],
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
