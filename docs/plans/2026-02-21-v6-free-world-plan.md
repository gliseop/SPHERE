# MAGISTRY v6: свободные агенты в свободном мире — План реализации

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Убрать привязку симуляции к конкретным организационным процессам и дать агентам полную свободу действий, контролируемую LLM-арбитром.

**Architecture:** Удаляем CASE_REGISTRY и FSM из cases.py. Дела становятся произвольными (тип и стадия — свободные строки). Арбитр получает описание организации от генератора мира и контролирует правдоподобность действий. LLM-оракул заменяет эвристический классификатор для определения нарушений постфактум. Сценарии становятся параметрическими (corruption_level + narrative_context).

**Tech Stack:** Python 3.12, Pydantic v2, pytest, OpenRouter/OpenAI API

---

### Task 1: Убрать CASE_REGISTRY и FSM из cases.py

Удаляем конечный автомат и реестр типов дел. Case, Proposal, Note, Vote остаются, но без привязки к конкретным типам.

**Files:**
- Modify: `src/magistry_sim/cases.py`
- Modify: `tests/test_models.py`

**Step 1: Обновить тесты**

Открыть `tests/test_models.py`. Удалить все тесты, использующие `CASE_REGISTRY`, `validate_transition`, `check_condition`, `apply_transition`, `CaseSchema`. Добавить тесты на создание Case с произвольным типом и стадией:

```python
from magistry_sim.cases import Case, Proposal, Note, Vote


def test_case_arbitrary_type():
    """Case принимает произвольный тип."""
    case = Case(
        id="c1",
        case_type="training",
        title="Обучение персонала",
        description="Курсы по ИБ",
        owner_id="off_1",
        stage="preparation",
    )
    assert case.case_type == "training"
    assert case.stage == "preparation"


def test_case_extra_fields_ignored():
    """Case игнорирует лишние поля от LLM."""
    case = Case(
        id="c1",
        case_type="audit",
        title="Проверка",
        description="Внутренний аудит",
        owner_id="off_1",
        stage="active",
        unknown_field="should_be_ignored",
    )
    assert case.case_type == "audit"


def test_case_stage_change():
    """Стадию дела можно менять напрямую."""
    case = Case(
        id="c1",
        case_type="procurement",
        title="Закупка",
        description="Серверы",
        owner_id="off_1",
        stage="open",
    )
    case.stage = "evaluation"
    assert case.stage == "evaluation"


def test_proposal_creation():
    """Proposal создаётся корректно."""
    p = Proposal(
        id="p1", case_id="c1", author_id="biz_1",
        content="Предложение", submitted_at=1,
    )
    assert p.author_id == "biz_1"


def test_note_creation():
    """Note создаётся корректно."""
    n = Note(
        id="n1", case_id="c1", author_id="off_1",
        content="Запись", created_at=1,
    )
    assert n.content == "Запись"


def test_vote_creation():
    """Vote создаётся корректно."""
    v = Vote(
        voter_id="juror_0", case_id="c1",
        verdict="виновен", reasoning="Улики", round=3,
    )
    assert v.verdict == "виновен"
```

**Step 2: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_models.py -v`
Expected: FAIL (test_case_extra_fields_ignored — extra="forbid" отклонит unknown_field)

**Step 3: Обновить cases.py**

Переписать `src/magistry_sim/cases.py` — удалить CaseSchema, CASE_REGISTRY, validate_transition, check_condition, apply_transition. Изменить `model_config` на `extra="ignore"` для всех моделей:

```python
"""Модели делопроизводства: дела, предложения, голоса."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Proposal(BaseModel):
    """Предложение (отклик) по делу."""

    model_config = {"extra": "ignore"}

    id: str
    case_id: str
    author_id: str
    content: str
    submitted_at: int


class Note(BaseModel):
    """Публичная запись в деле."""

    model_config = {"extra": "ignore"}

    id: str
    case_id: str
    author_id: str
    content: str
    created_at: int


class Vote(BaseModel):
    """Голос присяжного по делу трибунала."""

    model_config = {"extra": "ignore"}

    voter_id: str
    case_id: str
    verdict: str
    reasoning: str
    round: int


class Case(BaseModel):
    """Организационный процесс (дело).

    Тип и стадия — произвольные строки. Арбитр контролирует
    правдоподобность переходов между стадиями.
    """

    model_config = {"extra": "ignore"}

    id: str
    case_type: str
    title: str
    description: str
    owner_id: str
    stage: str = "open"
    params: str = ""
    proposals: list[Proposal] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    votes: list[Vote] = Field(default_factory=list)
    created_at: int = 0
    deadline_round: int | None = None
    closed_at: int | None = None
    decision: str | None = None
    justification: str | None = None
```

**Step 4: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_models.py -v`
Expected: PASS

**Step 5: Коммит**

```bash
git add src/magistry_sim/cases.py tests/test_models.py
git commit -m "refactor: remove CASE_REGISTRY and FSM from cases.py"
```

---

### Task 2: Обобщить операции состояния (state_ops.py)

Изменить `extra="forbid"` на `extra="ignore"` в StateOp. Убрать зависимость apply_state_op от CASE_REGISTRY. CreateCaseOp создаёт дело с произвольным типом.

**Files:**
- Modify: `src/magistry_sim/state_ops.py:24-31` (StateOp.model_config)
- Modify: `src/magistry_sim/state_ops.py:384-431` (apply_state_op: CreateCaseOp)
- Modify: `src/magistry_sim/state_ops.py:433-448` (apply_state_op: CloseCaseOp)
- Modify: `tests/test_state_ops.py`

**Step 1: Обновить тесты**

В `tests/test_state_ops.py` найти тест `test_extra_fields_rejected` (или аналогичный) и заменить на тест, проверяющий что extra поля игнорируются. Добавить тесты для произвольных типов дел:

```python
def test_extra_fields_ignored():
    """Лишние поля в операции игнорируются."""
    op = CreateCaseOp(
        params={"case_type": "training", "title": "Тест"},
        unknown_extra="value",
    )
    assert op.params["case_type"] == "training"


def test_create_case_arbitrary_type(world_state):
    """CreateCaseOp создаёт дело с произвольным типом."""
    op = CreateCaseOp(params={
        "case_type": "training",
        "title": "Обучение ИБ",
        "description": "Курсы",
        "owner_id": "off_1",
    })
    result = apply_state_op(op, world_state, round_num=0, agent_id="off_1")
    assert result.success
    case = list(world_state.cases.values())[0]
    assert case.case_type == "training"
    assert case.stage == "open"


def test_close_case_any_type(world_state):
    """CloseCaseOp закрывает дело любого типа."""
    from magistry_sim.cases import Case
    world_state.cases["c1"] = Case(
        id="c1", case_type="meeting", title="Совещание",
        description="", owner_id="off_1", stage="active",
    )
    op = CloseCaseOp(case_id="c1", decision="Завершено")
    result = apply_state_op(op, world_state, round_num=1, agent_id="off_1")
    assert result.success
    assert world_state.cases["c1"].closed_at == 1
    assert world_state.cases["c1"].decision == "Завершено"
```

**Step 2: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_state_ops.py -v -k "test_extra_fields_ignored or test_create_case_arbitrary or test_close_case_any"`
Expected: FAIL (extra="forbid" и CASE_REGISTRY ещё на месте)

**Step 3: Обновить state_ops.py**

3a. Изменить `model_config` у StateOp (строка 31):
```python
class StateOp(BaseModel):
    """Базовая операция над состоянием мира."""
    model_config = {"extra": "ignore"}
    op: str
```

3b. В `apply_state_op`, блок CreateCaseOp (строки 401-431) — убрать зависимость от CASE_REGISTRY:
```python
    if isinstance(op, CreateCaseOp):
        case_type = op.params.get("case_type", "general")
        case_id = state.new_case_id()
        case = Case(
            id=case_id,
            case_type=case_type,
            title=op.params.get("title", ""),
            description=op.params.get("description", ""),
            owner_id=op.params.get("owner_id", agent_id),
            stage=op.params.get("stage", "open"),
            params=op.params.get("params", ""),
            created_at=round_num,
        )
        state.cases[case_id] = case
        state.event_log.log(
            round=round_num,
            event_type="case_opened",
            agent_id=agent_id,
            payload={"case_id": case_id, "case_type": case_type},
        )
        return OpResult(True, f"Дело {case_id} создано")
```

3c. В `apply_state_op`, блок CloseCaseOp (строки 433-448) — убрать зависимость от CASE_REGISTRY:
```python
    if isinstance(op, CloseCaseOp):
        case = state.cases.get(op.case_id)
        if case is None:
            return OpResult(False, f"Дело {op.case_id} не найдено")
        case.stage = "closed"
        case.decision = op.decision
        case.closed_at = round_num
        state.event_log.log(
            round=round_num,
            event_type="case_resolved",
            agent_id=agent_id,
            payload={"case_id": op.case_id, "decision": op.decision},
        )
        return OpResult(True, f"Дело {op.case_id} закрыто")
```

3d. В `apply_state_op`, блок InitiateTribunalOp (строки 582-604) — убрать хардкод `owner_id="auditor"`:
```python
    if isinstance(op, InitiateTribunalOp):
        tribunal_id = state.new_case_id()
        tribunal = Case(
            id=tribunal_id,
            case_type="investigation",
            title=f"Расследование по делу {op.case_id}",
            description=f"Обвиняемый: {op.accused_id}",
            owner_id=agent_id or "auditor",
            stage="tribunal",
            params=f"source_case={op.case_id},accused={op.accused_id}",
            created_at=round_num,
        )
        state.cases[tribunal_id] = tribunal
        state.event_log.log(
            round=round_num,
            event_type="tribunal_formed",
            payload={
                "tribunal_id": tribunal_id,
                "source_case": op.case_id,
                "accused": op.accused_id,
            },
        )
        return OpResult(True, f"Расследование {tribunal_id} начато")
```

3e. Убрать импорт `CASE_REGISTRY, apply_transition` из функции `apply_state_op` (строка 401). Оставить только `from .cases import Case, Proposal, Vote`.

**Step 4: Запустить все тесты state_ops**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_state_ops.py -v`
Expected: PASS (обновить сломавшиеся тесты, которые зависели от CASE_REGISTRY)

**Step 5: Коммит**

```bash
git add src/magistry_sim/state_ops.py tests/test_state_ops.py
git commit -m "refactor: generalize state_ops, remove CASE_REGISTRY dependency"
```

---

### Task 3: Обновить модель сценария (config.py, scenarios.py, enums.py)

Добавить `corruption_level` и `narrative_context` в ScenarioConfig. Сделать Capability и Need универсальными. Переписать сценарии S0/S1/S2 как параметрические.

**Files:**
- Modify: `src/magistry_sim/config.py`
- Modify: `src/magistry_sim/scenarios.py`
- Modify: `tests/test_scenarios.py`

**Step 1: Обновить тесты**

Переписать `tests/test_scenarios.py`:

```python
import pytest
from magistry_sim.config import ScenarioConfig, AgentProfile, Need, Capability
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.scenarios import get_scenario, add_governance_agents


def test_scenario_has_corruption_level():
    """Сценарий содержит уровень коррупции."""
    scenario = get_scenario(ScenarioId.S0)
    assert hasattr(scenario, "corruption_level")
    assert 0.0 <= scenario.corruption_level <= 1.0


def test_scenario_has_narrative_context():
    """Сценарий содержит нарративный контекст."""
    scenario = get_scenario(ScenarioId.S0)
    assert hasattr(scenario, "narrative_context")
    assert len(scenario.narrative_context) > 0


def test_s0_clean_scenario():
    """S0 — чистый сценарий без коррупции."""
    scenario = get_scenario(ScenarioId.S0)
    assert scenario.corruption_level == 0.0
    assert len(scenario.agents) >= 3


def test_s1_corruption_scenario():
    """S1 — сценарий с предпосылками к коррупции."""
    scenario = get_scenario(ScenarioId.S1)
    assert scenario.corruption_level > 0.0
    # Должны быть связи между агентами
    has_connections = any(
        len(a.connections) > 0 for a in scenario.agents
    )
    assert has_connections


def test_need_without_case_type():
    """Need может быть без привязки к типу дела."""
    need = Need(
        description="Организовать обучение",
        target_agent_id="off_1",
        appear_round=0,
    )
    assert need.description == "Организовать обучение"


def test_add_governance_agents_g1():
    """G1 добавляет аудитора."""
    scenario = get_scenario(ScenarioId.S0)
    updated = add_governance_agents(scenario, GovernanceMode.G1)
    auditor_exists = any(
        any(c.action == "audit" for c in a.capabilities)
        for a in updated.agents
    )
    assert auditor_exists


def test_add_governance_agents_g3():
    """G3 добавляет аудитора и присяжных."""
    scenario = get_scenario(ScenarioId.S0)
    updated = add_governance_agents(scenario, GovernanceMode.G3)
    juror_count = sum(1 for a in updated.agents if a.id.startswith("juror_"))
    assert juror_count >= 3


def test_capability_universal():
    """Capability может не иметь привязки к типам дел."""
    cap = Capability(action="manage", case_types=[])
    assert cap.action == "manage"
    assert cap.case_types == []
```

**Step 2: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_scenarios.py -v`
Expected: FAIL (corruption_level и narrative_context ещё не добавлены)

**Step 3: Обновить config.py**

3a. Добавить поля `corruption_level` и `narrative_context` в ScenarioConfig. Изменить `model_config` на `extra="ignore"`. Сделать `case_type` в Need необязательным:

```python
class Need(BaseModel):
    """Потребность организации, возникающая в определённый раунд."""

    model_config = {"extra": "ignore"}

    description: str
    target_agent_id: str
    appear_round: int
    case_type: str = ""
    urgency: str = "средняя"


class ScenarioConfig(BaseModel):
    """Полная конфигурация сценария симуляции."""

    model_config = {"extra": "ignore"}

    id: ScenarioId
    title: str
    description: str
    max_rounds: int = 8
    seed: int = 42
    agents: list[AgentProfile] = Field(default_factory=list)
    needs: list[Need] = Field(default_factory=list)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    corruption_level: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Уровень коррупционного давления (0.0-1.0).",
    )
    narrative_context: str = Field(
        default="",
        description="Описание типа организации для генератора мира.",
    )
```

3b. Также изменить `model_config` на `extra="ignore"` в Capability, Connection, ResourcePool, AgentProfile, GovernanceConfig.

**Step 4: Обновить scenarios.py**

Переписать SCENARIOS с параметрическими сценариями. Capabilities теперь более универсальные (не привязаны к procurement/hiring). Добавить narrative_context и corruption_level:

```python
SCENARIOS: dict[ScenarioId, ScenarioConfig] = {
    ScenarioId.S0: ScenarioConfig(
        id=ScenarioId.S0,
        title="Чистая организация",
        description=(
            "Базовый сценарий: муниципальная администрация без предпосылок "
            "к коррупции. Контрольный прогон."
        ),
        max_rounds=15,
        seed=42,
        corruption_level=0.0,
        narrative_context=(
            "Муниципальная администрация среднего города. "
            "Отделы: обеспечение, кадры, бюджет, ИТ. "
            "Текущие процессы: закупки, наём, обучение, аудит."
        ),
        agents=[
            AgentProfile(
                id="off_1",
                name="Иванов А.П.",
                position="начальник отдела обеспечения",
                capabilities=[
                    Capability(action="manage", case_types=[]),
                    Capability(action="open_case", case_types=[]),
                    Capability(action="resolve_case", case_types=[]),
                ],
                greed=0.2,
                fear=0.5,
                honesty=0.8,
                initial_resources=ResourcePool(
                    budget_limit=10_000_000,
                    staffing_slots=5,
                ),
            ),
            AgentProfile(
                id="biz_1",
                name="Петров С.И.",
                position="директор «ТехСнаб»",
                capabilities=[
                    Capability(action="submit_proposal", case_types=[]),
                ],
                greed=0.3,
                fear=0.3,
                honesty=0.7,
                competence=0.7,
                initial_resources=ResourcePool(
                    contract_capacity=3,
                ),
            ),
            AgentProfile(
                id="biz_2",
                name="Сидоров В.К.",
                position="директор «ГрадТех»",
                capabilities=[
                    Capability(action="submit_proposal", case_types=[]),
                ],
                greed=0.3,
                fear=0.4,
                honesty=0.7,
                competence=0.6,
                initial_resources=ResourcePool(
                    contract_capacity=3,
                ),
            ),
        ],
        needs=[
            Need(
                description="Организовать работу отдела обеспечения",
                target_agent_id="off_1",
                appear_round=0,
                urgency="высокая",
            ),
        ],
        governance=GovernanceConfig(mode=GovernanceMode.G0),
    ),
    ScenarioId.S1: ScenarioConfig(
        id=ScenarioId.S1,
        title="Предпосылки к сговору",
        description=(
            "Организация с предпосылками к коррупции: один из чиновников "
            "и подрядчик — бывшие коллеги, жадность и нечестность повышены."
        ),
        max_rounds=25,
        seed=42,
        corruption_level=0.5,
        narrative_context=(
            "Муниципальная администрация среднего города. "
            "Отделы: обеспечение, кадры, бюджет, ИТ. "
            "Текущие процессы: закупки, наём, обучение, аудит. "
            "В организации ходят слухи о неформальных связях."
        ),
        agents=[
            AgentProfile(
                id="off_1",
                name="Козлов И.М.",
                position="начальник отдела обеспечения",
                capabilities=[
                    Capability(action="manage", case_types=[]),
                    Capability(action="open_case", case_types=[]),
                    Capability(action="resolve_case", case_types=[]),
                ],
                greed=0.8,
                fear=0.3,
                honesty=0.2,
                connections=[
                    Connection(
                        target_id="biz_1",
                        name="Петров С.И.",
                        relation="бывший коллега",
                        strength=3.0,
                    ),
                ],
                initial_resources=ResourcePool(
                    budget_limit=10_000_000,
                    staffing_slots=5,
                ),
            ),
            AgentProfile(
                id="biz_1",
                name="Петров С.И.",
                position="директор «ТехСнаб»",
                capabilities=[
                    Capability(action="submit_proposal", case_types=[]),
                ],
                greed=0.8,
                fear=0.2,
                honesty=0.2,
                competence=0.4,
                connections=[
                    Connection(
                        target_id="off_1",
                        name="Козлов И.М.",
                        relation="бывший коллега",
                        strength=3.0,
                    ),
                ],
                initial_resources=ResourcePool(
                    contract_capacity=3,
                ),
            ),
            AgentProfile(
                id="biz_2",
                name="Сидоров В.К.",
                position="директор «ГрадТех»",
                capabilities=[
                    Capability(action="submit_proposal", case_types=[]),
                ],
                greed=0.3,
                fear=0.4,
                honesty=0.7,
                competence=0.8,
                initial_resources=ResourcePool(
                    contract_capacity=3,
                ),
            ),
        ],
        needs=[
            Need(
                description="Организовать работу отдела обеспечения",
                target_agent_id="off_1",
                appear_round=0,
                urgency="высокая",
            ),
        ],
        governance=GovernanceConfig(mode=GovernanceMode.G0),
    ),
    ScenarioId.S2: ScenarioConfig(
        id=ScenarioId.S2,
        title="Кумовство в организации",
        description=(
            "Организация с родственными связями между сотрудниками "
            "и внешними участниками."
        ),
        max_rounds=25,
        seed=42,
        corruption_level=0.7,
        narrative_context=(
            "Региональное управление строительства. "
            "Отделы: проектирование, экспертиза, кадры, снабжение. "
            "Текущие процессы: выдача разрешений, наём, аттестация, "
            "закупки стройматериалов."
        ),
        agents=[
            AgentProfile(
                id="off_1",
                name="Козлов И.М.",
                position="начальник отдела",
                capabilities=[
                    Capability(action="manage", case_types=[]),
                    Capability(action="open_case", case_types=[]),
                    Capability(action="resolve_case", case_types=[]),
                ],
                greed=0.6,
                fear=0.3,
                honesty=0.3,
                connections=[
                    Connection(
                        target_id="off_2",
                        name="Волков Д.Н.",
                        relation="коллега по отделу",
                        strength=2.5,
                    ),
                ],
                initial_resources=ResourcePool(
                    budget_limit=5_000_000,
                    staffing_slots=5,
                ),
            ),
            AgentProfile(
                id="off_2",
                name="Волков Д.Н.",
                position="специалист отдела",
                capabilities=[
                    Capability(action="submit_proposal", case_types=[]),
                ],
                greed=0.4,
                fear=0.5,
                honesty=0.5,
                connections=[
                    Connection(
                        target_id="off_1",
                        name="Козлов И.М.",
                        relation="начальник",
                        strength=2.5,
                    ),
                    Connection(
                        target_id="cand_1",
                        name="Волков А.Д.",
                        relation="родственник (племянник)",
                        strength=5.0,
                    ),
                ],
            ),
            AgentProfile(
                id="cand_1",
                name="Волков А.Д.",
                position="внешний специалист",
                capabilities=[
                    Capability(action="submit_proposal", case_types=[]),
                ],
                greed=0.3,
                fear=0.5,
                honesty=0.5,
                competence=0.3,
                connections=[
                    Connection(
                        target_id="off_2",
                        name="Волков Д.Н.",
                        relation="дядя",
                        strength=5.0,
                    ),
                ],
            ),
            AgentProfile(
                id="cand_2",
                name="Антонова И.С.",
                position="внешний специалист",
                capabilities=[
                    Capability(action="submit_proposal", case_types=[]),
                ],
                greed=0.2,
                fear=0.3,
                honesty=0.8,
                competence=0.9,
            ),
        ],
        needs=[
            Need(
                description="Организовать работу отдела",
                target_agent_id="off_1",
                appear_round=0,
                urgency="средняя",
            ),
        ],
        governance=GovernanceConfig(mode=GovernanceMode.G0),
    ),
}
```

**Step 5: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_scenarios.py -v`
Expected: PASS

**Step 6: Коммит**

```bash
git add src/magistry_sim/config.py src/magistry_sim/scenarios.py tests/test_scenarios.py
git commit -m "refactor: parametric scenarios with corruption_level and narrative_context"
```

---

### Task 4: Переписать WORLD_RULES и арбитр

Убрать из системного промпта арбитра привязку к «закупкам/найму». Арбитр получает описание организации от контекста. Добавить organization_description в evaluate().

**Files:**
- Modify: `src/magistry_sim/world_rules.py`
- Modify: `src/magistry_sim/arbiter.py`
- Modify: `tests/test_arbiter.py`

**Step 1: Обновить тесты**

В `tests/test_arbiter.py` добавить тест, проверяющий что арбитр принимает `org_description`:

```python
def test_arbiter_accepts_org_description(mock_llm):
    """Arbiter.evaluate принимает описание организации."""
    arbiter = Arbiter(llm=mock_llm)
    verdict = arbiter.evaluate(
        agent_id="off_1",
        description="Организовать обучение по ИБ",
        target="отдел ИТ",
        justification="Требование регулятора",
        state=mock_state,
        round_num=0,
        org_description="Муниципальная администрация среднего города.",
    )
    assert isinstance(verdict.feasible, bool)
```

**Step 2: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_arbiter.py -v`
Expected: FAIL (org_description не поддерживается)

**Step 3: Переписать world_rules.py**

```python
"""Правила среды -- системный промпт для LLM-арбитра.

Описывает физику и институциональное устройство моделируемого мира.
Арбитр оценивает реалистичность действий, а не их моральность.
"""

WORLD_RULES = """Ты — арбитр симуляции организационных процессов. Твоя задача — оценить, \
реализуемо ли описанное действие агента в текущем контексте, и если да — \
определить, как оно изменит состояние мира.

## Устройство мира

Симуляция моделирует организацию с отделами, бюджетами и разнообразными \
процессами: закупки, наём, обучение, аттестация, проекты, совещания, ревизии \
и любые другие виды организационной деятельности.

В мире действуют сотрудники разных должностей, подрядчики, аудиторы \
и другие участники. Каждый агент имеет определённые полномочия, ресурсы \
и личностные характеристики.

{org_description}

## Физика мира

Действия делятся на легальные и нелегальные. Оба типа физически возможны, но нелегальные \
несут риск обнаружения и последствий для репутации.

Легальные действия: создание дел, подача предложений, принятие решений, \
отправка отчётов, голосование, обмен сообщениями, перемещение, организация \
мероприятий, проведение проверок — любые действия, соответствующие должности и полномочиям.

Нелегальные действия (физически возможны, но рискованны):
- Подделка документов: возможна, если агент имеет доступ к делу. Оставляет след.
- Передача средств (взятка): возможна при личной встрече. Требует приватной обстановки.
- Давление на участников: возможно через сообщения или личную встречу. Оставляет след.
- Манипуляция с делами: заморозка, отмена, изменение параметров. Может вызвать подозрения.
- Скрытие информации: возможно, но не гарантировано.
- Фаворитизм: предпочтение знакомых при принятии решений.
- Нецелевое расходование: использование бюджета не по назначению.

## Что оценивать

1. Физическая возможность: может ли действие произойти (есть ли полномочия, ресурсы, доступ)?
2. Контекстуальная правдоподобность: реалистично ли действие для данной должности и ситуации?
3. Последствия: какие изменения WorldState вызывает действие?
4. Побочные эффекты: какие следы оставляет действие (улики, репутационный риск)?

## Формат ответа

Ты ДОЛЖЕН вернуть JSON по заданной схеме. Не объясняй, не комментируй — только JSON.

## Доступные операции (state_changes)

Каждый элемент state_changes — словарь с ключом "op" и дополнительными полями:

- create_case: создать дело (params: {{case_type, title, description, owner_id}})
- close_case: закрыть дело (case_id, decision)
- modify_case: изменить параметры (case_id, changes: {{поле: значение}})
- transfer_funds: передать средства (from_id, to_id, amount)
- add_evidence: добавить улику (evidence_type, description, visible_to)
- remove_evidence: удалить улику (evidence_id)
- modify_reputation: изменить репутацию (agent_id, delta)
- send_message: отправить сообщение (from_id, to_id, content, private)
- update_graph: усилить связь (agent_a, agent_b, delta)
- freeze_case: заморозить дело (case_id)
- file_complaint: подать жалобу (case_id, assessment)
- initiate_tribunal: начать расследование (case_id, accused_id)
- cast_vote: проголосовать (case_id, voter_id, verdict, reasoning)
- move_agent: переместить агента (agent_id, location_id)
- create_need: создать потребность (description, target_agent_id, urgency)
- submit_proposal: подать предложение (case_id, author_id, content)
"""


ARBITER_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "feasible": {
            "type": "boolean",
            "description": "Может ли действие физически произойти",
        },
        "state_changes": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Операции над WorldState",
        },
        "side_effects": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Побочные эффекты (улики, репутационные риски)",
        },
        "narrative": {
            "type": "string",
            "description": "Текстовое описание результата действия",
        },
    },
    "required": ["feasible", "state_changes", "side_effects", "narrative"],
    "additionalProperties": False,
}
```

**Step 4: Обновить arbiter.py**

Добавить параметр `org_description` в метод `evaluate()`. Вставить описание организации в WORLD_RULES через `.format()`:

В `arbiter.py`, метод `evaluate()` — добавить параметр `org_description: str = ""` и использовать его при форматировании WORLD_RULES:

```python
    def evaluate(
        self,
        agent_id: str,
        description: str,
        target: str,
        justification: str,
        state: "WorldState",
        round_num: int,
        org_description: str = "",
    ) -> ArbiterVerdict:
```

В системном промпте вызова LLM заменить `WORLD_RULES` на:
```python
        org_section = ""
        if org_description:
            org_section = f"\n## Описание организации\n\n{org_description}\n"
        system = WORLD_RULES.format(org_description=org_section)
```

**Step 5: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_arbiter.py -v`
Expected: PASS

**Step 6: Коммит**

```bash
git add src/magistry_sim/world_rules.py src/magistry_sim/arbiter.py tests/test_arbiter.py
git commit -m "refactor: generalize WORLD_RULES and arbiter, add org_description"
```

---

### Task 5: Обобщить контекст агентов (context.py)

Убрать зависимость от CASE_REGISTRY. Убрать хардкод investigation/tribunal в _build_juror_section. Показывать дела без FSM-проверок.

**Files:**
- Modify: `src/magistry_sim/context.py`
- Modify: `tests/test_context.py`

**Step 1: Обновить context.py**

1a. Убрать импорт `CASE_REGISTRY` (строка 5):
```python
# Было: from .cases import CASE_REGISTRY
# Убрать эту строку
```

1b. В `build_situation()`, блок «Текущие дела» (строки 96-125) — убрать зависимость от CASE_REGISTRY:
```python
    # Текущие дела
    cases = state.get_cases_involving(agent_id)
    if cases:
        parts.append("Текущие дела:")
        for case in cases:
            is_closed = case.closed_at is not None
            status_str = (
                "закрыто" if is_closed else f"стадия «{case.stage}»"
            )
            line = (
                f"  #{case.id} ({case.case_type}, "
                f"{case.title}): {status_str}"
            )
            if case.proposals and not is_closed:
                line += f", предложений: {len(case.proposals)}"
            if case.deadline_round and not is_closed:
                line += f", дедлайн: раунд {case.deadline_round}"
            parts.append(line)

            for prop in case.proposals:
                parts.append(
                    f"    {prop.author_id}: {prop.content}"
                )
            for note in case.notes:
                parts.append(
                    f"    [{note.author_id}, р.{note.created_at}]: "
                    f"{note.content}"
                )
```

1c. В `_build_juror_section()` (строка 280) — обобщить проверку: вместо `case.case_type == "investigation" and case.stage == "tribunal"` использовать проверку на наличие голосования:
```python
    for case in state.cases.values():
        if case.stage == "tribunal" and case.closed_at is None:
            already = any(
                v.voter_id == agent_id for v in case.votes
            )
            if not already:
                parts.append(
                    f"Вы назначены присяжным по делу {case.id}."
                )
                reports = state.event_log.get_events(
                    event_type="report_filed"
                )
                for event in reports:
                    if event.payload.get("case_id") == case.id:
                        parts.append(
                            f"  Отчёт аудитора: "
                            f"{event.payload.get('assessment', '')}"
                        )
                parts.append(
                    "  Проголосуйте по этому делу."
                )
```

**Step 2: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_context.py -v`
Expected: PASS

**Step 3: Коммит**

```bash
git add src/magistry_sim/context.py
git commit -m "refactor: remove CASE_REGISTRY dependency from context.py"
```

---

### Task 6: Обобщить environment.py

Убрать FSM-переходы (_check_conditional_transitions), импорт CASE_REGISTRY, хардкод investigation/tribunal. Передавать org_description арбитру.

**Files:**
- Modify: `src/magistry_sim/environment.py`
- Modify: `tests/test_environment.py`

**Step 1: Обновить environment.py**

1a. Убрать импорт FSM-функций (строка 12):
```python
# Было:
# from .cases import CASE_REGISTRY, apply_transition, check_condition
# Стало:
from .cases import Case
```

1b. Удалить метод `_check_conditional_transitions()` (строки 386-413) полностью.

1c. В `run()` (строка 162) — убрать вызов `self._check_conditional_transitions()`.

1d. В `_run_agent_turn()` — передавать org_description арбитру при вызове evaluate:
```python
                        verdict = self._arbiter.evaluate(
                            agent_id=agent_id,
                            description=description,
                            target=target,
                            justification=justification,
                            state=self._state,
                            round_num=self._state.round,
                            org_description=getattr(
                                self._scenario, "narrative_context", ""
                            ),
                        )
```

1e. В `_apply_round_end_effects()` (строки 475-514) — убрать зависимость от check_condition и apply_transition для проверки кворума трибуналов. Заменить на простую проверку:
```python
        # Проверка кворума расследований
        for case in self._state.cases.values():
            if case.stage == "tribunal" and case.closed_at is None:
                jury_size = self._scenario.governance.jury_size
                required_votes = max(1, jury_size)
                if len(case.votes) >= required_votes:
                    guilty = sum(
                        1
                        for v in case.votes
                        if v.verdict.strip().lower() == "виновен"
                    )
                    not_guilty = sum(
                        1
                        for v in case.votes
                        if v.verdict.strip().lower() == "невиновен"
                    )
                    verdict = (
                        "виновен"
                        if guilty > not_guilty
                        else "невиновен"
                    )
                    case.stage = "verdict"
                    case.decision = verdict
                    case.closed_at = self._state.round

                    self._state.event_log.log(
                        round=self._state.round,
                        event_type="tribunal_verdict",
                        payload={
                            "case_id": case.id,
                            "verdict": verdict,
                            "guilty_votes": guilty,
                            "not_guilty_votes": not_guilty,
                        },
                    )
```

1f. В `_form_tribunal()` — уже использует Case напрямую, не зависит от CASE_REGISTRY. Оставить как есть.

**Step 2: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_environment.py tests/test_environment_v5.py -v`
Expected: PASS (или адаптировать сломавшиеся тесты)

**Step 3: Коммит**

```bash
git add src/magistry_sim/environment.py tests/test_environment.py
git commit -m "refactor: remove FSM from environment, pass org_description to arbiter"
```

---

### Task 7: Обобщить tools/actions.py

Убрать зависимость от CASE_REGISTRY и хардкод типов дел из функций-инструментов.

**Files:**
- Modify: `src/magistry_sim/tools/actions.py`
- Modify: `tests/test_tools.py`

**Step 1: Обновить tools/actions.py**

1a. В `open_case()` — убрать хардкод deadline для procurement и зависимость от CASE_REGISTRY:
```python
def open_case(case_type: str = "general", title: str = "", description: str = "") -> str:
    """Открыть новое дело.

    Args:
        case_type: Тип дела (произвольная строка).
        title: Заголовок дела.
        description: Описание дела.

    Returns:
        Результат операции.
    """
    state = get_state()
    caller_id = get_caller_id()

    if not state.has_capability(caller_id, "open_case", case_type):
        if not state.has_capability(caller_id, "manage", ""):
            return f"У вас нет полномочий для создания дел."

    case_id = state.new_case_id()
    case = Case(
        id=case_id,
        case_type=case_type,
        title=title or f"Дело ({case_type})",
        description=description,
        owner_id=caller_id,
        stage="open",
        created_at=state.round,
    )
    state.cases[case_id] = case

    # Убрать потребность, если создание дела её закрывает
    state.active_needs = [
        n for n in state.active_needs
        if not (n.target_agent_id == caller_id)
    ]

    state.event_log.log(
        round=state.round,
        event_type="case_opened",
        agent_id=caller_id,
        payload={"case_id": case_id, "case_type": case_type},
    )
    return f"Дело {case_id} ({case_type}) открыто."
```

1b. В `submit_proposal()` — убрать проверку terminal_stages через CASE_REGISTRY:
```python
def submit_proposal(case_id: str, content: str) -> str:
    state = get_state()
    caller_id = get_caller_id()
    case = state.cases.get(case_id)
    if case is None:
        return f"Дело {case_id} не найдено."
    if case.closed_at is not None:
        return f"Дело {case_id} закрыто."
    if caller_id == case.owner_id:
        return "Владелец дела не может подавать предложения по своему делу."
    # ... rest of the function without CASE_REGISTRY
```

1c. В `resolve_case()` — убрать CASE_REGISTRY lookup для action_transitions:
```python
def resolve_case(case_id: str, decision: str, justification: str = "") -> str:
    state = get_state()
    caller_id = get_caller_id()
    case = state.cases.get(case_id)
    if case is None:
        return f"Дело {case_id} не найдено."
    if case.owner_id != caller_id:
        if not state.has_capability(caller_id, "resolve_case", case.case_type):
            return "У вас нет полномочий для решения этого дела."
    if case.closed_at is not None:
        return f"Дело {case_id} уже закрыто."

    case.decision = decision
    case.justification = justification
    case.stage = "closed"
    case.closed_at = state.round
    # ... event_log and return
```

1d. В `cast_vote()` — убрать проверку `case.case_type == "investigation"`:
```python
def cast_vote(case_id: str, verdict: str, reasoning: str = "") -> str:
    state = get_state()
    caller_id = get_caller_id()
    if not state.has_capability(caller_id, "vote", ""):
        return "У вас нет полномочий для голосования."
    case = state.cases.get(case_id)
    if case is None:
        return f"Дело {case_id} не найдено."
    if case.stage != "tribunal":
        return "Голосование возможно только на стадии трибунала."
    # ... rest without case_type check
```

**Step 2: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_tools.py -v`
Expected: PASS

**Step 3: Коммит**

```bash
git add src/magistry_sim/tools/actions.py tests/test_tools.py
git commit -m "refactor: remove CASE_REGISTRY from tools/actions.py"
```

---

### Task 8: Создать LLM-оракул (oracle.py)

Новый модуль, заменяющий эвристический ViolationClassifier. Оракул имеет доступ ко всем событиям (включая приватные) и определяет ground truth нарушений постфактум.

**Files:**
- Create: `src/magistry_sim/oracle.py`
- Create: `tests/test_oracle.py`

**Step 1: Написать тесты**

```python
"""Тесты LLM-оракула."""
import pytest
from magistry_sim.oracle import ViolationOracle, OracleVerdict


class MockOracleLLM:
    """Мок LLM для оракула."""

    def __init__(self, verdicts: list[dict]):
        self._verdicts = verdicts
        self._call_idx = 0

    def generate_structured(self, system, user, schema, **kwargs):
        from magistry_sim.llm import LLMResponse
        if self._call_idx < len(self._verdicts):
            data = self._verdicts[self._call_idx]
            self._call_idx += 1
        else:
            data = {"violations": []}
        return LLMResponse(content="", data=data, tokens_used=0)


def test_oracle_no_violations():
    """Оракул не находит нарушений в чистом прогоне."""
    llm = MockOracleLLM([{"violations": []}])
    oracle = ViolationOracle(llm=llm)
    verdicts = oracle.analyze(
        events=[
            {"event_type": "case_opened", "agent_id": "off_1", "round": 0,
             "payload": {"case_id": "c1", "case_type": "training"}},
        ],
        messages=[],
        cases={"c1": {"id": "c1", "case_type": "training",
                       "owner_id": "off_1", "closed_at": 5}},
    )
    assert len(verdicts) == 0


def test_oracle_finds_violation():
    """Оракул обнаруживает нарушение."""
    llm = MockOracleLLM([{
        "violations": [{
            "case_id": "c1",
            "violation_type": "взятка",
            "agents_involved": ["off_1", "biz_1"],
            "confidence": 0.9,
            "evidence": ["Приватное сообщение о передаче средств"],
            "reasoning": "Агент off_1 передал средства biz_1",
        }]
    }])
    oracle = ViolationOracle(llm=llm)
    verdicts = oracle.analyze(
        events=[
            {"event_type": "funds_transferred", "agent_id": "off_1",
             "round": 2, "payload": {"from": "off_1", "to": "biz_1",
                                      "amount": 50000}},
        ],
        messages=[
            {"from_id": "off_1", "to_id": "biz_1",
             "content": "Жду перевод", "private": True, "round": 1},
        ],
        cases={"c1": {"id": "c1", "case_type": "procurement",
                       "owner_id": "off_1", "closed_at": 5}},
    )
    assert len(verdicts) == 1
    assert verdicts[0].violation_type == "взятка"
    assert verdicts[0].confidence == 0.9


def test_oracle_verdict_structure():
    """OracleVerdict имеет все необходимые поля."""
    v = OracleVerdict(
        case_id="c1",
        violation_type="фаворитизм",
        agents_involved=["off_1", "biz_1"],
        confidence=0.8,
        evidence=["Улика 1"],
        reasoning="Обоснование",
    )
    assert v.case_id == "c1"
    assert v.agents_involved == ["off_1", "biz_1"]
```

**Step 2: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_oracle.py -v`
Expected: FAIL (модуль oracle.py ещё не существует)

**Step 3: Создать oracle.py**

```python
"""LLM-оракул: постфактум-анализ нарушений с полным доступом к логу."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .llm import LLMProvider

logger = logging.getLogger(__name__)


ORACLE_SCHEMA = {
    "type": "object",
    "properties": {
        "violations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string"},
                    "violation_type": {"type": "string"},
                    "agents_involved": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confidence": {"type": "number"},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "reasoning": {"type": "string"},
                },
                "required": [
                    "case_id", "violation_type", "agents_involved",
                    "confidence", "evidence", "reasoning",
                ],
            },
        },
    },
    "required": ["violations"],
    "additionalProperties": False,
}

ORACLE_SYSTEM = (
    "Ты — всеведущий аналитик организационных нарушений. "
    "Ты видишь ВСЕ события и сообщения, включая приватные. "
    "Твоя задача — определить, какие действия представляют собой "
    "нарушения: взятки, сговор, фаворитизм, подделка документов, "
    "злоупотребление полномочиями, нецелевое расходование, давление.\n\n"
    "Для каждого нарушения укажи: связанное дело (case_id), тип нарушения, "
    "вовлечённых агентов, уверенность (0.0-1.0), улики и обоснование.\n\n"
    "Если нарушений нет — верни пустой список violations.\n\n"
    "Верни JSON по заданной схеме."
)


@dataclass
class OracleVerdict:
    """Вердикт оракула по одному нарушению.

    Attributes:
        case_id: Идентификатор связанного дела.
        violation_type: Тип нарушения.
        agents_involved: Список вовлечённых агентов.
        confidence: Уверенность оракула (0.0-1.0).
        evidence: Список обнаруженных улик.
        reasoning: Обоснование вердикта.
    """

    case_id: str
    violation_type: str
    agents_involved: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    reasoning: str = ""


class ViolationOracle:
    """LLM-оракул для постфактум-анализа нарушений.

    Получает полный лог событий и сообщений (включая приватные)
    и определяет ground truth нарушений для расчёта метрик.

    Args:
        llm: Провайдер языковой модели.
    """

    def __init__(self, llm: "LLMProvider") -> None:
        self._llm = llm

    def analyze(
        self,
        events: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        cases: dict[str, dict[str, Any]],
    ) -> list[OracleVerdict]:
        """Проанализировать все события и определить нарушения.

        Args:
            events: Полный журнал событий.
            messages: Все сообщения (включая приватные).
            cases: Словарь дел.

        Returns:
            Список вердиктов о нарушениях.
        """
        events_text = "\n".join(
            f"- [{e.get('round', '?')}] {e.get('agent_id', '?')}: "
            f"{e.get('event_type', '?')} {e.get('payload', {})}"
            for e in events[:100]
        ) or "Нет событий"

        msgs_text = "\n".join(
            f"- [{m.get('round', '?')}] "
            f"{'[ПРИВАТНО]' if m.get('private') else '[публично]'} "
            f"{m.get('from_id', '?')} -> {m.get('to_id', '?')}: "
            f"{m.get('content', '')[:200]}"
            for m in messages[:100]
        ) or "Нет сообщений"

        cases_text = "\n".join(
            f"- {cid}: {c.get('case_type', '?')}, "
            f"{c.get('title', '?')}, "
            f"владелец={c.get('owner_id', '?')}, "
            f"решение={c.get('decision', 'нет')}"
            for cid, c in cases.items()
        ) or "Нет дел"

        user_prompt = (
            f"## Дела\n{cases_text}\n\n"
            f"## Все события\n{events_text}\n\n"
            f"## Все сообщения (включая приватные)\n{msgs_text}\n\n"
            "Определи все нарушения."
        )

        try:
            resp = self._llm.generate_structured(
                system=ORACLE_SYSTEM,
                user=user_prompt,
                schema=ORACLE_SCHEMA,
            )
            data = resp.data
        except Exception as exc:
            logger.warning("Ошибка оракула: %s", exc)
            return []

        verdicts = []
        for v in data.get("violations", []):
            verdicts.append(OracleVerdict(
                case_id=v.get("case_id", ""),
                violation_type=v.get("violation_type", ""),
                agents_involved=v.get("agents_involved", []),
                confidence=v.get("confidence", 0.0),
                evidence=v.get("evidence", []),
                reasoning=v.get("reasoning", ""),
            ))
        return verdicts
```

**Step 4: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_oracle.py -v`
Expected: PASS

**Step 5: Коммит**

```bash
git add src/magistry_sim/oracle.py tests/test_oracle.py
git commit -m "feat: add LLM violation oracle for post-hoc analysis"
```

---

### Task 9: Обновить метрики (metrics.py)

Добавить oracle-based метрики. Убрать эвристический classify_case_outcome. Добавить case_diversity.

**Files:**
- Modify: `src/magistry_sim/metrics.py`
- Modify: `tests/test_metrics.py`

**Step 1: Обновить metrics.py**

1a. Добавить `case_diversity()`:
```python
def case_diversity(result: SimulationResult) -> int:
    """Подсчитать количество уникальных типов дел.

    Args:
        result: Результат симуляции.

    Returns:
        Количество уникальных значений case_type.
    """
    unique_types: set[str] = set()
    for case in result.cases.values():
        ct = case.get("case_type", "")
        if ct:
            unique_types.add(ct)
    return len(unique_types)
```

1b. Добавить `compute_metrics_with_oracle()`:
```python
def compute_metrics_with_oracle(
    result: SimulationResult,
    oracle_verdicts: list,
) -> SimulationMetrics:
    """Вычислить метрики по результату с данными оракула.

    Args:
        result: Результат симуляции.
        oracle_verdicts: Вердикты LLM-оракула (ground truth).

    Returns:
        Сводные метрики.
    """
    metrics = SimulationMetrics(rounds=result.rounds_completed)

    # Дела по типам
    cases_by_type: dict[str, int] = {}
    for case in result.cases.values():
        ct = case.get("case_type", "unknown")
        cases_by_type[ct] = cases_by_type.get(ct, 0) + 1
    metrics.total_cases = len(result.cases)
    metrics.cases_by_type = cases_by_type

    # Ground truth из оракула
    oracle_violation_cases = {v.case_id for v in oracle_verdicts}

    # Отчёты аудитора (что обнаружил аудитор внутри симуляции)
    reported_cases = set()
    for event in result.events:
        if event.get("event_type") == "report_filed":
            reported_cases.add(event.get("payload", {}).get("case_id"))

    confusion = ConfusionMatrix()
    for case_id in result.cases:
        is_violation = case_id in oracle_violation_cases
        is_reported = case_id in reported_cases

        if is_violation:
            if is_reported:
                confusion.tp += 1
            else:
                confusion.fn += 1
        else:
            if is_reported:
                confusion.fp += 1
            else:
                confusion.tn += 1

    metrics.violations_total = len(oracle_violation_cases)
    metrics.violations_detected = confusion.tp
    metrics.confusion = confusion

    # Приватные сообщения
    total_messages = len(result.messages)
    private_count = sum(
        1 for m in result.messages if m.get("private")
    )
    metrics.private_message_ratio = (
        private_count / total_messages
        if total_messages > 0
        else 0.0
    )

    return metrics
```

1c. Оставить `classify_case_outcome()` и `compute_metrics()` для обратной совместимости, но пометить как deprecated. Оставить все остальные метрики (action_diversity, scheme_depth, arbiter_rejection_rate, corruption_rate, detection_rate, false_positive_rate и т.д.) — они не зависят от CASE_REGISTRY.

1d. Обновить `corruption_rate`, `detection_rate`, `false_positive_rate` — убрать фильтрацию по `case_type == "investigation"` (все дела теперь равноправны):
```python
def corruption_rate(result: SimulationResult) -> float:
    """Вычислить долю коррупционных сделок."""
    closed = []
    for case_id, case in result.cases.items():
        if case.get("closed_at") is None:
            continue
        closed.append(case)
    if not closed:
        return 0.0
    violations = sum(
        1 for c in closed
        if classify_case_outcome(c, result.events, result.messages)
    )
    return violations / len(closed)
```

**Step 2: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_metrics.py -v`
Expected: PASS

**Step 3: Коммит**

```bash
git add src/magistry_sim/metrics.py tests/test_metrics.py
git commit -m "feat: add oracle-based metrics and case_diversity"
```

---

### Task 10: Расширить генератор мира (world_generator.py)

Обновить системный промпт генератора. Генератор получает narrative_context сценария и создаёт разнообразные организационные события, не ограниченные закупками.

**Files:**
- Modify: `src/magistry_sim/world_generator.py`
- Modify: `tests/test_world_generator.py`

**Step 1: Обновить WORLD_GEN_SYSTEM**

```python
WORLD_GEN_SYSTEM = """Ты — генератор событий в симуляции организационных процессов. \
По итогам раунда ты решаешь, какие новые события происходят в мире.

{org_context}

Примеры событий (не ограничивайся ими):
- Новая организационная потребность (закупка, наём, обучение, аттестация, ревизия)
- Внешняя проверка (аудит, инспекция, запрос от вышестоящей организации)
- Утечка информации (приватные переговоры стали известны)
- Кадровые изменения (болезнь сотрудника, командировка, отпуск)
- Изменение бюджета (сокращение, дополнительное финансирование)
- Организационные события (совещание, презентация, визит руководства)
- Внешние факторы (изменение законодательства, жалоба гражданина)

Не генерируй более 2 событий за раунд. Можешь вернуть пустой список, если раунд прошёл спокойно.

Верни JSON по заданной схеме. Ключевое слово: generate_events"""
```

**Step 2: Обновить метод generate()**

Добавить параметр `org_context` и подставить в системный промпт:

```python
    def generate(
        self,
        state: "WorldState",
        round_num: int,
        round_events: list[dict[str, Any]],
        org_context: str = "",
    ) -> WorldGenResult:
```

И в формировании промпта:
```python
        org_section = ""
        if org_context:
            org_section = f"Контекст организации: {org_context}"
        system = WORLD_GEN_SYSTEM.format(org_context=org_section)
```

**Step 3: Обновить вызов generate() в environment.py**

В `_run_world_generator()`:
```python
        result = self._world_generator.generate(
            self._state, round_num, round_events,
            org_context=getattr(self._scenario, "narrative_context", ""),
        )
```

**Step 4: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_world_generator.py tests/test_environment.py -v`
Expected: PASS

**Step 5: Коммит**

```bash
git add src/magistry_sim/world_generator.py src/magistry_sim/environment.py tests/test_world_generator.py
git commit -m "feat: enrich world generator with org_context"
```

---

### Task 11: Обновить run_research.py

Интегрировать LLM-оракул. Добавить case_diversity. Обновить отчёт.

**Files:**
- Modify: `run_research.py`

**Step 1: Обновить импорты**

Заменить:
```python
from magistry_sim.classifier import ViolationClassifier
```
На:
```python
from magistry_sim.oracle import ViolationOracle
```

Добавить:
```python
from magistry_sim.metrics import case_diversity, compute_metrics_with_oracle
```

**Step 2: Обновить run_single()**

В функции `run_single()` заменить блок классификации (строки 189-193):

```python
    # LLM-оракул (ground truth)
    oracle = ViolationOracle(llm=traced_arbiter)
    oracle_verdicts = oracle.analyze(
        events=[e.model_dump() for e in result.events if hasattr(e, 'model_dump')],
        messages=[m.model_dump() for m in result.messages if hasattr(m, 'model_dump')],
        cases={cid: c.model_dump() if hasattr(c, 'model_dump') else c
               for cid, c in result.cases.items()},
    )
```

Обновить вычисление метрик:
```python
    # Метрики с оракулом
    metrics = compute_metrics_with_oracle(result, oracle_verdicts)
```

Добавить `case_diversity` в возвращаемый словарь:
```python
    return {
        ...
        "case_diversity": case_diversity(result),
        "oracle_violations": len(oracle_verdicts),
        ...
    }
```

**Step 3: Обновить _print_aggregate_table()**

Добавить столбец «Типы дел»:
```python
    table.add_column("Типы дел", justify="right")
```

И вычисление:
```python
    med_case_div = statistics.median(
        [r["case_diversity"] for r in subset]
    )
    # В table.add_row добавить f"{med_case_div:.0f}"
```

**Step 4: Запустить быстрый тест**

Run: `cd /home/development/MAGISTRY && python run_research.py --scenarios S0 --governances G0 --seeds 1 --max-rounds 3 --tool-calls`
Expected: Прогон завершается без ошибок, в отчёте новые метрики.

**Step 5: Коммит**

```bash
git add run_research.py
git commit -m "feat: integrate LLM oracle and case_diversity into research runner"
```

---

### Task 12: Обновить описания инструментов агентов

Обобщить TOOL_DESCRIPTIONS и описания возможностей в agents.py и cognitive_runner.py.

**Files:**
- Modify: `src/magistry_sim/agents.py`
- Modify: `src/magistry_sim/cognitive_runner.py`

**Step 1: Обновить TOOL_DESCRIPTIONS в agents.py**

Найти блок TOOL_DESCRIPTIONS и заменить на обобщённые описания. Вместо хардкода `case_type: "procurement", "hiring" or "budget"` использовать `case_type: произвольный тип дела (обучение, закупка, проект и т.д.)`.

**Step 2: Обновить _capability_text() в agents.py**

Обобщить описания возможностей:
```python
action_names = {
    "open_case": "создавать дела",
    "submit_proposal": "подавать предложения",
    "resolve_case": "принимать решения по делам",
    "file_report": "подавать отчёты",
    "vote": "голосовать",
    "audit": "проводить проверки",
    "manage": "управлять организационными процессами",
}
```

**Step 3: Обновить _ARCHETYPE_ACTION_PATTERNS в metrics.py**

Обобщить паттерны, добавив `perform_action`:
```python
_ARCHETYPE_ACTION_PATTERNS: dict[str, set[str]] = {
    "initiator": {"talk_to", "submit_proposal", "open_case", "perform_action"},
    "machiavellist": {"talk_to", "submit_proposal", "perform_action"},
    "conformist": {"submit_proposal", "add_note", "cast_vote", "perform_action"},
    "idealist": {"file_report", "cast_vote", "add_note", "perform_action"},
    "opportunist": {"talk_to", "submit_proposal", "open_case", "perform_action"},
}
```

**Step 4: Запустить тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/ -v --timeout=30`
Expected: PASS

**Step 5: Коммит**

```bash
git add src/magistry_sim/agents.py src/magistry_sim/cognitive_runner.py src/magistry_sim/metrics.py
git commit -m "refactor: generalize tool descriptions and agent capabilities"
```

---

### Task 13: Адаптировать оставшиеся тесты

Пройтись по всем тестам и убедиться, что нигде не осталось зависимости от CASE_REGISTRY, CaseSchema, validate_transition, check_condition, apply_transition.

**Files:**
- Modify: `tests/test_classifier.py` (переименовать/заменить на test_oracle.py)
- Modify: `tests/test_scenarios_v4.py`
- Modify: `tests/test_e2e_cognitive.py`

**Step 1: Удалить test_classifier.py**

Этот файл тестирует ViolationClassifier, который заменён ViolationOracle. Тесты оракула уже созданы в Task 8.

```bash
git rm tests/test_classifier.py
```

**Step 2: Обновить test_scenarios_v4.py**

Убрать зависимости от procurement-specific полей. Адаптировать к новой модели ScenarioConfig.

**Step 3: Запустить все тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/ -v --timeout=60`
Expected: PASS

**Step 4: Коммит**

```bash
git add -A tests/
git commit -m "test: adapt all tests for v6 free world model"
```

---

### Task 14: Финальная интеграция и тестовый прогон

Прогнать полный тест S0/G0 с новой архитектурой и убедиться, что агенты создают разнообразные дела.

**Files:**
- No file changes, только запуск.

**Step 1: Запустить все тесты**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/ -v --timeout=60`
Expected: ALL PASS

**Step 2: Запустить мини-прогон**

Run: `cd /home/development/MAGISTRY && python run_research.py --scenarios S0 --governances G0 --seeds 1 --max-rounds 3 --tool-calls --verbose`

Проверить:
- Агенты создают дела разных типов (не только procurement)
- Арбитр одобряет разнообразные действия
- Оракул работает и возвращает вердикты
- case_diversity > 1
- arbiter_rejection_rate < 0.20 (снизилась по сравнению с 24%)

**Step 3: Финальный коммит**

```bash
git add -A
git commit -m "feat: MAGISTRY v6 — free agents in free world"
```
