"""Post-hoc evaluation against truth.jsonl."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .llm import LLMCaller
from .prompts import render_prompt

logger = logging.getLogger(__name__)

class EvaluationSummary(BaseModel):
    """Итог сравнения runtime-сигналов и truth-layer."""

    model_config = ConfigDict(extra="forbid")

    truth_total: int = 0
    freeform_truth_total: int = 0
    runtime_flagged_total: int = 0
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    semantic_truth_source: str = "truth"
    semantic_truth_total: int = 0
    semantic_true_positive: int = 0
    semantic_false_positive: int = 0
    semantic_false_negative: int = 0
    semantic_precision: float = 0.0
    semantic_recall: float = 0.0
    semantic_f1: float = 0.0
    semantic_status: str = "skipped"
    """Статус выполнения семантического судьи.

    Значения:
        - ``"computed"``: судья отработал успешно, метрики ``semantic_*`` валидны;
        - ``"failed"``: судья был вызван, но упал с исключением — значения ``semantic_*``
          остаются плейсхолдерами и не должны интерпретироваться как реальные;
        - ``"skipped"``: судья не запускался (например, отсутствует truth-список).
    """
    semantic_failure_reason: str = ""
    """Свободно-формулированная причина падения, заполняется при ``semantic_status="failed"``."""
    case_truth_source: str = "truth"
    case_truth_total: int = 0
    case_true_positive: int = 0
    case_false_positive: int = 0
    case_false_negative: int = 0
    case_precision: float = 0.0
    case_recall: float = 0.0
    case_f1: float = 0.0
    by_violation_type: dict[str, dict[str, int]] = Field(default_factory=dict)


def evaluate_run(*, events_path: Path, truth_path: Path, truth_freeform_path: Path | None = None) -> EvaluationSummary:
    """Сравнить audit_flagged из events.jsonl с deterministic и optional freeform truth."""
    truth_records = [item for item in _iter_jsonl(truth_path) if isinstance(item, dict)]
    freeform_truth_records = [item for item in _iter_jsonl(truth_freeform_path) if isinstance(item, dict)] if truth_freeform_path else []
    event_records = [item for item in _iter_jsonl(events_path) if isinstance(item, dict)]

    truth_entries = [_truth_strict_entry(item) for item in truth_records]
    truth_entries = [item for item in truth_entries if item is not None]
    signal_entries = [_signal_strict_entry(item) for item in event_records if str(item.get("event_type") or "") == "audit_flagged"]
    signal_entries = [item for item in signal_entries if item is not None]
    by_violation: dict[str, dict[str, int]] = {}

    for item in truth_entries:
        violation_type = str(item["violation_type"])
        stats = by_violation.setdefault(violation_type, {"truth": 0, "signals": 0, "tp": 0, "fp": 0, "fn": 0})
        stats["truth"] += 1

    for item in signal_entries:
        violation_type = str(item["violation_type"])
        stats = by_violation.setdefault(violation_type, {"truth": 0, "signals": 0, "tp": 0, "fp": 0, "fn": 0})
        stats["signals"] += 1

    matched_pairs = _strict_match(truth_entries=truth_entries, signal_entries=signal_entries)
    matched_truth = {truth_idx for truth_idx, _ in matched_pairs}
    matched_signal = {signal_idx for _, signal_idx in matched_pairs}

    semantic_truth_records = freeform_truth_records if freeform_truth_records else truth_records
    semantic_truth_source = "truth_freeform" if freeform_truth_records else "truth"

    truth_findings = [_truth_finding(item) for item in semantic_truth_records]
    truth_findings = [item for item in truth_findings if item is not None]
    signal_findings = [_signal_finding(item) for item in event_records if str(item.get("event_type") or "") == "audit_flagged"]
    signal_findings = [item for item in signal_findings if item is not None]
    truth_cases = _build_case_items(truth_findings)
    signal_cases = _build_case_items(signal_findings)
    truth_cases_total = len(truth_cases)
    signal_cases_total = len(signal_cases)

    for truth_idx, signal_idx in matched_pairs:
        violation_type = str(truth_entries[truth_idx]["violation_type"])
        by_violation.setdefault(violation_type, {"truth": 0, "signals": 0, "tp": 0, "fp": 0, "fn": 0})["tp"] += 1
    for idx, item in enumerate(signal_entries):
        if idx in matched_signal:
            continue
        violation_type = str(item["violation_type"])
        by_violation.setdefault(violation_type, {"truth": 0, "signals": 0, "tp": 0, "fp": 0, "fn": 0})["fp"] += 1
    for idx, item in enumerate(truth_entries):
        if idx in matched_truth:
            continue
        violation_type = str(item["violation_type"])
        by_violation.setdefault(violation_type, {"truth": 0, "signals": 0, "tp": 0, "fp": 0, "fn": 0})["fn"] += 1

    precision = (len(matched_pairs) / len(signal_entries)) if signal_entries else 0.0
    recall = (len(matched_pairs) / len(truth_entries)) if truth_entries else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return EvaluationSummary(
        truth_total=len(truth_entries),
        freeform_truth_total=len(freeform_truth_records),
        runtime_flagged_total=len(signal_entries),
        true_positive=len(matched_pairs),
        false_positive=len(signal_entries) - len(matched_pairs),
        false_negative=len(truth_entries) - len(matched_pairs),
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        semantic_truth_source=semantic_truth_source,
        semantic_truth_total=len(truth_findings),
        semantic_true_positive=0,
        semantic_false_positive=len(signal_findings),
        semantic_false_negative=len(truth_findings),
        semantic_precision=0.0,
        semantic_recall=0.0,
        semantic_f1=0.0,
        case_truth_source=semantic_truth_source,
        case_truth_total=truth_cases_total,
        case_true_positive=0,
        case_false_positive=signal_cases_total,
        case_false_negative=truth_cases_total,
        case_precision=0.0,
        case_recall=0.0,
        case_f1=0.0,
        by_violation_type=by_violation,
    )


def _evaluation_semantic_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "semantic_matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "truth_index": {"type": "integer"},
                        "signal_index": {"type": "integer"},
                    },
                    "required": ["truth_index", "signal_index"],
                },
            },
            "case_matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "truth_case_index": {"type": "integer"},
                        "signal_case_index": {"type": "integer"},
                    },
                    "required": ["truth_case_index", "signal_case_index"],
                },
            },
        },
        "required": ["semantic_matches", "case_matches"],
    }


async def augment_evaluation_with_semantic_judge(
    *,
    summary: EvaluationSummary,
    llm: LLMCaller,
    events_path: Path,
    truth_path: Path,
    truth_freeform_path: Path | None,
    scenario_description: str,
    temperature: float = 0.0,
) -> EvaluationSummary:
    """Дополнить strict evaluation отдельным semantic/case judge pass."""

    truth_records = [item for item in _iter_jsonl(truth_path) if isinstance(item, dict)]
    freeform_truth_records = [item for item in _iter_jsonl(truth_freeform_path) if isinstance(item, dict)] if truth_freeform_path else []
    event_records = [item for item in _iter_jsonl(events_path) if isinstance(item, dict)]

    semantic_truth_records = freeform_truth_records if freeform_truth_records else truth_records
    truth_findings = [_truth_finding(item) for item in semantic_truth_records]
    truth_findings = [item for item in truth_findings if item is not None]
    signal_findings = [_signal_finding(item) for item in event_records if str(item.get("event_type") or "") == "audit_flagged"]
    signal_findings = [item for item in signal_findings if item is not None]
    truth_cases = _build_case_items(truth_findings)
    signal_cases = _build_case_items(signal_findings)

    if not truth_findings and not truth_cases:
        return summary

    payload = {
        "scenario_description": scenario_description,
        "strict_metrics": summary.model_dump(mode="json"),
        "truth_findings": truth_findings,
        "signal_findings": signal_findings,
        "truth_cases": truth_cases,
        "signal_cases": signal_cases,
    }
    try:
        resp = await llm.generate_structured(
            role="evaluation",
            name="semantic_match",
            tick=int(summary.truth_total + summary.runtime_flagged_total),
            system=render_prompt("evaluation.semantic_match.system"),
            user=render_prompt(
                "evaluation.semantic_match.user",
                payload_json=json.dumps(payload, ensure_ascii=False),
            ),
            schema=_evaluation_semantic_schema(),
            temperature=temperature,
        )
    except Exception as exc:
        reason = _classify_semantic_judge_error(exc)
        logger.warning(
            "semantic judge failed: %s — %s (%s); truth_findings=%d signal_findings=%d",
            type(exc).__name__,
            exc,
            reason,
            len(truth_findings),
            len(signal_findings),
        )
        return summary.model_copy(
            update={
                "semantic_status": "failed",
                "semantic_failure_reason": f"{type(exc).__name__}: {reason}",
            }
        )

    data = resp.data if isinstance(resp.data, dict) else {}
    semantic_matches = _normalize_index_matches(
        items=data.get("semantic_matches"),
        left_size=len(truth_findings),
        right_size=len(signal_findings),
        left_key="truth_index",
        right_key="signal_index",
    )
    case_matches = _normalize_index_matches(
        items=data.get("case_matches"),
        left_size=len(truth_cases),
        right_size=len(signal_cases),
        left_key="truth_case_index",
        right_key="signal_case_index",
    )

    semantic_tp = len(semantic_matches)
    semantic_fp = len(signal_findings) - semantic_tp
    semantic_fn = len(truth_findings) - semantic_tp
    case_tp = len(case_matches)
    case_fp = len(signal_cases) - case_tp
    case_fn = len(truth_cases) - case_tp
    semantic_precision = (semantic_tp / len(signal_findings)) if signal_findings else 0.0
    semantic_recall = (semantic_tp / len(truth_findings)) if truth_findings else 0.0
    case_precision = (case_tp / len(signal_cases)) if signal_cases else 0.0
    case_recall = (case_tp / len(truth_cases)) if truth_cases else 0.0

    return summary.model_copy(
        update={
            "semantic_true_positive": semantic_tp,
            "semantic_false_positive": semantic_fp,
            "semantic_false_negative": semantic_fn,
            "semantic_precision": round(semantic_precision, 4),
            "semantic_recall": round(semantic_recall, 4),
            "semantic_f1": round(_f1(semantic_precision, semantic_recall), 4),
            "semantic_status": "computed",
            "semantic_failure_reason": "",
            "case_true_positive": case_tp,
            "case_false_positive": case_fp,
            "case_false_negative": case_fn,
            "case_precision": round(case_precision, 4),
            "case_recall": round(case_recall, 4),
            "case_f1": round(_f1(case_precision, case_recall), 4),
        }
    )


def save_evaluation(summary: EvaluationSummary, path: Path) -> None:
    """Сохранить evaluation summary в JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
    return out


def _truth_key(item: dict[str, Any]) -> tuple[int, str, str, str | None, str] | None:
    tick = int(item.get("tick", 0))
    subject = str(item.get("subject_agent_id") or "")
    violation_type = _normalize_violation_type(item.get("violation_type"))
    if not subject or not violation_type:
        return None
    target = _normalize_target(item.get("target_agent_id")) or _normalize_target(item.get("beneficiary"))
    if target is None and violation_type.startswith("self_"):
        target = subject
    evidence_refs = _extract_evidence_refs(item.get("evidence_refs"))
    return (tick, subject, violation_type, target, _evidence_signature(evidence_refs))


def _signal_key(item: dict[str, Any]) -> tuple[int, str, str, str | None, str] | None:
    payload = item.get("payload", {}) or {}
    if not isinstance(payload, dict):
        return None

    tick = int(item.get("tick", 0))
    subject = str(payload.get("subject_agent_id") or payload.get("target_agent_id") or "")
    violation_type = _normalize_violation_type(payload.get("violation_type"))
    if not subject or not violation_type:
        return None

    evidence_refs = _extract_evidence_refs(payload.get("evidence_refs"))
    target = _normalize_target(
        payload.get("counterparty_agent_id")
        or payload.get("related_target_agent_id")
        or payload.get("target_agent_id")
        or payload.get("beneficiary")
        or _first_evidence_target_agent_id(evidence_refs)
    )
    return (tick, subject, violation_type, target, _evidence_signature(evidence_refs))


def _truth_strict_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    key = _truth_key(item)
    if key is None:
        return None
    tick, subject, violation_type, target, _ = key
    evidence_refs = _extract_evidence_refs(item.get("evidence_refs"))
    return {
        "tick": tick,
        "subject": subject,
        "violation_type": violation_type,
        "target": target,
        "evidence_exact": {_evidence_ref_signature(ref, include_timestamp=True) for ref in evidence_refs if isinstance(ref, dict)},
        "evidence_relaxed": {_evidence_ref_signature(ref, include_timestamp=False) for ref in evidence_refs if isinstance(ref, dict)},
    }


def _signal_strict_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    key = _signal_key(item)
    if key is None:
        return None
    tick, subject, violation_type, target, _ = key
    payload = item.get("payload", {}) or {}
    evidence_refs = _extract_evidence_refs(payload.get("evidence_refs"))
    return {
        "tick": tick,
        "subject": subject,
        "violation_type": violation_type,
        "target": target,
        "evidence_exact": {_evidence_ref_signature(ref, include_timestamp=True) for ref in evidence_refs if isinstance(ref, dict)},
        "evidence_relaxed": {_evidence_ref_signature(ref, include_timestamp=False) for ref in evidence_refs if isinstance(ref, dict)},
    }


def _normalize_violation_type(value: Any) -> str:
    return str(value or "").strip()


def _extract_evidence_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _first_evidence_target_agent_id(evidence_refs: list[dict[str, Any]]) -> str | None:
    for ref in evidence_refs:
        for key in ("counterparty_agent_id", "related_target_agent_id", "target_agent_id", "to_id"):
            target = _normalize_target(ref.get(key))
            if target and target.startswith("agent:"):
                return target
    return None


def _normalize_target(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _evidence_signature(evidence_refs: list[dict[str, Any]]) -> str:
    signatures = sorted(
        {
            _evidence_ref_signature(item)
            for item in evidence_refs
            if isinstance(item, dict)
        }
    )
    try:
        return json.dumps(signatures, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return repr(signatures)


def _truth_finding(item: dict[str, Any]) -> dict[str, Any] | None:
    subject = str(item.get("subject_agent_id") or "").strip()
    if not subject:
        return None
    return {
        "tick": int(item.get("tick", 0)),
        "subject": subject,
        "target": _normalize_target(item.get("target_agent_id")),
        "beneficiary": _normalize_target(item.get("beneficiary")),
        "violation_type": _normalize_violation_type(item.get("violation_type") or item.get("violation_type_freeform")),
        "risk_tags": sorted({str(tag).strip().casefold() for tag in list(item.get("risk_tags") or []) if str(tag).strip()}),
        "summary": str(item.get("summary") or item.get("rationale") or ""),
        "mechanism": str(item.get("mechanism") or ""),
        "evidence_refs": _extract_evidence_refs(item.get("evidence_refs")),
    }


def _signal_finding(item: dict[str, Any]) -> dict[str, Any] | None:
    payload = item.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    subject = str(payload.get("subject_agent_id") or payload.get("target_agent_id") or "").strip()
    if not subject:
        return None
    return {
        "tick": int(item.get("tick", 0)),
        "subject": subject,
        "target": _normalize_target(
            payload.get("counterparty_agent_id")
            or payload.get("related_target_agent_id")
            or payload.get("target_agent_id")
            or _first_evidence_target_agent_id(_extract_evidence_refs(payload.get("evidence_refs")))
        ),
        "beneficiary": _normalize_target(payload.get("beneficiary")),
        "violation_type": _normalize_violation_type(payload.get("violation_type")),
        "risk_tags": sorted({str(tag).strip().casefold() for tag in list(payload.get("risk_tags") or []) if str(tag).strip()}),
        "summary": str(payload.get("summary") or ""),
        "mechanism": str(payload.get("mechanism") or ""),
        "evidence_refs": _extract_evidence_refs(payload.get("evidence_refs")),
    }


def _build_case_items(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for finding in findings:
        item = {
            "subject": str(finding.get("subject") or "").strip(),
            "target": _normalize_target(finding.get("target")),
            "beneficiary": _normalize_target(finding.get("beneficiary")),
            "violation_type": _normalize_violation_type(finding.get("violation_type")),
            "risk_tags": sorted(str(tag).strip().casefold() for tag in set(finding.get("risk_tags") or set()) if str(tag).strip()),
            "summary": str(finding.get("summary") or "").strip(),
            "mechanism": str(finding.get("mechanism") or "").strip(),
            "evidence_refs": _extract_evidence_refs(finding.get("evidence_refs")),
        }
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _normalize_index_matches(
    *,
    items: Any,
    left_size: int,
    right_size: int,
    left_key: str,
    right_key: str,
) -> list[tuple[int, int]]:
    """Принимает совпадения по ключам ``_index`` или их алиасам ``_idx``.

    LLM-судьи на разных провайдерах возвращают то ``truth_index``/``signal_index``,
    то их сокращённые варианты ``truth_idx``/``signal_idx``. Без алиасинга весь
    результат тихо отбрасывается, что обнулит semantic-метрики.
    """
    if not isinstance(items, list):
        return []
    left_aliases = (left_key, left_key.replace("_index", "_idx"))
    right_aliases = (right_key, right_key.replace("_index", "_idx"))

    def _pick(item: dict[str, Any], aliases: tuple[str, ...]) -> Any:
        for key in aliases:
            if key in item:
                return item.get(key)
        return None

    out: list[tuple[int, int]] = []
    used_left: set[int] = set()
    used_right: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            left_idx = int(_pick(item, left_aliases))
            right_idx = int(_pick(item, right_aliases))
        except (TypeError, ValueError):
            continue
        if not (0 <= left_idx < left_size and 0 <= right_idx < right_size):
            continue
        if left_idx in used_left or right_idx in used_right:
            continue
        used_left.add(left_idx)
        used_right.add(right_idx)
        out.append((left_idx, right_idx))
    return out


_STRICT_TICK_WINDOW = 3


def _strict_match(
    *,
    truth_entries: list[dict[str, Any]],
    signal_entries: list[dict[str, Any]],
    tick_window: int = _STRICT_TICK_WINDOW,
) -> list[tuple[int, int]]:
    candidates: list[tuple[float, int, int]] = []
    for truth_idx, truth in enumerate(truth_entries):
        for signal_idx, signal in enumerate(signal_entries):
            score = _strict_match_score(truth=truth, signal=signal, tick_window=tick_window)
            if score > 0.0:
                candidates.append((score, truth_idx, signal_idx))
    candidates.sort(reverse=True)
    matches: list[tuple[int, int]] = []
    matched_truth: set[int] = set()
    matched_signal: set[int] = set()
    for _, truth_idx, signal_idx in candidates:
        if truth_idx in matched_truth or signal_idx in matched_signal:
            continue
        matched_truth.add(truth_idx)
        matched_signal.add(signal_idx)
        matches.append((truth_idx, signal_idx))
    return matches


def _strict_match_score(
    *,
    truth: dict[str, Any],
    signal: dict[str, Any],
    tick_window: int = _STRICT_TICK_WINDOW,
) -> float:
    truth_tick = int(truth["tick"])
    signal_tick = int(signal["tick"])
    tick_diff = abs(truth_tick - signal_tick)
    if tick_diff > max(0, int(tick_window)):
        return 0.0
    tick_penalty = 0.0
    if tick_diff > 0:
        # За каждый тик расхождения снимаем небольшую долю — точное попадание
        # ценнее «попадание в окне», но обоих признаём как валидные совпадения.
        tick_penalty = min(0.2, 0.05 * tick_diff)
    if str(truth["subject"]) != str(signal["subject"]):
        return 0.0
    if not _violation_type_match(truth.get("violation_type"), signal.get("violation_type")):
        return 0.0
    if not _compatible_target(truth.get("target"), signal.get("target")):
        return 0.0

    truth_exact = set(truth.get("evidence_exact") or set())
    signal_exact = set(signal.get("evidence_exact") or set())
    truth_relaxed = set(truth.get("evidence_relaxed") or set())
    signal_relaxed = set(signal.get("evidence_relaxed") or set())
    if truth_exact and signal_exact and truth_exact == signal_exact:
        return max(0.0, 1.0 - tick_penalty)
    if truth_relaxed and signal_relaxed and truth_relaxed == signal_relaxed:
        return max(0.0, 0.8 - tick_penalty)
    if not truth_relaxed and not signal_relaxed:
        return max(0.0, 0.7 - tick_penalty)
    # Truth without evidence refs (e.g. state-based detect_contact_patterns):
    # match on header fields alone — the TruthRecord has no specific event anchors.
    if not truth_relaxed:
        return max(0.0, 0.6 - tick_penalty)
    return 0.0


def _compatible_target(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left == right

def _evidence_ref_signature(item: dict[str, Any], *, include_timestamp: bool = True) -> str:
    keys = {
        "tick": item.get("tick"),
        "event_type": item.get("event_type"),
        "actor_id": item.get("actor_id"),
        "target_agent_id": item.get("target_agent_id"),
        "counterparty_agent_id": item.get("counterparty_agent_id"),
        "to_id": item.get("to_id"),
        "vote_id": item.get("vote_id"),
        "work_id": item.get("work_id"),
        "case_id": item.get("case_id"),
    }
    if include_timestamp:
        keys["timestamp"] = item.get("timestamp")
    return json.dumps(keys, ensure_ascii=False, sort_keys=True)


_VIOLATION_TYPE_SYNONYMS: dict[str, set[str]] = {
    # LLM-аудитор v3 фактически использует ``preferential_treatment`` и ``other``
    # как описание тех же эпизодов, что детерминированный truth-детектор
    # маркирует как ``conflict_of_interest``. Анализ events.jsonl в прогоне
    # ``overnight_g3_v3_deepseek`` показал, что 21 finding с типом ``other``
    # описывает приватные контакты, координацию замолчания, неформальные
    # встречи в обход формального процесса — это семейство «конфликт интересов /
    # преференциальное обращение». Без словаря синонимов 30 из 47 finding'ов
    # не сопоставлялись с эталоном, обнуляя precision и recall.
    "conflict_of_interest": {
        "conflict_of_interest",
        "preferential_treatment",
        "other",
    },
}


def _build_violation_type_index(
    groups: dict[str, set[str]],
) -> dict[str, frozenset[str]]:
    """Свернуть словарь групп синонимов в индекс «значение → группа».

    Для каждого значения в любой группе сопоставляется замороженное множество
    всех её членов. Это даёт симметричный и транзитивный матчер: если ``a`` и
    ``b`` принадлежат одной группе, то и ``b`` и ``a`` тоже, и любая третья
    точка ``c`` из той же группы матчится с ``a`` и ``b``.

    Args:
        groups: Словарь, где ключ — каноническое имя группы (для удобства
            интроспекции), а значение — множество синонимов.

    Returns:
        Индекс «значение в нижнем регистре без пробелов → frozenset группы».
    """
    index: dict[str, frozenset[str]] = {}
    for group in groups.values():
        normalized = frozenset(item.lower().strip() for item in group)
        for member in normalized:
            index[member] = normalized
    return index


_VIOLATION_TYPE_INDEX: dict[str, frozenset[str]] = _build_violation_type_index(
    _VIOLATION_TYPE_SYNONYMS
)


def _violation_type_match(left: Any, right: Any) -> bool:
    """Сравнить два значения ``violation_type`` с учётом словаря синонимов.

    Ранее функция требовала точного совпадения строк, и LLM-аудитор с типами
    ``preferential_treatment``/``other`` не сопоставлялся с truth-детектором,
    выдающим только ``conflict_of_interest``. Теперь мы признаём пару валидной,
    если оба значения принадлежат одной группе синонимов в
    ``_VIOLATION_TYPE_SYNONYMS``. Сравнение нечувствительно к регистру и
    окружающим пробелам.

    Args:
        left: Левое значение (например, из truth-записи).
        right: Правое значение (например, из payload события audit_flagged).

    Returns:
        ``True``, если значения совпадают точно либо принадлежат одной группе
        синонимов; иначе — ``False``. Пустые/``None``-значения никогда не
        матчатся.
    """
    left_norm = _normalize_violation_type(left).lower().strip()
    right_norm = _normalize_violation_type(right).lower().strip()
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    left_group = _VIOLATION_TYPE_INDEX.get(left_norm)
    right_group = _VIOLATION_TYPE_INDEX.get(right_norm)
    if left_group is None or right_group is None:
        return False
    return left_group is right_group


def _classify_semantic_judge_error(exc: Exception) -> str:
    """Сгенерировать человекочитаемую подсказку о причине падения LLM-судьи.

    Анализирует текст исключения и выделяет три класса распространённых
    ошибок: исчерпание баланса OpenRouter (HTTP 402), переполнение контекстного
    окна модели и прочие сбои. Подсказка попадает в ``semantic_failure_reason``
    и в WARNING-лог, чтобы аналитик мог быстро понять, нужно ли пополнять
    баланс, сжимать промпт или искать иную причину.

    Args:
        exc: Исключение, перехваченное в обёртке судьи.

    Returns:
        Короткая фраза, описывающая категорию ошибки.
    """
    text = str(exc).lower()
    if "402" in text or "insufficient credits" in text or "requires more credits" in text:
        return "OpenRouter insufficient credits"
    if "context length" in text or "context_length" in text or "maximum context" in text:
        return "prompt too long"
    return "generic LLM failure"


def _f1(precision: float, recall: float) -> float:
    if precision + recall <= 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)
