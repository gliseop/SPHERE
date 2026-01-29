# План работ по ВКР и прототипу: агентно‑имитационное моделирование AI+DAO для кейса госзакупок

## 0) Зафиксированные вводные (из `CONCEPT.README.md` и обсуждения)
- **Дедлайн сдачи/защиты:** `2026-05-01`.
- **Итог:** текст ВКР **+ минимальный прототип**, но с **тремя механизмами**: AI‑аудит + репутация + голосование (mock‑DAO).
- **Кейс эксперимента:** **госзакупки/контракты**.
- **Фокус оценки:** **коррупция/сговор**, но прогоняем **несколько сценариев**.
- **Масштаб прогона:** **20–50 агентов**.
- **Данные:** **синтетика + правила генерации**.
- **LLM:** **cloud API (OpenAI‑совместимый)**.
- **UI:** в MVP достаточно CLI+отчётов; **реалтайм‑3D граф — stretch goal после MVP**.

---

## 1) Результаты, которые должны получиться (критерии готовности)

### 1.1. ВКР (текст)
- Чёткая постановка задачи: проблема принципала‑агента + стоимость контроля + коррупционные сети.
- Формализация: что именно считаем “гибридной системой управления (AI+DAO)” в рамках работы (механизмы, ограничения, “иммунитет топ‑уровня”).
- Методология имитационного эксперимента: параметры, метрики, сценарии, сравниваемые режимы, воспроизводимость.
- Экспериментальные результаты: таблицы/графики по метрикам, интерпретация, ограничения.
- Раздел по этике/рискам: приватность, злоупотребления наблюдением, ложноположительные, “capture” системы.

### 1.2. Прототип (репозиторий)
- Запуск одной командой: симуляции → артефакты → отчёт.
- Повторяемость: фиксируем `seed`, конфиг, версии зависимостей, логи prompt/response LLM.
- Сравнение режимов управления (минимум 4): `baseline`, `audit_only`, `audit+rep`, `audit+rep+vote`.
- Минимум 2 сценария атак/сговора с измеримыми эффектами.
- Набор unit‑тестов, где LLM заменён на детерминированный `FakeLLM`.

---

## 2) Архитектура прототипа (decision-complete)

### 2.1. Структура репозитория (создаём с нуля)
- `pyproject.toml` (Python 3.11+), зависимости, entrypoints
- `README.md` (как запускать)
- `configs/`
  - `configs/base.yaml`
  - `configs/scenarios/collusion.yaml`
  - `configs/scenarios/evasion.yaml`
  - `configs/modes/baseline.yaml`
  - `configs/modes/audit_only.yaml`
  - `configs/modes/audit_rep.yaml`
  - `configs/modes/audit_rep_vote.yaml`
- `src/magistry_sim/`
  - `__init__.py`
  - `cli.py`
  - `config.py`
  - `rng.py`
  - `schemas.py`
  - `events.py`
  - `world.py`
  - `agents.py`
  - `llm.py`
  - `memory.py`
  - `audit.py`
  - `reputation.py`
  - `dao.py`
  - `metrics.py`
  - `runner.py`
  - `report.py`
  - `viz_export.py`
- `tests/`
  - `test_reputation.py`
  - `test_audit_risk.py`
  - `test_dao_vote.py`
  - `test_runner_determinism.py`
  - `fixtures/` (мини-конфиги)
- `runs/` (в `.gitignore`, сюда складываются результаты)

### 2.2. Главный контракт ввода/вывода
**Вход:** YAML‑конфиг + seed

**Выход (в `runs/<run_id>/`):**
- `config_resolved.yaml`
- `events.jsonl` (единый журнал событий)
- `audit_flags.jsonl`
- `metrics.json`
- `graph.graphml` (соцграф + связи “агент–контракт/поставщик”)
- `report.md` (человеко‑читаемый отчёт)
- `figures/*.png` (графики метрик)

### 2.3. CLI (точные команды)
Entry point: `python -m magistry_sim.cli`

Команды:
- `magistry-sim run --config configs/base.yaml --seed 42`
- `magistry-sim sweep --config configs/base.yaml --seeds 1,2,3,4,5`
- `magistry-sim compare --run-ids <id1,id2,...>` (сводная таблица по режимам)

---

## 3) Модель симуляции (минимально достаточная, но “живая”)

### 3.1. Тип симуляции
- Дискретное время: `tick = 1 день`, горизонт по умолчанию `T=180` (полгода).
- Событийная модель поверх тиков: в каждый tick генерируются и исполняются события.

### 3.2. Сущности и схемы (в `src/magistry_sim/schemas.py`)
Pydantic‑модели (точные имена):
- `AgentId = str`, `OrgId = str`, `ContractId = str`, `SupplierId = str`
- `class AgentProfile(BaseModel)`:
  - `id: AgentId`
  - `role: Literal["procurement_officer","manager","auditor","supplier","citizen_observer"]`
  - `alignment: Literal["honest","opportunist","corrupt"]`
  - `competence: float` (0..1)
  - `risk_tolerance: float` (0..1)
  - `reputation: int` (стартовое значение)
- `class SocialEdge(BaseModel)`:
  - `src: AgentId`
  - `dst: AgentId`
  - `type: Literal["education","family","ex_colleague","business"]`
  - `strength: float` (0..1)
- `class Tender(BaseModel)`:
  - `id: ContractId`
  - `budget: int`
  - `category: str`
  - `created_day: int`
  - `status: Literal["announced","bidding","awarded","executed","closed"]`
  - `winner_supplier_id: SupplierId | None`
- `class AuditFlag(BaseModel)`:
  - `day: int`
  - `subject: Literal["tender","agent","supplier","edge"]`
  - `subject_id: str`
  - `risk_score: float` (0..1)
  - `reasons: list[str]`
- `class Proposal(BaseModel)`:
  - `id: str`
  - `day: int`
  - `type: Literal["open_investigation","freeze_tender","remove_officer","escalate_to_board"]`
  - `payload: dict`
- `class VoteRecord(BaseModel)`:
  - `proposal_id: str`
  - `day: int`
  - `eligible_voters: list[AgentId]`
  - `tally_yes: int`
  - `tally_no: int`
  - `outcome: Literal["passed","rejected"]`

### 3.3. Агенты и принятие решений
`src/magistry_sim/agents.py`
- Базовый класс: `class Agent:` с методами
  - `def step(self, world: "World", day: int) -> list["Event"]: ...`
  - `def observe(self, event: "Event") -> None: ...`
- Решения, где нужна “человечность”, делаем через LLM **строго в JSON**:
  - переговоры поставщик↔чиновник
  - попытки сговора/обмана
  - оправдания/объяснения при проверках
- Все “дорогие” LLM‑вызовы идут через `LLMClient` и логируются.

### 3.4. LLM-обвязка (в `src/magistry_sim/llm.py`)
- Интерфейс:
  - `class LLMClient(Protocol):`
    - `def complete_json(self, *, system: str, user: str, schema: dict, temperature: float) -> dict: ...`
- Реализации:
  - `class OpenAICompatibleLLM(LLMClient)` (base_url, api_key, model)
  - `class FakeLLM(LLMClient)` (для тестов, детерминированный)

### 3.5. Память агентов (в `src/magistry_sim/memory.py`)
MVP: “рабочая” память без полноценной векторной БД:
- `class AgentMemory:`
  - `def add(self, day: int, text: str, tags: dict) -> None`
  - `def recall(self, query: str, k: int = 5) -> list[str]` (BM25/простая эвристика)
- В тексте ВКР отдельно описываем, как это расширяется до embeddings/vector DB.

---

## 4) Три механизма управления (AI+DAO)

### 4.1. AI‑аудитор (в `src/magistry_sim/audit.py`)
- Вход: `World` + `events.jsonl` (в памяти в раннере)
- Выход: список `AuditFlag` + “сводка дня” (текст/структура)
- Риск‑скоринг (на базе LLM):
  - повторяющиеся победы одного поставщика
  - связь “чиновник↔поставщик” по соцграфу
  - аномально высокий бюджет относительно категории
  - ускоренные/обходные процедуры
- Триггеры: если `risk_score >= threshold`, создаём `Proposal`.

### 4.2. Репутация (в `src/magistry_sim/reputation.py`)
- Правило: репутация **не уменьшается**, но **скорость набора** может стать 0.
- API:
  - `def update_reputation(world: World, day: int, flags: list[AuditFlag]) -> None`
- Эффект:
  - право инициировать/участвовать в голосовании зависит от `reputation >= X`
  - продвижение на роли “manager/procurement_officer” требует порога репутации

### 4.3. DAO‑голосование (mock) (в `src/magistry_sim/dao.py`)
- Цель: продемонстрировать **неподделываемость результата** в модели и “анонимность как допущение”.
- Реализация:
  - `def run_vote(world: World, proposal: Proposal, day: int) -> VoteRecord`
  - Выборка голосующих: `eligible_voters` по роли/репутации
  - Модель “анонимности”: в `events.jsonl` пишем только “ballot_cast”, без ID, а в памяти раннера храним map для тестов (и это явно описываем как упрощение).
- После принятия:
  - `freeze_tender` блокирует контракт
  - `open_investigation` повышает интенсивность аудита по субъекту
  - `remove_officer` лишает агента полномочий (но не “наказывает” репутацией)

---

## 5) Сценарии атак (минимум 2) и режимы сравнения

### 5.1. Сценарий 1: “Сговор офицера и поставщика” (`configs/scenarios/collusion.yaml`)
- 1–2 коррупционных офицера, 2–3 аффилированных поставщика.
- Поведение: подгон условий тендера, дробление лотов, повторяемые победы.
- Ожидание: без AI+DAO растут потери бюджета и низкая выявляемость.

### 5.2. Сценарий 2: “Обход аудита / маскировка сети” (`configs/scenarios/evasion.yaml`)
- Коррупционные агенты пытаются:
  - переключаться между поставщиками (rotation)
  - создавать “буферных” посредников (доп. узлы поставщиков)
  - вести переговоры “в обход” (в симуляции — скрытые сообщения, которые оставляют меньше цифровых следов)
- Ожидание: AI‑аудит должен компенсировать через соцграф/аномалии, но с ростом false positives.

### 5.3. Режимы управления (минимум 4)
- `baseline`: нет аудита, нет репутации, нет голосования
- `audit_only`: есть флаги, но нет системного последствия
- `audit_rep`: последствия через остановку роста репутации/гейтинг ролей
- `audit_rep_vote`: + предложения и коллективные решения

---

## 6) Метрики и отчётность (в `src/magistry_sim/metrics.py` и `report.py`)
Метрики (все считаются по run):
- `diverted_funds_total`
- `diverted_funds_rate`
- `detections_true_positive`, `detections_false_positive`
- `mean_time_to_detection_days`
- `tenders_frozen_count`
- `investigations_opened_count`
- `corrupt_agents_blocked_from_promotion_count`
- `decision_latency_days` (от флага до исхода голосования/действия)

`report.md` должен содержать:
- краткое описание режима/сценария
- таблицу метрик
- графики: потери бюджета по времени, число флагов, FP/TP, latency
- “качественный кусок”: 1–2 примера цепочек событий (из `events.jsonl`) как narrative

---

## 7) Тестирование (pytest) — что именно проверяем
- `test_reputation.py`: репутация не убывает; флаг останавливает рост.
- `test_audit_risk.py`: известная синтетическая сеть → ожидаемый `risk_score` и `reasons`.
- `test_dao_vote.py`: корректный подсчёт, пороги принятия.
- `test_runner_determinism.py`: при `FakeLLM` и фиксированном seed метрики совпадают.

---

## 8) Stretch goal: реалтайм‑3D визуализация (после MVP, но уже спроектирована)

### 8.1. Поток данных
- Симулятор пишет события в `events.jsonl` и параллельно может стримить их через WebSocket.

### 8.2. Backend (Python)
- `src/magistry_sim/server.py` (FastAPI):
  - `GET /api/runs`
  - `GET /api/runs/{run_id}/metrics`
  - `WS /ws/runs/{run_id}` (события в реальном времени)

### 8.3. Frontend (React + 3D граф)
Папка: `ui/` (Vite)
- Библиотека 3D графа: `react-force-graph-3d`
- Фичи:
  - узлы: агенты, поставщики, тендеры
  - рёбра: соцсвязи, “выиграл контракт”, “коммуникация”
  - таймлайн: ползунок по дню + live‑режим
  - клик по узлу → панель детализации (профиль, репутация, флаги, участие в голосованиях)

---

## 9) План-график до `2026-05-01` (с чёткими выходами по этапам)

### Этап A — Спецификация и каркас (до `2026-02-07`)
- Создать структуру проекта, CLI, конфиги, схемы событий/сущностей.
- Заглушки агентов + генератор синтетики.
- Выход: `magistry-sim run` даёт `events.jsonl` + пустые метрики.

### Этап B — AI‑аудит + базовые метрики (до `2026-02-21`)
- Реализовать risk rules, `AuditFlag`, экспорт графа.
- Выход: флаги появляются в сценарии сговора; метрики считаются.

### Этап C — Репутация + гейтинг ролей (до `2026-03-07`)
- Репутационный рост, остановка роста, пороги полномочий.
- Выход: в `audit_rep` коррупционные агенты “застревают” без продвижения.

### Этап D — Mock‑DAO голосование + действия (до `2026-03-21`)
- Proposal‑pipeline от аудитора → голосование → действие в мире.
- Выход: в `audit_rep_vote` снижается ущерб/ускоряется выявление.

### Этап E — Второй сценарий (evasion) + sweep/compare (до `2026-04-04`)
- Добавить evasion‑сценарий, многосидовый прогон, сводный отчёт.
- Выход: `sweep` и `compare` строят сравнительные таблицы.

### Этап F — Эксперименты и текст ВКР (до `2026-04-25`)
- Запустить планируемые серии прогонов (5 seeds × 4 режима × 2 сценария).
- Сформировать графики/таблицы и вставить в текст.
- Выход: готовые “Результаты экспериментов” + “Обсуждение/ограничения”.

### Этап G — Полировка и (опционально) UI прототип (до `2026-04-30`)
- Мини‑дашборд/3D граф (если успеваем без риска для текста).
- Финальная проверка воспроизводимости, README, оформление.

---

## 10) Каркас ВКР (структура глав, чтобы совпало с прототипом)
- Глава 1: проблема принципала‑агента, контроль/коррупция, ограничения иерархий.
- Глава 2: концепт AI‑аудит + репутация + DAO (включая “иммунитет топ‑уровня” как допущение/ограничение).
- Глава 3: методология LLM‑агентов, архитектура симулятора, дизайн эксперимента (режимы/сценарии/метрики).
- Глава 4: результаты, сравнение режимов, разбор кейсов, чувствительность к параметрам.
- Заключение: выводы, применимость, риски, что делать дальше (векторная память, криптография голосований, реальные данные).

