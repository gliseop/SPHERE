# Навигатор по архитектуре

Этот документ помогает ориентироваться в архитектуре MAGISTRY: карта модулей, зависимости между ними и путь данных в веб-интерфейсе. Документ не дублирует содержание кода, а указывает, куда читать.

## Карта модулей

```mermaid
graph TB
    subgraph Ядро["Ядро движка"]
        ENGINE[engine.py<br/>WorldEngine]
        STATE[state.py<br/>WorldState]
        CONFIG[config.py<br/>ScenarioConfig]
        IDS[ids.py<br/>TypedId, EntityKind]
        ENTITIES[entities.py<br/>EntityRegistry]
        IDALLOC[id_alloc.py<br/>IdAllocator]
        OPS[ops.py<br/>StateOp → Event]
    end

    subgraph Агент["Агент"]
        AGENT[agent.py<br/>AgentRunner]
        MEMORY[memory.py<br/>AgentMemory]
        ACTIONS[actions.py<br/>Action + perform]
        PERSONA[persona.py<br/>PersonaArtifact]
        BM25[bm25.py<br/>BM25]
    end

    subgraph Управление["Управление и арбитраж"]
        ARBITER[arbiter.py<br/>HybridArbiter]
        JOURNAL[journal.py<br/>YAMLJournal]
        DAO[dao.py<br/>DAO vote + policy]
    end

    subgraph Генерация["Генерация мира"]
        WORLDGEN[worldgen.py<br/>WorldGenerator]
        COMPOSER[composer.py<br/>WorldComposer]
        ORACLE[oracle.py<br/>ViolationOracle]
    end

    subgraph LLM["LLM-подсистема (llm/)"]
        PROTOCOLS[protocols.py<br/>LLMProvider]
        PROVIDERS[providers.py<br/>OpenAI + Mock]
        EMBEDDINGS[embeddings.py<br/>EmbeddingProvider]
        CACHE[llm/cache.py<br/>LLMCache]
        CALLER[caller.py<br/>LLMCaller]
    end

    subgraph Инфраструктура["Инфраструктура"]
        EVENTS[events.py<br/>EventLog JSONL]
        TRACING[tracing.py<br/>TraceLog JSONL]
        SCENARIO[scenario.py<br/>load/save YAML]
        GRAPHS[graphs.py<br/>LangGraph]
        CLI[cli.py<br/>magistry-lc]
    end

    subgraph Веб["Веб-интерфейс"]
        MAIN[web/backend/main.py]
        ROUTES[web/backend/routes/]
        AUTH[web/backend/auth.py]
        RUNNER[web/backend/runner.py]
        FRONT[web/frontend/]
    end

    ENGINE --> STATE
    ENGINE --> AGENT
    ENGINE --> ARBITER
    ENGINE --> OPS
    ENGINE --> EVENTS
    ENGINE --> WORLDGEN

    AGENT --> MEMORY
    AGENT --> ACTIONS
    AGENT --> PERSONA
    MEMORY --> BM25
    MEMORY --> EMBEDDINGS

    ARBITER --> JOURNAL
    ARBITER --> ENTITIES
    ARBITER --> CALLER

    DAO --> OPS
    DAO --> STATE

    COMPOSER --> CALLER
    ORACLE --> CALLER

    CALLER --> PROVIDERS
    CALLER --> TRACING
    PROVIDERS --> PROTOCOLS

    CLI --> ENGINE
    CLI --> COMPOSER
    CLI --> ORACLE

    RUNNER --> ENGINE
    MAIN --> AUTH
    MAIN --> ROUTES
    MAIN --> RUNNER
    FRONT --> MAIN
```

## Указатель модулей

| Хочу понять... | Где читать |
|---|---|
| Как устроен тик симуляции | `engine.py` → `WorldEngine.run()` |
| Как агент принимает решение | `agent.py` → `AgentRunner`, `memory.py` → гибридный retrieval |
| Какие действия доступны агенту | `actions.py` → `ActionKind`, структурированные + `perform` |
| Как арбитр проверяет действия | `arbiter.py` → полномочия + антифантомы + LLM-perform |
| Как работает YAML-журнал | `journal.py` → инкрементальная сводка мира для арбитра |
| Как устроено DAO-голосование | `dao.py` → кворум, порог, закрытие голосования |
| Типизированные ID и антифантомы | `ids.py` + `entities.py` → `EntityRegistry` |
| Детерминированный apply | `ops.py` → `StateOp` преобразуется в `Event` |
| Как генерируется сценарий через LLM | `composer.py` → `WorldComposer.compose()` |
| Как работает генератор мира | `worldgen.py` → внешние события без приватных утечек |
| Как оракул анализирует нарушения | `oracle.py` → чанкинг по events.jsonl |
| Как устроена память агента | `memory.py` → working buffer + long-term hybrid index |
| Как работает гибридный поиск | `memory.py` (retrieval) + `bm25.py` (лексический) + `embeddings.py` (векторный) |
| Как устроены LLM-провайдеры | `llm/providers.py` → `OpenAICompatibleProvider`, `MockLLMProvider` |
| Как устроен конфиг сценария | `config.py` → `ScenarioConfig` (Pydantic) |
| Как загружается/сохраняется сценарий | `scenario.py` → YAML/JSON |
| Как устроена личность агента | `persona.py` → `PersonaArtifact`, `PersonaLibrary`, `PersonaGenerator` |
| Как работает LangGraph-интеграция | `graphs.py` → tick graph + SqliteSaver checkpoints |
| Как работает CLI | `cli.py` → `run`, `compose`, `oracle` |

## Путь данных: тик симуляции

```mermaid
sequenceDiagram
    participant E as WorldEngine
    participant A as AgentRunner
    participant M as AgentMemory
    participant Arb as Arbiter
    participant Ent as EntityRegistry
    participant O as Ops
    participant S as WorldState
    participant EL as EventLog

    E->>A: decide(agent, state, tick)
    A->>M: retrieve(situation)
    M-->>A: релевантные воспоминания
    A-->>E: Action[]

    loop Каждое действие
        E->>Arb: evaluate(action, state)
        Arb->>Ent: validate_targets(action)
        Ent-->>Arb: ✓ / reject (антифантом)
        Arb-->>E: verdict (approved / rejected)

        alt Одобрено
            E->>O: apply(action, state)
            O->>S: обновление состояния
            O-->>E: Event
            E->>EL: запись события
        end
    end

    E->>M: store(events для агента)
```

## Путь данных: веб-интерфейс

```mermaid
sequenceDiagram
    participant Б as Браузер (React)
    participant R as Routes (FastAPI)
    participant Run as Runner
    participant E as WorldEngine
    participant EL as EventLog

    Б->>R: POST /api/runs/launch {scenario}
    R->>Run: запуск в фоновом потоке
    Run->>E: WorldEngine.run()

    loop Каждый тик
        E->>EL: запись события
        E->>Run: помещение в очередь
    end

    Б->>R: WebSocket /ws/live
    R->>Б: аутентификация (JWT)

    loop Пока симуляция идёт
        R->>Б: sim_event (пакет событий)
        R->>Б: graph_update (обновление графа)
        R->>Б: sim_status
    end

    R->>Б: sim_ended

    Б->>R: GET /api/run/{name}
    R->>Б: JSON (события, граф, метрики)
```

Браузер подключается по WebSocket после аутентификации. Сервер пакетирует события (по `MAGISTRY_WS_EVENT_BATCH_SIZE` штук каждые `MAGISTRY_WS_EVENT_BATCH_INTERVAL_S` секунд) и троттлит обновления графа (не чаще `MAGISTRY_LIVE_GRAPH_THROTTLE_S`). При подключении к уже идущей симуляции клиент получает буфер последних `MAGISTRY_LIVE_HISTORY_EVENTS` событий.
