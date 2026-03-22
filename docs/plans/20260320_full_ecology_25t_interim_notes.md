# Промежуточные заметки по прогону `full_ecology_25t_20260319_try1`

## Контекст

- Каталог прогона: `results/full_ecology_25t_20260319_try1`
- Сценарий: `scenarios/procurement_tender_full_ecology.yaml`
- Конфиг LLM из `.env`: OpenRouter (`OPENAI_BASE_URL=https://openrouter.ai/api/v1`), модель `openai/gpt-oss-120b`, routing через `provider_order=["Groq"]`
- Состояние на момент фиксации заметки: прогон на ноутбуке был оборван выключением машины. `status.json` остался в состоянии `running`, `pid=18068`, `tick=17`, но соответствующий процесс больше не существует, а финальные sidecar-файлы (`summary.json`, `evaluation.json`, `fidelity.json`) не были записаны.

## Промежуточный разбор прогона

По оставшимся артефактам незавершённого прогона:

- `events.jsonl`: 684 событий
- `truth.jsonl`: 14 truth-records
- `trace.jsonl`: 227 traced LLM spans
- `audit_flagged`: 31
- `audit_case_opened`: 16
- `audit_case_updated`: 19
- `audit_case_closed`: 16
- `review_case_opened`: 7
- `review_case_closed`: 7
- `arbiter_op_failed`: 18

Промежуточная evaluation по уже записанным данным (вычислена вручную по имеющимся `events.jsonl` + `truth.jsonl`, так как штатный sidecar не успел записаться):

- strict: `precision=0.0`, `recall=0.0`, `f1=0.0`
- semantic: `precision=0.3226`, `recall=0.7143`, `f1=0.4444`

Текущий truth-layer:

- `non_escalation_under_pressure`: 5
- `support_vote_after_private_contact`: 5
- `preferential_treatment_for_connected_actor`: 3
- `partial_disclosure_under_deadline_pressure`: 1

Текущий runtime-audit:

- `preferential_treatment_for_connected_actor`: 13
- `support_vote_after_private_contact`: 9
- `non_escalation_under_pressure`: 6
- `partial_disclosure_under_deadline_pressure`: 3

Промежуточный вывод:

- runtime-аудитор уже заметно лучше покрывает canonical labels, чем старый контур без нормализации и case aggregation;
- strict exact-match остаётся слишком хрупким;
- semantic matching уже даёт осмысленный сигнал и лучше отражает фактическую близость finding’ов.

## Наблюдения по latency

### 1. Узкое место не похоже на RPS saturation OpenRouter

По `results/llm_debug.jsonl`:

- записано 19k+ provider-debug записей;
- зафиксирован только один retried `JSONDecodeError`;
- нет волны `429`, `APIConnectionError`, `APITimeoutError` или массовых retries.

Это больше похоже не на нехватку пропускной способности OpenRouter, а на long-tail latency одного provider/model path.

### 2. Основной симптом — отдельные очень медленные успешные вызовы

Самые медленные spans на текущем префиксе прогона:

- `agent:deputy`, tick 11: ~145 s
- `memory` / `agent:spec`, tick 15: ~144 s
- `memory` / `agent:sec_vladimir_sergeevich_volkov`, tick 16: ~141 s
- `runtime_auditor`, tick 4: ~139 s
- `memory` / `agent:head`, tick 16: ~128 s
- `agent:deputy`, tick 9: ~125 s

Это именно успешные вызовы, а не retry storm.

### 3. Prompt sizes уже достаточно велики для latency-tail

Средние traced значения:

- `agent`: ~11.2k prompt tokens, ~10.3 s mean span
- `auditor`: ~37.2k prompt tokens, ~23.8 s mean span
- `memory`: ~3.0k prompt tokens, ~16.2 s mean span

Максимумы:

- `agent`: 22.9k prompt tokens
- `auditor`: 50.1k prompt tokens

Для `gpt-oss-120b` это не экстремальный throughput-case, но вполне достаточный объём, чтобы ловить длинные latency tails на structured/tool-calling запросах.

### 4. Часть wall-time не видна в `trace.jsonl`

`TraceLog` покрывает только `LLMCaller.generate*`. Вне trace остаются реальные embedding HTTP calls:

- query embedding в `agent._render_memory()`
- batch embeddings в `_update_agent_memory()`

Так как embeddings идут через OpenRouter/OpenAI-compatible endpoint и не трассируются в `trace.jsonl`, наблюдаемые “пустые” промежутки между LLM spans, вероятно, частично объясняются именно ими.

### 5. Есть архитектурные усилители медленности

- В agent prompt без компактного summary попадают последние 50 событий, включая тяжёлые payload’ы `audit_case_updated` и `arbiter_op_failed`.
- В auditor prompt уходит большой стек: `state_snapshot`, `open_audit_cases`, `pending_obligations`, `current_tick_events`, `recent_events`, `private_contact_pairs`.
- `memory`-суммаризация после тика идёт последовательно по агентам.
- `provider_order=["Groq"]` означает, что OpenRouter не занимается latency-aware balancing: slow, but successful Groq response не будет автоматически заменён на другой backend.

## Гипотезы о природе зависаний

Наиболее вероятная комбинация:

1. long-tail latency конкретного path `OpenRouter -> Groq -> openai/gpt-oss-120b`;
2. слишком длинный provider timeout (`120s`) без fail-fast на agent/memory roles;
3. неучтённые embedding RTT вне `trace.jsonl`;
4. prompt bloat из-за сырого event replay и case payload’ов.

Менее вероятная гипотеза:

- “модель ушла в прострацию” как runaway generation. Completion lengths умеренные, structured output в целом валиден, retry storm почти отсутствует.

## Что пробовать дальше

1. Ввести реальные per-role timeout на уровне провайдера, а не только `asyncio.wait_for`.
2. Для agent/memory ролей убрать жёсткий pin на Groq и добавить latency-aware routing или fallback model.
3. Сильно ужать agent facts и auditor context.
4. Добавить trace/debug для embeddings.
5. Разнести модели по ролям:
   - `agent` / `memory`: более быстрая модель;
   - `auditor`: либо текущая модель с меньшим context budget, либо отдельная faster structured model.
