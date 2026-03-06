"""Утилиты поиска артефактов прогонов (legacy и directory-based)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

from .settings import RESULTS_DIR

RunFormat = Literal["legacy", "directory"]

_LEGACY_EVENTS_SUFFIX = "_events.jsonl"
_RUN_META_RE = re.compile(
    r"^(?P<scenario>S\d+)_(?P<governance>G\d+)"
    r"(?:_seed(?P<seed>\d+))?"
    r"(?:_(?P<variant>[A-Za-z0-9_\-]+))?$"
)


@dataclass(frozen=True, slots=True)
class RunArtifactRef:
    """Ссылка на events-файл прогона."""

    name: str
    events_path: Path
    format: RunFormat

    def mtime(self) -> float:
        """Получить mtime events-файла (0.0 при ошибке)."""
        try:
            return self.events_path.stat().st_mtime
        except OSError:
            return 0.0


def parse_run_name(run_name: str) -> dict:
    """Разобрать имя прогона в метаданные для UI."""
    m = _RUN_META_RE.match(run_name)
    if m:
        return {
            "scenario": m.group("scenario"),
            "governance": m.group("governance"),
            "seed": int(m.group("seed")) if m.group("seed") else None,
            "variant": m.group("variant") or None,
        }
    return {
        "scenario": run_name,
        "governance": "",
        "seed": None,
        "variant": None,
    }


def list_run_artifacts(*, results_dir: Path | None = None) -> list[RunArtifactRef]:
    """Вернуть все доступные прогоны в обоих форматах."""
    base = RESULTS_DIR if results_dir is None else results_dir
    if not base.is_dir():
        return []

    refs_by_name: dict[str, RunArtifactRef] = {}

    for path in base.glob(f"*{_LEGACY_EVENTS_SUFFIX}"):
        if not path.is_file():
            continue
        name = path.name[: -len(_LEGACY_EVENTS_SUFFIX)]
        _put_newer(
            refs_by_name,
            RunArtifactRef(name=name, events_path=path, format="legacy"),
        )

    for child in base.iterdir():
        if not child.is_dir():
            continue
        events_path = child / "events.jsonl"
        if not events_path.is_file():
            continue
        _put_newer(
            refs_by_name,
            RunArtifactRef(name=child.name, events_path=events_path, format="directory"),
        )

    return sorted(refs_by_name.values(), key=lambda ref: ref.mtime(), reverse=True)


def resolve_run_artifact(run_name: str, *, results_dir: Path | None = None) -> RunArtifactRef | None:
    """Разрешить конкретный прогон по имени в legacy/directory формате."""
    base = RESULTS_DIR if results_dir is None else results_dir
    legacy = base / f"{run_name}{_LEGACY_EVENTS_SUFFIX}"
    directory = base / run_name / "events.jsonl"

    candidates: list[RunArtifactRef] = []
    if legacy.is_file():
        candidates.append(RunArtifactRef(name=run_name, events_path=legacy, format="legacy"))
    if directory.is_file():
        candidates.append(RunArtifactRef(name=run_name, events_path=directory, format="directory"))

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    first, second = candidates
    return first if first.mtime() >= second.mtime() else second


def run_json_sidecar_candidates(
    ref: RunArtifactRef,
    stem: str,
    *,
    results_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Кандидаты JSON sidecar-файла для прогона (primary, fallback)."""
    base = RESULTS_DIR if results_dir is None else results_dir
    legacy = base / f"{ref.name}_{stem}.json"
    directory = base / ref.name / f"{stem}.json"
    if ref.format == "directory":
        return directory, legacy
    return legacy, directory


def run_log_sidecar_candidates(
    ref: RunArtifactRef,
    stem: str,
    *,
    results_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Кандидаты LOG sidecar-файла для прогона (primary, fallback)."""
    base = RESULTS_DIR if results_dir is None else results_dir
    legacy = base / f"{ref.name}_{stem}.log"
    directory = base / ref.name / f"{stem}.log"
    if ref.format == "directory":
        return directory, legacy
    return legacy, directory


def run_jsonl_sidecar_candidates(
    ref: RunArtifactRef,
    stem: str,
    *,
    results_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Кандидаты JSONL sidecar-файла для прогона (primary, fallback)."""
    base = RESULTS_DIR if results_dir is None else results_dir
    legacy = base / f"{ref.name}_{stem}.jsonl"
    directory = base / ref.name / f"{stem}.jsonl"
    if ref.format == "directory":
        return directory, legacy
    return legacy, directory


def _put_newer(refs: dict[str, RunArtifactRef], candidate: RunArtifactRef) -> None:
    existing = refs.get(candidate.name)
    if existing is None or candidate.mtime() >= existing.mtime():
        refs[candidate.name] = candidate
