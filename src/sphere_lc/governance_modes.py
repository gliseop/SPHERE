"""Canonical built-in governance-mode mapping for SPHERE-LC."""

from __future__ import annotations

from .config import ScenarioConfig


BUILTIN_GOVERNANCE_MODES: tuple[str, ...] = ("G0", "G1", "G2", "G3")


def apply_builtin_governance_mode(cfg: ScenarioConfig, mode_id: str) -> None:
    """Mutate ``cfg`` so its governance matches one of the built-in modes."""

    normalized = str(mode_id or "").strip().upper()
    if normalized not in BUILTIN_GOVERNANCE_MODES:
        raise ValueError(f"Unknown built-in governance mode: {mode_id!r}")

    audit = cfg.governance.audit
    audit.mode = "hybrid"
    audit.collegial_review_enabled = False
    audit.reputation_penalty_delta = None

    if normalized == "G0":
        audit.enabled = False
        audit.reputation_freeze_enabled = False
    elif normalized == "G1":
        audit.enabled = True
        audit.reputation_freeze_enabled = False
    elif normalized == "G2":
        audit.enabled = True
        audit.reputation_freeze_enabled = True
    else:
        audit.enabled = True
        audit.reputation_freeze_enabled = True
        audit.collegial_review_enabled = True

    cfg.governance.audit = audit


def infer_builtin_governance_mode(cfg: ScenarioConfig, *, default_mode: str = "G1") -> str:
    """Infer built-in governance mode from a normalized ``ScenarioConfig``."""

    audit = cfg.governance.audit
    if not audit.enabled:
        return "G0"
    if audit.collegial_review_enabled:
        return "G3"
    if audit.reputation_freeze_enabled:
        return "G2"
    return default_mode
