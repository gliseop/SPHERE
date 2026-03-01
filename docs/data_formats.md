# Форматы данных

Описание форматов данных, используемых в MAGISTRY: шаблоны агентов, архетипы личности, интервью, сценарии и результаты.

## Типы агентов (`data/agent_types/`)

JSON-файлы, определяющие шаблоны для создания агентов. Каждый файл содержит идентификатор, описание, набор полномочий и начальные ресурсы.

```json
{
  "id": "auditor",
  "name": "Аудитор",
  "description": "Проводит проверки и подаёт отчёты по делам.",
  "id_prefix": "aud",
  "capabilities": [
    { "action": "audit", "case_types": [] },
    { "action": "file_report", "case_types": [] }
  ],
  "resources": {
    "budget_limit": 0,
    "staffing_slots": 0,
    "contract_capacity": 0
  }
}
```

Доступные типы: `auditor`, `business_contractor`, `juror`, `official_procurement`.

| Поле | Тип | Описание |
|---|---|---|
| `id` | строка | Уникальный идентификатор типа |
| `name` | строка | Человекочитаемое название |
| `description` | строка | Описание роли |
| `id_prefix` | строка | Префикс для идентификаторов агентов |
| `capabilities` | массив | Полномочия: `action` (действие) и `case_types` (типы дел) |
| `resources` | объект | Начальные ресурсы (бюджет, штат, контракты) |

## Архетипы личности (`data/personalities/`)

JSON-файлы с профилями по HEXACO, Dark Triad и техниками нейтрализации.

```json
{
  "id": "pragmatist",
  "name": "Прагматик-исполнитель",
  "description": "Держит баланс между правилами и реальностью.",
  "prototypes": ["Макс Вебер", "Дуайт Эйзенхауэр"],
  "biography": "Вы цените стабильность и управляемость...",
  "hexaco": {
    "honesty_humility": 62,
    "emotionality": 48,
    "extraversion": 52,
    "agreeableness": 58,
    "conscientiousness": 70,
    "openness": 46
  },
  "dark_triad": {
    "narcissism": 28,
    "machiavellianism": 35,
    "psychopathy": 18
  },
  "neutralization_techniques": ["defense_of_necessity"]
}
```

Доступные архетипы: `machiavellist`, `opportunist`, `pragmatist`, `reformer`, `technocrat`.

| Поле | Тип | Описание |
|---|---|---|
| `hexaco` | объект | Шесть факторов HEXACO (0–100) |
| `dark_triad` | объект | Маккиавеллизм, нарциссизм, психопатия (0–100) |
| `neutralization_techniques` | массив | Техники нейтрализации по Sykes & Matza |
| `prototypes` | массив | Исторические прототипы для контекста |
| `biography` | строка | Биографическое описание для промпта |

## Интервью (`data/interviews/`)

JSON-файлы с результатами нарративных интервью. Содержат ответы на 30 вопросов по 8 доменам, сгенерированные LLM на основе профиля личности.

```json
{
  "id": "pragmatist",
  "archetype": "pragmatist",
  "role": "чиновник",
  "hexaco": { ... },
  "dark_triad": { ... },
  "interview": {
    "Опишите обычный день из вашей жизни...": "Мой обычный день начинается...",
    "Как вы принимаете решения под давлением...": "Когда на работе возникает давление..."
  }
}
```

Восемь доменов вопросов: повседневная жизнь, мотивация, финансы, лояльность, правила и принципы, межличностные отношения, давление и манипуляции, самооценка.

Файлы интервью используются `InterviewFragmentIndex` для fragment-based retrieval: ответы индексируются и извлекаются по релевантности при принятии агентом решений.

## Сценарии (`scenarios/`)

JSON-файлы с полной конфигурацией сценария для запуска через веб-интерфейс.

```json
{
  "id": "seed_s0_g0",
  "name": "Чистая сделка без контроля",
  "description": "Контрольный прогон: закупка серверного оборудования...",
  "narrative_context": "Государственное учреждение с прозрачными процедурами.",
  "scenario": "S0",
  "governance": "G0",
  "rounds": 15,
  "seed": 1,
  "runner": "cognitive",
  "agents": [
    {
      "id": "off_1",
      "name": "Иванов А.П.",
      "role": "official",
      "position": "начальник отдела обеспечения",
      "initial_reputation": 8.0,
      "greed": 0.2,
      "fear": 0.5,
      "honesty": 0.8
    }
  ]
}
```

| Поле | Тип | Описание |
|---|---|---|
| `scenario` | строка | Идентификатор базового сценария (S0–S6) |
| `governance` | строка | Режим управления (G0–G3) |
| `rounds` | целое | Количество раундов |
| `seed` | целое | Зерно генератора случайных чисел |
| `runner` | строка | Тип исполнителя (`cognitive`, `llm`) |
| `agents` | массив | Профили агентов с полномочиями и параметрами |
| `narrative_context` | строка | Описание контекста для промптов |

Сценарии могут также содержать поля `needs` (потребности организации), `connections` (начальные связи), `personality_archetype` (архетип для агента) и другие параметры из `ScenarioConfig`.

## Результаты (`results/`)

### Журнал событий (JSONL)

Каждая строка — JSON-объект с событием симуляции:

```json
{"type": "case_opened", "agent_id": "off_1", "case_id": "CASE-001", "case_type": "procurement", "round": 2, "timestamp": "2026-02-17T10:30:00+03:00"}
{"type": "proposal_submitted", "agent_id": "biz_1", "case_id": "CASE-001", "round": 3}
{"type": "message_sent", "from": "off_1", "to": "biz_1", "private": true, "round": 4}
{"type": "case_resolved", "agent_id": "off_1", "case_id": "CASE-001", "decision": "Выбран biz_1", "round": 6}
```

Типы событий: `case_opened`, `case_resolved`, `proposal_submitted`, `note_added`, `report_filed`, `vote_cast`, `message_sent`, `move`, `idle`, `reputation_frozen`, `tribunal_formed`, `need_generated`.

### Артефакты (`results/artifacts/`)

Сгенерированные документы (через `DocumentForge`) и другие файлы, привязанные к прогонам.

### Журнал LLM (`results/llm_debug.jsonl`)

Журнал всех LLM-вызовов для отладки: промпты, ответы, длительность, стоимость.

## Режимы управления (`data/governance_modes/`)

Директория для хранения пользовательских конфигураций режимов управления в формате JSON. Создаётся автоматически; заполняется через веб-интерфейс.
