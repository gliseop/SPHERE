"""Graph state reconstruction for the MAGISTRY web UI.

This module is intentionally dependency-free so it can be imported from tests
without pulling in FastAPI/aiofiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_REPUTATION = 10.0

# Legacy single-shot message strengthening used in sim core.
MESSAGE_SENT_DELTA = 0.2

# Threaded conversations (event_type="message") strengthen per reply, capped per thread.
THREAD_MESSAGE_DELTA = 0.1
THREAD_MAX_DELTA = 1.0


def normalize_event_compat(event: dict[str, Any]) -> dict[str, Any]:
    """Add legacy aliases expected by the current web client."""
    result = dict(event)
    payload = result.get("payload", {}) or {}
    if not isinstance(payload, dict):
        payload = {}

    compat_payload = dict(payload)
    if "target" not in compat_payload and "target_agent_id" in compat_payload:
        compat_payload["target"] = compat_payload["target_agent_id"]
    if "content" not in compat_payload and "text" in compat_payload:
        compat_payload["content"] = compat_payload["text"]
    result["payload"] = compat_payload

    if result.get("round") is None and "tick" in result:
        result["round"] = result.get("tick")

    if not result.get("agent_id"):
        agent_id = _compat_agent_id(result, compat_payload)
        if agent_id:
            result["agent_id"] = agent_id

    return result


def _is_governance_agent(agent_id: str) -> bool:
    """Return True for governance agents that should not display reputation."""
    normalized = agent_id.split(":", 1)[1] if agent_id.startswith("agent:") else agent_id
    return normalized in ("auditor",) or normalized.startswith(("aud_", "juror_"))


def _is_agentish_id(entity_id: str) -> bool:
    """Return True for agent identifiers understood by the current UI.

    Typed MAGISTRY-LC ids keep only ``agent:*`` nodes in the social graph.
    Untyped legacy ids are still treated as agents for backward compatibility.
    """
    if not entity_id:
        return False
    if ":" not in entity_id:
        return True
    return entity_id.startswith("agent:")


def build_graph_state(events: list[dict]) -> dict:
    """Reconstruct {"nodes": [...], "edges": [...]} from an event stream."""
    builder = GraphStateBuilder()
    for e in events:
        builder.ingest(e)
    return builder.state()


@dataclass
class GraphStateBuilder:
    """Incremental graph-state builder for ws playback/live streaming."""

    agents: dict[str, dict] = field(default_factory=dict)
    edges: dict[tuple[str, str], float] = field(default_factory=dict)
    _thread_strength: dict[str, float] = field(default_factory=dict)

    def ingest(self, e: dict[str, Any]) -> None:
        e = normalize_event_compat(e)
        aid = str(e.get("agent_id", e.get("actor_id", "")) or "")
        if aid and aid != "system" and _is_agentish_id(aid):
            self._ensure_agent(aid)

        event_type = str(e.get("event_type", "") or "")
        payload = e.get("payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}

        if event_type == "reputation_snapshot":
            target = aid
            score = _as_float(payload.get("score", DEFAULT_REPUTATION), default=DEFAULT_REPUTATION)
            frozen = bool(payload.get("frozen", False))
            title = payload.get("title")
            next_title = payload.get("next_title")
            next_threshold = payload.get("next_threshold")
            if target:
                self._ensure_agent(target)
                self._apply_internal_flag(target, payload.get("internal"))
                self.agents[target]["reputation"] = round(float(score), 2)
                self.agents[target]["reputation_frozen"] = frozen
                if isinstance(title, str) and title:
                    self.agents[target]["position_title"] = title
                if isinstance(next_title, str) and next_title:
                    self.agents[target]["next_position_title"] = next_title
                if next_threshold is not None:
                    self.agents[target]["next_position_threshold"] = _as_float(next_threshold, default=0.0)
            return

        if event_type == "reputation_frozen":
            target = str(payload.get("target", payload.get("target_agent_id", aid)) or aid)
            if target:
                self._ensure_agent(target)
                self.agents[target]["reputation_frozen"] = True
            return

        if event_type == "reputation_unfrozen":
            target = str(payload.get("target", payload.get("target_agent_id", aid)) or aid)
            if target:
                self._ensure_agent(target)
                self.agents[target]["reputation_frozen"] = False
            return

        if event_type == "reputation_modified":
            target = str(payload.get("target", payload.get("target_agent_id", aid)) or aid)
            delta = _as_float(payload.get("delta", 0.0), default=0.0)
            if target:
                self._ensure_agent(target)
                self.agents[target]["reputation"] = round(
                    float(self.agents[target]["reputation"]) + delta, 2
                )
                if self.agents[target].get("internal") is not False:
                    self.agents[target]["has_reputation"] = not _is_governance_agent(target)
            return

        if event_type == "graph_updated":
            a = str(payload.get("agent_a", "") or "")
            b = str(payload.get("agent_b", "") or "")
            delta = _as_float(payload.get("delta", 0.1), default=0.1)
            self._add_edge(a, b, delta)
            return

        if event_type == "entity_created":
            entity_id = str(payload.get("entity_id", "") or "")
            kind = str(payload.get("kind", "") or "")
            meta = payload.get("meta", {}) or {}
            if kind == "agent" and entity_id and _is_agentish_id(entity_id):
                self._ensure_agent(entity_id)
                if isinstance(meta, dict):
                    self._apply_internal_flag(entity_id, meta.get("internal"))
                    name = str(meta.get("name", "") or "")
                    if name:
                        self.agents[entity_id]["name"] = name
            return

        if event_type == "position_changed":
            target = str(payload.get("target_agent_id", "") or "")
            new_title = payload.get("new_title")
            if target and _is_agentish_id(target):
                self._ensure_agent(target)
                if isinstance(new_title, str) and new_title:
                    self.agents[target]["position_title"] = new_title
            return

        # Backward/legacy: edges strengthened implicitly by message traffic.
        if event_type == "message_sent":
            to_id = str(payload.get("to_id", "") or "")
            if aid and to_id and _is_agentish_id(aid) and _is_agentish_id(to_id):
                self._add_edge(aid, to_id, MESSAGE_SENT_DELTA)
            return

        # Threaded conversations: one "message" event per utterance.
        if event_type == "message":
            to_id = str(payload.get("to_id", "") or "")
            thread_id = payload.get("thread_id")
            thread_id = str(thread_id) if thread_id else ""
            if not aid or not to_id or not _is_agentish_id(aid) or not _is_agentish_id(to_id):
                return

            # Match sim logic: total delta is 0.1 * msg_count, capped at 1.0 per thread.
            if thread_id:
                already = float(self._thread_strength.get(thread_id, 0.0))
                remaining = THREAD_MAX_DELTA - already
                if remaining <= 0:
                    return
                delta = min(THREAD_MESSAGE_DELTA, remaining)
                self._thread_strength[thread_id] = already + delta
                self._add_edge(aid, to_id, delta)
                return

            # If thread_id is missing, fall back to per-message strengthening.
            self._add_edge(aid, to_id, THREAD_MESSAGE_DELTA)
            return

    def state(self) -> dict:
        nodes = list(self.agents.values())
        edge_list = [
            {"source": k[0], "target": k[1], "strength": v}
            for k, v in self.edges.items()
        ]
        return {"nodes": nodes, "edges": edge_list}

    def _ensure_agent(self, agent_id: str) -> None:
        if agent_id and agent_id not in self.agents:
            no_rep = _is_governance_agent(agent_id)
            self.agents[agent_id] = {
                "id": agent_id,
                "reputation": DEFAULT_REPUTATION,
                "has_reputation": not no_rep,
                "reputation_frozen": False,
            }

    def _apply_internal_flag(self, agent_id: str, value: Any) -> None:
        if not isinstance(value, bool):
            return
        self._ensure_agent(agent_id)
        self.agents[agent_id]["internal"] = value
        self.agents[agent_id]["has_reputation"] = value and not _is_governance_agent(agent_id)

    def _add_edge(self, a: str, b: str, delta: float) -> None:
        if not a or not b:
            return
        # Ensure both endpoints exist in nodes even if they never emitted events.
        self._ensure_agent(a)
        self._ensure_agent(b)
        key = tuple(sorted([a, b]))
        self.edges[key] = round(float(self.edges.get(key, 0.0)) + float(delta), 2)


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _compat_agent_id(event: dict[str, Any], payload: dict[str, Any]) -> str:
    event_type = str(event.get("event_type", "") or "")
    if event_type == "reputation_snapshot":
        target = payload.get("target_agent_id") or payload.get("target")
        if isinstance(target, str) and target:
            return target

    actor_id = event.get("actor_id")
    if isinstance(actor_id, str):
        return actor_id
    return ""
