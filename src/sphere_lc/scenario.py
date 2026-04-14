"""Загрузка/сохранение сценариев SPHERE-LC (YAML/JSON)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ScenarioConfig


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise ImportError(
            'Для YAML-сценариев установите зависимости: pip install -e ".[lc]"'
        ) from exc

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Scenario YAML must be a mapping/object")
    return data


def _dump_yaml(data: dict[str, Any]) -> str:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise ImportError(
            'Для YAML-сценариев установите зависимости: pip install -e ".[lc]"'
        ) from exc

    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def load_scenario(path: str | Path) -> ScenarioConfig:
    """Загрузить сценарий из YAML/JSON."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    if p.suffix.lower() in (".yaml", ".yml"):
        data = _load_yaml(p)
    else:
        data = json.loads(p.read_text(encoding="utf-8"))
    return ScenarioConfig.model_validate(data)


def save_scenario(cfg: ScenarioConfig, path: str | Path) -> None:
    """Сохранить сценарий в YAML/JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump(mode="json")
    if p.suffix.lower() in (".yaml", ".yml"):
        p.write_text(_dump_yaml(data), encoding="utf-8")
    else:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

