"""Вспомогательные функции валидации, используемые в маршрутах."""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import HTTPException

from .settings import (
    MAX_ROUNDS,
    MAX_SEED,
    RUN_NAME_RE,
    RESULTS_DIR,
    SCENARIOS_DIR,
    SCENARIO_ID_RE,
    S_NUM_RE,
    G_NUM_RE,
    GOVERNANCE_MODES_DIR,
)


def validate_run_name(name: str) -> None:
    """Проверить имя прогона на допустимые символы и path traversal.

    Args:
        name: Имя прогона из URL.

    Raises:
        HTTPException 400: Если имя содержит недопустимые символы.
        HTTPException 400: Если итоговый путь выходит за пределы RESULTS_DIR.
    """
    if not RUN_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail="Invalid run name")
    legacy_resolved = (RESULTS_DIR / f"{name}_events.jsonl").resolve()
    dir_resolved = (RESULTS_DIR / name / "events.jsonl").resolve()
    if not str(legacy_resolved).startswith(str(RESULTS_DIR)):
        raise HTTPException(status_code=400, detail="Invalid run name")
    if not str(dir_resolved).startswith(str(RESULTS_DIR)):
        raise HTTPException(status_code=400, detail="Invalid run name")


def validate_scenario_id(scenario_id: str) -> None:
    """Проверить идентификатор сценария (S-номер, UUID или slug).

    Args:
        scenario_id: Строка-идентификатор вида ``S\\d+``, UUID или slug.

    Raises:
        HTTPException 400: Если содержит недопустимые символы.
        HTTPException 400: Если итоговый путь выходит за пределы SCENARIOS_DIR.
    """
    if not SCENARIO_ID_RE.fullmatch(scenario_id):
        raise HTTPException(status_code=400, detail="Invalid scenario ID")
    resolved = (SCENARIOS_DIR / f"{scenario_id}.json").resolve()
    if not str(resolved).startswith(str(SCENARIOS_DIR)):
        raise HTTPException(status_code=400, detail="Invalid scenario ID")


def validate_library_id(item_id: str, base_dir: Path, *, kind: str) -> None:
    """Проверить ID элемента (agent-type/personality) на безопасный путь.

    Args:
        item_id: Идентификатор элемента.
        base_dir: Корневая директория библиотеки.
        kind: Человекочитаемое название типа (для сообщения об ошибке).

    Raises:
        HTTPException 400: Если ID невалиден или путь небезопасен.
    """
    if not SCENARIO_ID_RE.fullmatch(item_id):
        raise HTTPException(status_code=400, detail=f"Invalid {kind} ID")
    resolved = (base_dir / f"{item_id}.json").resolve()
    if not str(resolved).startswith(str(base_dir)):
        raise HTTPException(status_code=400, detail=f"Invalid {kind} ID")


def next_s_number() -> str:
    """Определить следующий свободный S-номер для пользовательского сценария.

    Сканирует файлы S*.json в SCENARIOS_DIR и встроенные сценарии (S0-S6),
    находит максимальный номер и возвращает ``S{max+1}``.

    Returns:
        Строка вида ``S7``, ``S8`` и т.д.
    """
    from web.backend.constants import ScenarioId

    max_num = -1
    for member in ScenarioId:
        m = S_NUM_RE.match(member.value)
        if m:
            max_num = max(max_num, int(m.group(1)))

    for pattern in ("S*.json", "S*.yaml", "S*.yml"):
        for p in SCENARIOS_DIR.glob(pattern):
            m = S_NUM_RE.match(p.stem)
            if m:
                max_num = max(max_num, int(m.group(1)))

    return f"S{max_num + 1}"


def next_g_number() -> str:
    """Определить следующий свободный G-номер для пользовательского режима управления.

    Сканирует файлы G*.json в GOVERNANCE_MODES_DIR и встроенные режимы (G0-G3),
    находит максимальный номер и возвращает ``G{max+1}``.

    Returns:
        Строка вида ``G4``, ``G5`` и т.д.
    """
    from web.backend.constants import GovernanceMode

    max_num = -1
    for member in GovernanceMode:
        m = G_NUM_RE.match(member.value)
        if m:
            max_num = max(max_num, int(m.group(1)))

    for p in GOVERNANCE_MODES_DIR.glob("G*.json"):
        m = G_NUM_RE.match(p.stem)
        if m:
            max_num = max(max_num, int(m.group(1)))

    return f"G{max_num + 1}"


def resolve_seed(value: object | None) -> int:
    """Преобразовать seed из запроса/сценария в int.

    Если seed не задан (None) — генерируется случайный seed.

    Args:
        value: seed (int/str/None).

    Returns:
        seed как неотрицательный int.

    Raises:
        HTTPException 400: Если seed имеет неверный формат.
    """
    if value is None:
        return secrets.randbelow(1_000_000_000)

    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail="Invalid seed")

    if isinstance(value, int):
        if value < 0:
            raise HTTPException(status_code=400, detail="Invalid seed")
        if value > MAX_SEED:
            raise HTTPException(status_code=400, detail="Invalid seed")
        return value

    if isinstance(value, float):
        if not value.is_integer():
            raise HTTPException(status_code=400, detail="Invalid seed")
        seed = int(value)
        if seed < 0:
            raise HTTPException(status_code=400, detail="Invalid seed")
        if seed > MAX_SEED:
            raise HTTPException(status_code=400, detail="Invalid seed")
        return seed

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return secrets.randbelow(1_000_000_000)
        try:
            seed = int(s)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid seed") from exc
        if seed < 0:
            raise HTTPException(status_code=400, detail="Invalid seed")
        if seed > MAX_SEED:
            raise HTTPException(status_code=400, detail="Invalid seed")
        return seed

    raise HTTPException(status_code=400, detail="Invalid seed")


def resolve_rounds(value: object | None, *, default: int = 10) -> int:
    """Преобразовать rounds в int и ограничить разумным максимумом.

    Args:
        value: rounds (int/str/None).
        default: Значение по умолчанию.

    Returns:
        rounds как положительный int.

    Raises:
        HTTPException 400: Если rounds невалиден.
    """
    if value is None:
        rounds = default
    elif isinstance(value, bool):
        raise HTTPException(status_code=400, detail="Invalid rounds")
    else:
        try:
            rounds = int(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid rounds") from exc

    if rounds < 1 or rounds > MAX_ROUNDS:
        raise HTTPException(status_code=400, detail="Invalid rounds")
    return rounds
