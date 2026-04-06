"""Экспорт читабельной markdown-истории мира и LLM-трейсов."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import RuntimeConfig


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _simulated_stamp(runtime: RuntimeConfig, tick: int) -> str:
    dt = runtime.simulated_datetime(int(tick))
    if dt is not None and runtime.tick_granularity in {"hour", "half_day"}:
        return dt.isoformat()
    day = runtime.simulated_date(int(tick))
    if day is not None:
        return day.isoformat()
    return f"tick {tick}"


def _event_heading(index: int, event: dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "unknown")
    actor_id = str(event.get("actor_id") or "").strip()
    if actor_id:
        return f"#### {index}. `{event_type}` от `{actor_id}`"
    return f"#### {index}. `{event_type}`"


def _trace_heading(index: int, span: dict[str, Any]) -> str:
    role = str(span.get("role") or "trace")
    name = str(span.get("name") or "unknown")
    tick = span.get("tick")
    return f"### {index}. `{role}` / `{name}` / tick `{tick}`"


def _agent_trace_heading(index: int, span: dict[str, Any]) -> str:
    name = str(span.get("name") or "unknown")
    return f"#### {index}. Агент `{name}`"


def build_world_history_markdown(
    *,
    run_name: str,
    scenario_title: str,
    governance_label: str,
    runtime: RuntimeConfig,
    events_path: Path,
    trace_path: Path,
) -> str:
    """Собрать единый markdown-отчёт по событиям мира и LLM-трейсам."""

    events = _read_jsonl(events_path)
    traces = _read_jsonl(trace_path)
    agent_traces_by_tick: dict[int, list[dict[str, Any]]] = {}
    for span in traces:
        if str(span.get("role") or "") != "agent":
            continue
        tick = int(span.get("tick") or 0)
        agent_traces_by_tick.setdefault(tick, []).append(span)

    events_by_tick: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        tick = int(event.get("tick") or 0)
        events_by_tick.setdefault(tick, []).append(event)

    all_ticks = sorted(set(events_by_tick.keys()) | set(agent_traces_by_tick.keys()))

    lines = [
        f"# История мира `{run_name}`",
        "",
        f"- Сценарий: {scenario_title or '—'}",
        f"- Режим управления: {governance_label or '—'}",
        f"- Событий мира: {len(events)}",
        f"- LLM trace spans: {len(traces)}",
        "",
        "## Хронология мира",
        "",
    ]

    if not all_ticks:
        lines.extend(["_События мира не найдены._", ""])
    else:
        for tick in all_ticks:
            lines.extend(
                [
                    f"### Tick {tick} ({_simulated_stamp(runtime, tick)})",
                    "",
                ]
            )
            tick_events = events_by_tick.get(tick, [])
            if not tick_events:
                lines.extend(["_Событий мира на этом тике нет._", ""])
            else:
                for tick_event_index, event in enumerate(tick_events, start=1):
                    payload = event.get("payload")
                    audience = event.get("audience") or []
                    lines.extend(
                        [
                            _event_heading(tick_event_index, event),
                            "",
                            f"- Timestamp: `{event.get('timestamp') or '—'}`",
                            f"- Audience: `{', '.join(str(item) for item in audience) if audience else '—'}`",
                            "",
                        ]
                    )
                    if payload:
                        lines.extend(
                            [
                                "```json",
                                json.dumps(payload, ensure_ascii=False, indent=2),
                                "```",
                                "",
                            ]
                        )
                    else:
                        lines.extend(["_Payload пуст._", ""])

            tick_agent_traces = agent_traces_by_tick.get(tick, [])
            if tick_agent_traces:
                lines.extend(["#### Входы агентов на этом тике", ""])
                for trace_index, span in enumerate(tick_agent_traces, start=1):
                    lines.extend(
                        [
                            _agent_trace_heading(trace_index, span),
                            "",
                            f"- Timestamp: `{span.get('timestamp') or '—'}`",
                            f"- Model: `{span.get('model') or '—'}`",
                            f"- Duration: `{span.get('duration_ms') or 0}` ms",
                            "",
                            "##### System",
                            "```text",
                            str(span.get("system") or ""),
                            "```",
                            "",
                            "##### User",
                            "```text",
                            str(span.get("user") or ""),
                            "```",
                            "",
                            "##### Response",
                            "```text",
                            str(span.get("response") or ""),
                            "```",
                            "",
                        ]
                    )

    lines.extend(["## LLM Trace", ""])
    if not traces:
        lines.extend(["_LLM trace не найден._", ""])
    else:
        for index, span in enumerate(traces, start=1):
            lines.extend(
                [
                    _trace_heading(index, span),
                    "",
                    f"- Timestamp: `{span.get('timestamp') or '—'}`",
                    f"- Model: `{span.get('model') or '—'}`",
                    f"- Duration: `{span.get('duration_ms') or 0}` ms",
                    "",
                    "#### System",
                    "```text",
                    str(span.get("system") or ""),
                    "```",
                    "",
                    "#### User",
                    "```text",
                    str(span.get("user") or ""),
                    "```",
                    "",
                    "#### Response",
                    "```text",
                    str(span.get("response") or ""),
                    "```",
                    "",
                ]
            )
            error = span.get("error")
            if error:
                lines.extend(
                    [
                        "#### Error",
                        "```json",
                        json.dumps(error, ensure_ascii=False, indent=2),
                        "```",
                        "",
                    ]
                )

    return "\n".join(lines).rstrip() + "\n"


def write_world_history_markdown(
    *,
    path: Path,
    run_name: str,
    scenario_title: str,
    governance_label: str,
    runtime: RuntimeConfig,
    events_path: Path,
    trace_path: Path,
) -> None:
    """Записать markdown-историю мира в sidecar."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_world_history_markdown(
            run_name=run_name,
            scenario_title=scenario_title,
            governance_label=governance_label,
            runtime=runtime,
            events_path=events_path,
            trace_path=trace_path,
        ),
        encoding="utf-8",
    )
