# План ночного прогона G0-G3 для главы 3 ВКР

**Дата составления:** 2026-04-14
**Ветка:** `feat/enchance-v2` (коммит `bed371a` и новее)
**Цель:** Собрать эмпирические данные для сравнительного анализа governance-режимов (G0, G1, G2, G3) на расширенном сценарии с 20+ агентами.

---

## 1. Контекст и требования

### Что уже проверено
- Smoke 5 тиков (20 агентов, G3): 0 ошибок движка, fidelity чистый, collegial review работает
- Все 322 unit-теста проходят
- Persona enrichment × 20 агентов ≈ 23 мин на tick 0
- Regular tick с 20 агентами ≈ 8-12 мин (sequential arbiter — главный bottleneck)

### Ожидаемая длительность и стоимость
| Конфиг | Wall time | LLM calls | Токены | Стоимость (OpenRouter nemotron) |
|---|---|---|---|---|
| 1 прогон (25 тиков, 20 агентов, с enrichment) | ~13-14 часов | ~1975 | ~20M | ~$3.30 |
| G0-G3 серия × 1 repeat | ~55-60 часов | ~7900 | ~80M | ~$13 |
| G0-G3 серия × 3 repeats | ~7-8 суток | ~24000 | ~240M | ~$40 |

**Рекомендация:** сначала 1 полный G3 прогон (≤14 ч), потом серия G0-G3 × 1 repeat.

---

## 2. Окружение

### Зависимости
```bash
# Python 3.11+
pip install -e .  # установить проект
```

### Env vars (`.env` в корне репозитория)
```bash
# Обязательно:
OPENAI_API_KEY=sk-or-v1-...                  # OpenRouter API key
OPENAI_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b

# Опционально (но рекомендуется):
SPHERE_LLM_MAX_RETRIES=3                     # default 2
SPHERE_LLM_REQUEST_TIMEOUT_S=120.0           # default 30 — большие schemas требуют больше
SPHERE_LLM_CALL_DEADLINE_S=180.0             # default 30 — аналогично
```

### Проверка окружения перед стартом
```bash
cd /path/to/project
python -c "from dotenv import load_dotenv; load_dotenv(); import os; print('API_KEY:', bool(os.getenv('OPENAI_API_KEY'))); print('BASE_URL:', os.getenv('OPENAI_BASE_URL'))"
python -m pytest tests/ -x -q   # должно быть 322 passed
```

Если тесты падают — **не запускать прогон**, сначала разобраться.

---

## 3. Команды запуска

### Вариант A: один прогон G3 (рекомендовано начать с него)
```bash
cd /path/to/project
python -m sphere_lc.cli run \
  --scenario scenarios/procurement_tender_large.json \
  --governance G3 \
  --ticks 25 \
  --out results/overnight_g3 \
  > results/overnight_g3.log 2>&1 &

# PID сохранить:
echo $! > results/overnight_g3.pid
```

**Проверка живости процесса:**
```bash
cat results/overnight_g3/status.json      # state + текущий tick
ps -p $(cat results/overnight_g3.pid)     # жив ли процесс
wc -l results/overnight_g3/events.jsonl   # растёт ли лог событий
```

### Вариант B: полная серия G0-G3
После успешного G3 (или параллельно на другом сервере):
```bash
python scripts/run_chapter2_baseline.py \
  --scenario scenarios/procurement_tender_large.json \
  --governance G0 G1 G2 G3 \
  --repeats 1 \
  --ticks 25 \
  --out results/overnight_g0_g3_series \
  2> results/overnight_g0_g3_series.log &
```

Прогресс-бар из скрипта идёт в **stderr**, итоговый JSON — в **stdout**.

---

## 4. Мониторинг

### Каждые 30-60 мин проверять:
1. **Статус процесса:** `ps -p <PID>` (жив?) + `cat results/overnight_*/status.json` (не зависло?)
2. **Прогресс тиков:** `status.json` → `tick` должен расти. Нормальная скорость: ~10 мин/тик после tick 0
3. **Нет ли critical errors:**
   ```bash
   grep -c '"event_type":"agent_llm_error"' results/overnight_*/events.jsonl
   grep -c '"event_type":"audit_runtime_error"' results/overnight_*/events.jsonl
   ```
   Допустимо: 0-5 за весь прогон. Больше 20 — останавливать и разбираться.
4. **Токены/стоимость:** Можно прикинуть по perf_summary.json после каждых 5 тиков

### Признаки проблем
- Tick не меняется 30+ мин → LLM завис или провайдер упал
- `state=failed` в status.json → прогон крашнулся, читать `.log`
- Процент rejection rate > 40% на стабильных тиках (1-2, 3+) → деградация LLM

### Что делать при сбое
1. Если процесс упал — **не перезапускать слепо**. Прочитать `.log`, понять причину
2. Если сетевой флап — дождаться восстановления, процесс сам не восстановится (нужен рестарт)
3. Если provider throttling (429) — увеличить `SPHERE_LLM_RETRY_BASE_DELAY_S` или подождать
4. Если schema validation fail повторяется — собрать events за последние 2 тика для дебага

---

## 5. Выходные артефакты

После успешного прогона в `results/overnight_g3/` (или `results/overnight_g0_g3_series/{scenario}_{mode}_run1/`):

| Файл | Что содержит | Критичность для ВКР |
|---|---|---|
| `events.jsonl` | Полный лог событий симуляции | **Обязательно** — основа анализа |
| `truth.jsonl` | Deterministic ground truth violations | **Обязательно** — метрики P/R/F1 |
| `truth_freeform.jsonl` | LLM-based freeform violations | Желательно (дополнительный слой) |
| `evaluation.json` | strict + semantic + case-level метрики | **Обязательно** — ключевые цифры |
| `fidelity.json` | Метрики правдоподобия симуляции | **Обязательно** — подтверждение реализма |
| `summary.json` | Сводка всех метрик | Обязательно — удобная сводка |
| `world_history.md` | Нарративная история мира | Желательно — для глав/illustrations |
| `perf_summary.json` | Детализация по LLM-фазам | Полезно — для описания cost/perf |
| `trace.jsonl` | Все LLM промпты/ответы | Полезно — для дебага/воспроизводимости |
| `personas.json` | Обогащённые персоны агентов | Полезно — для описания методологии |
| `names.json` | Реестр имён агентов | Для таблиц в ВКР |
| `environment_timeline.jsonl` | Динамика environment по тикам | Полезно — для графиков |

### Для серии G0-G3 дополнительно
- `comparative_report.json` — агрегированная сводка всех модов → главная таблица для ВКР

---

## 6. Ключевые проверки после прогона

### Sanity checks
```bash
cd results/overnight_g3
python -c "
import json
# 1. Прогон завершён?
s = json.load(open('status.json'))
assert s['state'] == 'finished', f'Not finished: {s}'

# 2. Достигли последнего тика?
assert s['tick'] == 24, f'Wrong tick: {s[\"tick\"]}'

# 3. Есть труты и сигналы?
truth = sum(1 for _ in open('truth.jsonl'))
print(f'Truth records: {truth}')
assert truth > 0, 'No truth records — audit/truth detector не сработал'

# 4. Fidelity чистый?
fid = json.load(open('fidelity.json'))
problems = [
    ('temporal', fid['temporal_violations_total']),
    ('machine_names', fid['identity_machine_name_total']),
    ('narrating', fid['narrating_leakage_total']),
]
for name, count in problems:
    if count > 0:
        print(f'WARNING: {name}_violations={count} (ожидали 0)')

# 5. Governance метрики
ev = json.load(open('evaluation.json'))
print(f'P/R/F1: {ev[\"precision\"]:.2f}/{ev[\"recall\"]:.2f}/{ev[\"f1\"]:.2f}')
print(f'Truth={ev[\"truth_total\"]} flagged={ev[\"runtime_flagged_total\"]}')
print('OK')
"
```

### Ожидаемые значения (ориентир, могут варьироваться)
- **Fidelity:** temporal/machine_names/phantom/narrating = 0 (критично)
- **G3 F1:** 0.6-1.0 (должен быть выше чем G0=0)
- **perform_approved:** 100-200 за 25 тиков
- **Audit events:** 10-30 `audit_flagged`, 2-6 `audit_case_opened`, 1-4 `audit_escalated` (в G3)
- **Secondary spawns:** 5-15 вторичных агентов (из worldgen + social graph)

---

## 7. Известные неблокирующие проблемы

Эти проблемы **не требуют остановки прогона**, просто учесть при анализе:

1. **Rejection rate 15-20%** — нормально для LLM-based системы. Из них:
   - ~5-8% корректные отказы (capability, содержательные)
   - ~5-10% LLM noise (schema failures, edge cases)
   - ~2-3% connection errors (если сеть флапает)

2. **`bureaucratic_loop_total` всегда 0** — метрика существует но детектор отключён (`_find_duplicate_open_work_item` возвращает None). Не путать с "нет loops" — просто не детектится.

3. **Collegial review quorum:** с 11 DAO voters достижение порога 0.5 требует 6 активных голосов. Если voting duration = 3 тика и voters не успевают проголосовать — кейс `dismissed: quorum_not_reached`. Это **нормально** и даёт data для обсуждения в ВКР.

4. **Freeform truth может быть пустым** — LLM-oracle не всегда находит нарушения сверх deterministic rules. Zero findings ≠ баг.

---

## 8. После завершения

### Commit результатов
```bash
# Добавить только summary/eval/fidelity (большие events/trace не в git)
git add results/overnight_g3/summary.json \
        results/overnight_g3/evaluation.json \
        results/overnight_g3/fidelity.json \
        results/overnight_g3/perf_summary.json
git commit -m "Add overnight G3 run results (25 ticks, 20 agents)"
git push
```

### Уведомление
Напиши заказчику (Aleksey Daniliuk) о завершении с краткой сводкой:
- `state` из status.json (finished/failed)
- F1 / P / R из evaluation.json
- Любые WARNING из sanity checks
- Путь к результатам

---

## 9. Приложение: минимальные системные требования

- **CPU:** 2 cores достаточно (все ждут LLM)
- **RAM:** 1-2 GB (движок + embedding cache)
- **Disk:** 500 MB на прогон (trace.jsonl — самый большой файл)
- **Network:** стабильное подключение к OpenRouter (api.openrouter.ai)
- **Python:** 3.11+
- **OS:** Linux/macOS/Windows — все работают (на Windows — `taskkill //PID //F` для kill)
