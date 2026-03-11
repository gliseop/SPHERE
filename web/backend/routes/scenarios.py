"""CRUD-маршруты для пользовательских сценариев /api/scenarios."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from magistry_lc.config import AgentConfig, ChannelConfig, GovernanceConfig, ScenarioConfig
from magistry_lc.ids import EntityKind, make_id, normalize_slug
from magistry_lc.persona import PersonaArtifact
from magistry_lc.scenario import save_scenario

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.models import ScenarioPayload
from web.backend.settings import (
    BUILTIN_GOVERNANCE_IDS,
    GOVERNANCE_MODES_DIR,
    PERSONALITIES_DIR,
    SCENARIOS_DIR,
)
from web.backend.validators import (
    S_NUM_RE,
    next_s_number,
    validate_library_id,
    validate_scenario_id,
)

router = APIRouter(tags=["scenarios"])
_ALLOCATE_ID_ATTEMPTS = 256
_SCENARIO_SUFFIXES = (".json", ".yaml", ".yml")
_DEFAULT_SCENARIO_TEMPLATE = "S1"
_DEFAULT_GOVERNANCE_MODE = "G1"
_DEFAULT_PUBLIC_CHANNEL = {"channel_id": "chan:public", "title": "Публичный канал"}
_LEGACY_TEMPLATE_FILE_MAP = {
    "S0": "seed_s0_g0.json",
    "S1": "seed_s1_g1.json",
    "S2": "seed_s2_g2.json",
}
_BUILTIN_SCENARIO_FILES = frozenset(_LEGACY_TEMPLATE_FILE_MAP.values())
_GOVERNANCE_METADATA_KEYS = frozenset({"id", "label", "description", "custom"})
_UNSET = object()
_KNOWN_ROLE_ALIASES = {
    "auditor",
    "audit",
    "aud",
    "аудитор",
    "business",
    "contractor",
    "vendor",
    "external",
    "biz",
    "подрядчик",
    "контрагент",
    "внешний",
    "juror",
    "jury",
    "jur",
    "присяжный",
    "official",
    "official_procurement",
    "off",
    "employee",
    "staff",
    "officials",
    "чиновник",
    "сотрудник",
}


def _load_personality_record(personality_id: str) -> dict[str, Any] | None:
    path = PERSONALITIES_DIR / f"{personality_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _persona_from_personality_id(personality_id: str) -> PersonaArtifact | None:
    raw = _load_personality_record(personality_id)
    if raw is None:
        return None
    return PersonaArtifact(
        persona_id=personality_id,
        summary=str(raw.get("description") or raw.get("name") or personality_id),
        biography=str(raw.get("biography") or ""),
    )


def _role_defaults(role: str) -> tuple[bool, list[str], bool, str]:
    normalized = (role or "").strip().casefold()
    if normalized in {"auditor", "audit", "aud", "аудитор"}:
        return True, ["audit", "message", "work", "dao"], False, "аудитор"
    if normalized in {
        "business",
        "contractor",
        "vendor",
        "external",
        "biz",
        "подрядчик",
        "контрагент",
        "внешний",
    }:
        return False, ["message"], False, "контрагент"
    if normalized in {"juror", "jury", "jur", "присяжный"}:
        return True, ["dao"], False, "присяжный"
    if normalized in {
        "official",
        "official_procurement",
        "off",
        "employee",
        "staff",
        "officials",
        "чиновник",
        "сотрудник",
    }:
        return True, ["message", "work", "dao"], True, "специалист"
    return True, ["message", "work"], True, (role or "специалист").strip() or "специалист"


def _normalize_capabilities(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _load_governance_mode_record(mode_id: str) -> dict[str, Any] | None:
    validate_library_id(mode_id, GOVERNANCE_MODES_DIR, kind="governance-mode")
    path = GOVERNANCE_MODES_DIR / f"{mode_id}.json"
    raw = _read_mapping(path)
    if raw is None:
        return None
    payload = dict(raw)
    payload["id"] = str(payload.get("id") or mode_id).strip() or mode_id
    return payload


def _extract_governance_config_payload(raw: dict[str, Any], *, mode_id: str) -> GovernanceConfig:
    candidate = raw.get("config")
    if isinstance(candidate, dict):
        config_data = candidate
    else:
        config_data = {
            key: value
            for key, value in raw.items()
            if key not in _GOVERNANCE_METADATA_KEYS
        }
    if not config_data:
        raise HTTPException(
            status_code=400,
            detail=f"Governance mode {mode_id} has no config payload",
        )
    try:
        return GovernanceConfig.model_validate(config_data)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid governance mode {mode_id}: {exc}",
        ) from exc


def _infer_ui_role(agent: AgentConfig) -> str:
    caps = set(agent.capabilities)
    if "audit" in caps:
        return "auditor"
    if not agent.internal:
        return "business"
    if caps == {"dao"}:
        return "juror"
    if "work" in caps or "dao" in caps:
        return "official"
    return "official"


def _ensure_default_public_channel(cfg: ScenarioConfig) -> None:
    if any(channel.channel_id == _DEFAULT_PUBLIC_CHANNEL["channel_id"] for channel in cfg.world.channels):
        return
    cfg.world.channels.insert(0, ChannelConfig.model_validate(_DEFAULT_PUBLIC_CHANNEL))


def _apply_builtin_governance_mode(cfg: ScenarioConfig, normalized: str) -> None:
    audit = cfg.governance.audit
    audit.mode = "rules"
    if normalized == "G0":
        audit.enabled = False
        audit.reputation_freeze_enabled = False
        audit.reputation_penalty_delta = None
    elif normalized == "G1":
        audit.enabled = True
        audit.reputation_freeze_enabled = False
        audit.reputation_penalty_delta = None
    elif normalized == "G2":
        audit.enabled = True
        audit.reputation_freeze_enabled = True
        audit.reputation_penalty_delta = None
    else:
        audit.enabled = True
        audit.reputation_freeze_enabled = True
        audit.reputation_penalty_delta = (
            float(audit.reputation_penalty_delta)
            if audit.reputation_penalty_delta is not None
            else -0.75
        )
    cfg.governance.audit = audit


def _apply_governance_mode(cfg: ScenarioConfig, governance_mode: str | None) -> None:
    normalized = (governance_mode or "").strip().upper()
    if not normalized:
        return
    if normalized in BUILTIN_GOVERNANCE_IDS:
        _apply_builtin_governance_mode(cfg, normalized)
        return

    raw = _load_governance_mode_record(normalized)
    if raw is None:
        raise HTTPException(status_code=400, detail=f"Governance mode not found: {normalized}")
    cfg.governance = _extract_governance_config_payload(raw, mode_id=normalized)


def _build_agent_config(
    agent_data: dict[str, Any],
    *,
    index: int,
    existing: AgentConfig | None,
) -> AgentConfig:
    raw_id = str(agent_data.get("id") or "").strip()
    name = str(agent_data.get("name") or raw_id or f"Агент {index}").strip() or f"Агент {index}"
    role = str(agent_data.get("role") or "").strip()
    normalized_role = role.casefold()
    position = str(agent_data.get("position") or "").strip()
    personality_id = str(agent_data.get("personality_archetype") or "").strip()
    role_is_known = normalized_role in _KNOWN_ROLE_ALIASES
    raw_caps = agent_data.get("capabilities", _UNSET)
    explicit_capabilities = (
        _normalize_capabilities(raw_caps) if raw_caps is not _UNSET else None
    )
    raw_initial_reputation = agent_data.get("initial_reputation", _UNSET)

    inferred_internal, inferred_caps, inferred_wants, inferred_title = _role_defaults(role)
    if existing is not None:
        agent_id = existing.agent_id
        internal = inferred_internal if role_is_known else existing.internal
        if explicit_capabilities is not None:
            capabilities = explicit_capabilities
        else:
            capabilities = (
                inferred_caps if role_is_known else (list(existing.capabilities) or inferred_caps)
            )
        wants_promotion = inferred_wants if role_is_known else existing.wants_promotion
        initial_title = position or (inferred_title if role_is_known else existing.initial_title) or inferred_title
        if raw_initial_reputation is _UNSET:
            initial_reputation = float(existing.initial_reputation)
        else:
            initial_reputation = float(raw_initial_reputation)
        persona = existing.persona.model_copy(deep=True)
    else:
        slug = normalize_slug(raw_id or name, fallback=f"agent_{index}")
        agent_id = make_id(EntityKind.AGENT, slug)
        internal = inferred_internal
        capabilities = explicit_capabilities if explicit_capabilities is not None else inferred_caps
        wants_promotion = inferred_wants
        initial_title = position or inferred_title
        initial_reputation = 0.0 if raw_initial_reputation is _UNSET else float(raw_initial_reputation)
        persona = PersonaArtifact(summary=(position or role or name).strip())

    if personality_id:
        library_persona = _persona_from_personality_id(personality_id)
        if library_persona is not None:
            persona = library_persona
        else:
            persona.persona_id = personality_id
    else:
        persona.persona_id = None

    return AgentConfig.model_validate(
        {
            "agent_id": agent_id,
            "name": name,
            "internal": internal,
            "persona": persona.model_dump(mode="json"),
            "capabilities": capabilities,
            "initial_reputation": initial_reputation,
            "initial_title": initial_title,
            "wants_promotion": wants_promotion,
        }
    )


def _sync_config_from_payload(
    cfg: ScenarioConfig,
    *,
    payload: ScenarioPayload,
    scenario_id: str,
    narrative_context: str = "",
    preserve_existing_agents_when_empty: bool = False,
) -> ScenarioConfig:
    cfg.title = payload.name.strip() or scenario_id
    cfg.description = (payload.description or narrative_context or "").strip()
    cfg.ticks = int(payload.rounds)
    if payload.seed is not None:
        cfg.seed = int(payload.seed)
    if "parallel_agents" in payload.model_fields_set:
        cfg.runtime.parallel_agents = bool(payload.parallel_agents)
    if "parallel_workers" in payload.model_fields_set:
        cfg.runtime.parallel_workers = (
            int(payload.parallel_workers) if payload.parallel_workers is not None else None
        )
    if "parallel_window" in payload.model_fields_set:
        cfg.runtime.parallel_window_seconds = (
            float(payload.parallel_window) if payload.parallel_window is not None else None
        )

    _apply_governance_mode(cfg, payload.governance)

    if payload.agents or not preserve_existing_agents_when_empty:
        existing_by_ui_id = {
            _to_ui_agent_id(agent.agent_id, fallback=f"agent_{index}"): agent
            for index, agent in enumerate(cfg.agents, start=1)
        }
        cfg.agents = [
            _build_agent_config(
                agent_data=agent_data.model_dump(mode="json", exclude_none=True),
                index=index,
                existing=existing_by_ui_id.get(agent_data.id),
            )
            for index, agent_data in enumerate(payload.agents, start=1)
        ]
    _ensure_default_public_channel(cfg)
    return cfg


def _legacy_payload_to_config(
    raw: dict[str, Any],
    *,
    scenario_id: str,
    seed_from_template: bool,
) -> ScenarioConfig:
    payload = ScenarioPayload.model_validate(raw)
    base_cfg: ScenarioConfig | None = None
    sim_config = raw.get("sim_config")
    if isinstance(sim_config, dict):
        try:
            base_cfg = ScenarioConfig.model_validate(sim_config)
        except Exception:
            base_cfg = None
    if base_cfg is None and seed_from_template:
        try:
            base_cfg = load_template_config_for_web(
                payload.scenario,
                governance=payload.governance,
            )
        except HTTPException:
            base_cfg = None
    cfg = base_cfg or ScenarioConfig()
    return _sync_config_from_payload(
        cfg,
        payload=payload,
        scenario_id=scenario_id,
        narrative_context=str(raw.get("narrative_context") or ""),
        preserve_existing_agents_when_empty=base_cfg is not None,
    )


def load_scenario_config_for_web(path: Path, *, scenario_id: str | None = None) -> ScenarioConfig:
    raw = _read_mapping(path)
    if raw is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    resolved_id = scenario_id or path.stem
    try:
        return ScenarioConfig.model_validate(raw)
    except Exception:
        try:
            return _legacy_payload_to_config(
                raw,
                scenario_id=resolved_id,
                seed_from_template=not _is_builtin_scenario_path(path),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid scenario config: {exc}") from exc


def load_template_config_for_web(
    scenario_id: str,
    *,
    governance: str | None = None,
) -> ScenarioConfig:
    candidate_name = _LEGACY_TEMPLATE_FILE_MAP.get(scenario_id)
    if candidate_name is None:
        candidate_path = _resolve_scenario_path(scenario_id)
        if candidate_path is None:
            raise HTTPException(status_code=404, detail="Template scenario not found")
    else:
        candidate_path = SCENARIOS_DIR / candidate_name
        if not candidate_path.exists():
            raise HTTPException(status_code=404, detail="Template scenario not found")

    cfg = load_scenario_config_for_web(candidate_path, scenario_id=scenario_id)
    if governance:
        _apply_governance_mode(cfg, governance)
    return cfg


def _read_mapping(path: Path) -> dict[str, Any] | None:
    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            import yaml  # type: ignore

            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_mapping(path: Path, data: dict[str, Any]) -> None:
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_scenario_path(scenario_id: str) -> Path | None:
    base_dir = SCENARIOS_DIR.resolve()
    existing: list[Path] = []
    for suffix in _SCENARIO_SUFFIXES:
        candidate = SCENARIOS_DIR / f"{scenario_id}{suffix}"
        resolved = candidate.resolve()
        if not resolved.is_relative_to(base_dir):
            continue
        if resolved.exists():
            existing.append(resolved)
    if not existing:
        return None
    if len(existing) == 1:
        return existing[0]
    return max(existing, key=lambda path: path.stat().st_mtime)


def _is_builtin_scenario_path(path: Path) -> bool:
    return path.name in _BUILTIN_SCENARIO_FILES


def _list_scenario_paths() -> list[Path]:
    chosen: dict[str, Path] = {}
    for suffix in _SCENARIO_SUFFIXES:
        for path in SCENARIOS_DIR.glob(f"*{suffix}"):
            if _is_builtin_scenario_path(path):
                continue
            current = chosen.get(path.stem)
            if current is None:
                chosen[path.stem] = path
                continue
            try:
                if path.stat().st_mtime >= current.stat().st_mtime:
                    chosen[path.stem] = path
            except OSError:
                continue
    return sorted(chosen.values(), key=lambda path: path.stem)


def _infer_governance_mode(cfg: ScenarioConfig) -> str:
    current = cfg.governance.model_dump(mode="json")
    for path in sorted(GOVERNANCE_MODES_DIR.glob("G*.json")):
        raw = _read_mapping(path)
        if raw is None:
            continue
        mode_id = str(raw.get("id") or path.stem).strip().upper() or path.stem.upper()
        if mode_id in BUILTIN_GOVERNANCE_IDS:
            continue
        try:
            candidate = _extract_governance_config_payload(raw, mode_id=mode_id)
        except HTTPException:
            continue
        if candidate.model_dump(mode="json") == current:
            return mode_id

    audit = cfg.governance.audit
    if not audit.enabled:
        return "G0"
    if audit.reputation_penalty_delta is not None:
        return "G3"
    if audit.reputation_freeze_enabled:
        return "G2"
    return _DEFAULT_GOVERNANCE_MODE


def _to_ui_agent_id(agent_id: str, *, fallback: str) -> str:
    if ":" not in agent_id:
        return agent_id or fallback
    _, _, slug = agent_id.partition(":")
    return slug or fallback


def _scenario_config_to_payload(cfg: ScenarioConfig, *, scenario_id: str) -> dict[str, Any]:
    agents: list[dict[str, Any]] = []
    for index, agent in enumerate(cfg.agents, start=1):
        personality: dict[str, Any] | None = None
        if agent.persona.persona_id:
            personality = _load_personality_record(agent.persona.persona_id)
        if personality is None and (agent.persona.biography or agent.persona.summary):
            personality = {"biography": agent.persona.biography or agent.persona.summary}
        agents.append(
            {
                "id": _to_ui_agent_id(agent.agent_id, fallback=f"agent_{index}"),
                "name": agent.name,
                "role": _infer_ui_role(agent),
                "position": agent.initial_title,
                "initial_reputation": float(agent.initial_reputation),
                "personality_archetype": agent.persona.persona_id,
                "personality": personality,
                "capabilities": list(agent.capabilities),
            }
        )

    scenario_template = scenario_id if S_NUM_RE.fullmatch(scenario_id) else _DEFAULT_SCENARIO_TEMPLATE
    return {
        "id": scenario_id,
        "name": cfg.title or scenario_id,
        "description": cfg.description or "",
        "scenario": scenario_template,
        "governance": _infer_governance_mode(cfg),
        "rounds": int(cfg.ticks),
        "seed": int(cfg.seed),
        "runner": "cognitive",
        "parallel_agents": bool(cfg.runtime.parallel_agents),
        "parallel_workers": cfg.runtime.parallel_workers,
        "parallel_window": cfg.runtime.parallel_window_seconds,
        "agents": agents,
        "sim_config": cfg.model_dump(mode="json"),
    }


def _read_scenario_payload(path: Path) -> dict[str, Any] | None:
    raw = _read_mapping(path)
    if raw is None:
        return None

    try:
        cfg = load_scenario_config_for_web(path, scenario_id=path.stem)
    except HTTPException:
        payload = dict(raw)
        payload["id"] = str(payload.get("id") or path.stem)
        return payload

    return _scenario_config_to_payload(cfg, scenario_id=path.stem)


def _save_payload(path: Path, payload: ScenarioPayload, *, scenario_id: str) -> dict[str, Any]:
    if payload.sim_config is not None:
        try:
            cfg = ScenarioConfig.model_validate(payload.sim_config)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid ScenarioConfig: {exc}") from exc

        cfg = _sync_config_from_payload(cfg, payload=payload, scenario_id=scenario_id)
        save_scenario(cfg, path)
        return _scenario_config_to_payload(cfg, scenario_id=scenario_id)

    data = payload.model_dump(mode="json")
    data["id"] = scenario_id
    _write_mapping(path, data)
    return data


@router.get("/api/scenarios")
async def list_scenarios(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список сценариев.

    Args:
        _user: Аутентифицированный пользователь (любая роль).
    """
    result = []
    for path in _list_scenario_paths():
        data = _read_scenario_payload(path)
        if data is not None:
            result.append(data)
    return result


@router.get("/api/scenarios/{scenario_id}")
async def get_scenario(scenario_id: str, _user: User = Depends(require_viewer)) -> dict:
    """Вернуть сценарий по ID.

    Args:
        scenario_id: UUID строка.
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        Словарь с данными сценария.
    """
    validate_scenario_id(scenario_id)
    path = _resolve_scenario_path(scenario_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    data = _read_scenario_payload(path)
    if data is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return data


@router.post("/api/scenarios", status_code=201)
async def create_scenario(payload: ScenarioPayload, _user: User = Depends(require_admin)) -> dict:
    """Создать новый сценарий.

    Автоматически присваивает S-номер (S7, S8, ...) на основе
    существующих встроенных сценариев (S0-S6) и файлов в SCENARIOS_DIR.

    Args:
        payload: Данные сценария.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Сохранённый сценарий с назначенным id.
    """
    for _ in range(_ALLOCATE_ID_ATTEMPTS):
        scenario_id = next_s_number()
        suffix = ".yaml" if payload.sim_config is not None else ".json"
        path = SCENARIOS_DIR / f"{scenario_id}{suffix}"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write("")
            try:
                return _save_payload(path, payload, scenario_id=scenario_id)
            except Exception:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
                raise
        except FileExistsError:
            continue
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to write scenario: {exc}") from exc

    raise HTTPException(status_code=409, detail="Failed to allocate scenario ID")


@router.put("/api/scenarios/{scenario_id}")
async def update_scenario(
    scenario_id: str,
    payload: ScenarioPayload,
    _user: User = Depends(require_admin),
) -> dict:
    """Обновить сценарий.

    Args:
        scenario_id: UUID строка.
        payload: Новые данные сценария.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Обновлённый сценарий.
    """
    validate_scenario_id(scenario_id)
    path = _resolve_scenario_path(scenario_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if _is_builtin_scenario_path(path):
        raise HTTPException(status_code=403, detail="Cannot modify built-in template scenario")
    return _save_payload(path, payload, scenario_id=scenario_id)


@router.delete("/api/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(scenario_id: str, _user: User = Depends(require_admin)) -> None:
    """Удалить сценарий.

    Args:
        scenario_id: UUID строка.
        _user: Аутентифицированный пользователь с ролью admin.
    """
    validate_scenario_id(scenario_id)
    path = _resolve_scenario_path(scenario_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if _is_builtin_scenario_path(path):
        raise HTTPException(status_code=403, detail="Cannot delete built-in template scenario")
    path.unlink()
