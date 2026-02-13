# MAGISTRY — симулятор AI+DAO Governance

Минимально рабочий прототип симуляции по концепту `CONCEPT.README.md` и плану `WORKPLAN.md`.

## Быстрый старт

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

Прогон сценария:

```bash
magistry-sim --scenario S1 --governance G3 --seed 42 --jsonl logs/run_s1_g3.jsonl
```

Список сценариев:

```bash
magistry-sim --list-scenarios
```

## Примечания
- Реальные вызовы LLM/блокчейна здесь не реализованы: симуляция использует упрощённые правила и события, чтобы зафиксировать контуры `AI Audit + Reputation Freeze + DAO Tribunal`.
- В `.[dev]` включены зависимости для тестов визуализации (matplotlib).
