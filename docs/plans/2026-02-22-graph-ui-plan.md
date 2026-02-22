# Graph UI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Создать веб-интерфейс для визуализации и воспроизведения симуляций MAGISTRY в форме интерактивного графа агентов.

**Architecture:** FastAPI backend (file watcher + WebSocket) + React/Vite frontend (react-force-graph-2d). Минимальное изменение в ядре: EventLog получает streaming append для live-режима. Backend реконструирует социальный граф из событий `graph_updated`, репутацию из `reputation_modified`, передаёт клиенту через WebSocket-протокол.

**Tech Stack:** Python FastAPI, aiofiles, uvicorn[standard], React 18, Vite, TypeScript, react-force-graph-2d, Tailwind CSS

---

## Task 1: Streaming append в EventLog

**Files:**
- Modify: `src/magistry_sim/events.py`

### Step 1: Изменить EventLog

Открыть `src/magistry_sim/events.py`. Изменить класс `EventLog`:

```python
class EventLog:
    """Журнал событий с записью в JSONL."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._stream_path: Path | None = None

    def set_stream_path(self, path: Path) -> None:
        """Установить путь для потоковой дозаписи событий.

        Args:
            path: Путь к файлу для дозаписи.
        """
        self._stream_path = path

    def log(
        self,
        round: int,
        event_type: str,
        agent_id: str = "",
        payload: dict | None = None,
    ) -> Event:
        """Записать событие.

        Args:
            round: Номер раунда.
            event_type: Тип события.
            agent_id: Идентификатор агента.
            payload: Дополнительные данные.

        Returns:
            Записанное событие.
        """
        event = Event(
            round=round,
            event_type=event_type,
            agent_id=agent_id,
            payload=payload or {},
        )
        self._events.append(event)
        if self._stream_path is not None:
            with open(self._stream_path, "a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
        return event
```

### Step 2: Обновить `run_research.py`

В функции `run_single`, после строки `env = Environment(...)` и перед `start = time.time()`, добавить:

```python
    env.state.event_log.set_stream_path(events_path)
```

**Примечание:** `events_path` уже определена выше в функции.

### Step 3: Запустить существующие тесты

```bash
cd /home/development/MAGISTRY
.venv/bin/pytest tests/ -x -q 2>&1 | tail -20
```

Ожидание: все тесты зелёные.

### Step 4: Commit

```bash
git add src/magistry_sim/events.py run_research.py
git commit -m "feat: add streaming append to EventLog for live monitoring"
```

---

## Task 2: Backend — структура и зависимости

**Files:**
- Create: `web/backend/requirements.txt`
- Create: `web/backend/main.py`

### Step 1: Установить зависимости

```bash
cd /home/development/MAGISTRY
.venv/bin/pip install fastapi "uvicorn[standard]" aiofiles python-multipart
```

### Step 2: Создать `web/backend/requirements.txt`

```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
aiofiles>=23.0
python-multipart>=0.0.9
```

### Step 3: Создать скелет `web/backend/main.py`

```python
"""FastAPI-сервер для веб-интерфейса симуляций MAGISTRY."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import AsyncIterator

import aiofiles
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

RESULTS_DIR = Path(__file__).parent.parent.parent / "results"
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

app = FastAPI(title="MAGISTRY Graph UI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Вспомогательные функции ---

def _parse_run_name(filename: str) -> dict:
    """Разобрать имя файла прогона в метаданные.

    Args:
        filename: Имя файла вида S1_G2_events.jsonl или S1_G2_seed42_events.jsonl.

    Returns:
        Словарь с полями scenario, governance, seed (если есть).
    """
    m = re.match(
        r"(?P<scenario>S\d+)_(?P<governance>G\d+)(?:_seed(?P<seed>\d+))?_events\.jsonl",
        filename,
    )
    if not m:
        return {"scenario": "?", "governance": "?", "seed": None}
    return {
        "scenario": m.group("scenario"),
        "governance": m.group("governance"),
        "seed": int(m.group("seed")) if m.group("seed") else None,
    }


def _build_graph_state(events: list[dict]) -> dict:
    """Реконструировать состояние графа из событий.

    Args:
        events: Список событий до текущего момента.

    Returns:
        Словарь {"nodes": [...], "edges": [...]}.
    """
    agents: dict[str, dict] = {}
    edges: dict[tuple, float] = {}

    for e in events:
        aid = e.get("agent_id", "")
        if aid and aid != "system":
            if aid not in agents:
                agents[aid] = {"id": aid, "reputation": 10.0}

        if e["event_type"] == "reputation_modified":
            target = e["payload"].get("target", aid)
            delta = e["payload"].get("delta", 0.0)
            if target not in agents:
                agents[target] = {"id": target, "reputation": 10.0}
            agents[target]["reputation"] += delta

        if e["event_type"] == "graph_updated":
            a = e["payload"].get("agent_a", "")
            b = e["payload"].get("agent_b", "")
            delta = e["payload"].get("delta", 0.1)
            if a and b:
                key = tuple(sorted([a, b]))
                edges[key] = edges.get(key, 0.0) + delta

    nodes = list(agents.values())
    edge_list = [
        {"source": k[0], "target": k[1], "strength": round(v, 2)}
        for k, v in edges.items()
    ]
    return {"nodes": nodes, "edges": edge_list}


async def _stream_events_from_file(
    path: Path, speed: float = 1.0
) -> AsyncIterator[dict]:
    """Читать события из JSONL-файла для playback.

    Args:
        path: Путь к файлу.
        speed: Множитель скорости (2.0 = в 2 раза быстрее).

    Yields:
        Словари событий.
    """
    delay = max(0.05, 0.3 / speed)
    async with aiofiles.open(path, encoding="utf-8") as f:
        async for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
                await asyncio.sleep(delay)


# --- REST endpoints ---

@app.get("/api/runs")
async def list_runs() -> list[dict]:
    """Вернуть список доступных прогонов.

    Returns:
        Список словарей с метаданными прогонов.
    """
    runs = []
    for p in sorted(RESULTS_DIR.glob("*_events.jsonl")):
        meta = _parse_run_name(p.name)
        meta["name"] = p.stem.replace("_events", "")
        meta["filename"] = p.name
        meta["size_kb"] = round(p.stat().st_size / 1024, 1)
        runs.append(meta)
    return runs


@app.get("/api/run/{name}")
async def get_run(name: str) -> dict:
    """Вернуть все события прогона.

    Args:
        name: Имя прогона (без суффикса _events.jsonl).

    Returns:
        Словарь с событиями и состоянием графа.
    """
    path = RESULTS_DIR / f"{name}_events.jsonl"
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Run not found")
    events = []
    async with aiofiles.open(path, encoding="utf-8") as f:
        async for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return {
        "name": name,
        "events": events,
        "graph": _build_graph_state(events),
        "meta": _parse_run_name(f"{name}_events.jsonl"),
    }


# --- WebSocket endpoints ---

@app.websocket("/ws/playback/{name}")
async def ws_playback(websocket: WebSocket, name: str, speed: float = 1.0) -> None:
    """WebSocket для воспроизведения записанного прогона.

    Args:
        websocket: WebSocket-соединение.
        name: Имя прогона.
        speed: Скорость воспроизведения (1.0 = реальное время, 5.0 = в 5 раз быстрее).
    """
    await websocket.accept()
    path = RESULTS_DIR / f"{name}_events.jsonl"
    if not path.exists():
        await websocket.send_json({"type": "error", "message": "Run not found"})
        await websocket.close()
        return

    events_so_far: list[dict] = []
    current_round = -1
    meta = _parse_run_name(f"{name}_events.jsonl")
    await websocket.send_json({"type": "meta", **meta})

    try:
        async for event in _stream_events_from_file(path, speed=speed):
            events_so_far.append(event)
            await websocket.send_json({"type": "event", "data": event})

            if event["round"] != current_round:
                current_round = event["round"]
                graph = _build_graph_state(events_so_far)
                await websocket.send_json({"type": "graph_state", **graph})

        await websocket.send_json({"type": "done"})
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    """WebSocket для мониторинга текущего прогона.

    Следит за самым свежим *_events.jsonl файлом и пушит новые строки.
    """
    await websocket.accept()

    events_so_far: list[dict] = []
    current_round = -1
    watched_path: Path | None = None
    file_pos = 0

    try:
        while True:
            # Найти самый свежий events файл
            candidates = sorted(
                RESULTS_DIR.glob("*_events.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                await asyncio.sleep(1.0)
                continue

            latest = candidates[0]
            if latest != watched_path:
                watched_path = latest
                file_pos = 0
                events_so_far = []
                current_round = -1
                meta = _parse_run_name(latest.name)
                await websocket.send_json({"type": "meta", **meta})

            async with aiofiles.open(watched_path, encoding="utf-8") as f:
                await f.seek(file_pos)
                new_lines = await f.read()
                file_pos = await f.tell()

            for line in new_lines.splitlines():
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                events_so_far.append(event)
                await websocket.send_json({"type": "event", "data": event})

                if event["round"] != current_round:
                    current_round = event["round"]
                    graph = _build_graph_state(events_so_far)
                    await websocket.send_json({"type": "graph_state", **graph})

            await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        pass


# --- Serve frontend ---
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")
```

### Step 4: Проверить импорты

```bash
cd /home/development/MAGISTRY
.venv/bin/python -c "import fastapi, aiofiles, uvicorn; print('ok')"
```

Ожидание: `ok`

### Step 5: Запустить backend и проверить эндпоинт

```bash
cd /home/development/MAGISTRY
.venv/bin/uvicorn web.backend.main:app --port 8765 --reload &
sleep 2
curl -s http://localhost:8765/api/runs | python3 -m json.tool | head -30
```

Ожидание: JSON-массив с прогонами.

### Step 6: Остановить тестовый сервер и commit

```bash
kill %1
git add web/backend/
git commit -m "feat: add FastAPI backend with WebSocket endpoints"
```

---

## Task 3: Frontend — инициализация проекта

**Files:**
- Create: `web/frontend/` (Vite + React + TypeScript)

### Step 1: Создать Vite-проект

```bash
cd /home/development/MAGISTRY/web
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
```

### Step 2: Установить зависимости

```bash
cd /home/development/MAGISTRY/web/frontend
npm install react-force-graph-2d
npm install -D tailwindcss@3 postcss autoprefixer
npx tailwindcss init -p
```

### Step 3: Настроить Tailwind

Заменить содержимое `web/frontend/tailwind.config.js`:

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
}
```

Заменить содержимое `web/frontend/src/index.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

### Step 4: Настроить Vite proxy

Заменить `web/frontend/vite.config.ts`:

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8765',
      '/ws': { target: 'ws://localhost:8765', ws: true },
    },
  },
})
```

### Step 5: Проверить сборку

```bash
cd /home/development/MAGISTRY/web/frontend
npm run build 2>&1 | tail -10
```

Ожидание: `✓ built in ...`

### Step 6: Commit

```bash
cd /home/development/MAGISTRY
git add web/frontend/
git commit -m "feat: init Vite + React + TypeScript frontend"
```

---

## Task 4: Frontend — WebSocket hook

**Files:**
- Create: `web/frontend/src/hooks/useSimulation.ts`
- Create: `web/frontend/src/types.ts`

### Step 1: Создать `web/frontend/src/types.ts`

```ts
export interface SimEvent {
  round: number
  event_type: string
  agent_id: string
  payload: Record<string, unknown>
  timestamp: string
}

export interface GraphNode {
  id: string
  reputation: number
}

export interface GraphEdge {
  source: string
  target: string
  strength: number
}

export interface GraphState {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface SimMeta {
  scenario: string
  governance: string
  seed: number | null
}

export type WsMessage =
  | { type: 'meta'; scenario: string; governance: string; seed: number | null }
  | { type: 'event'; data: SimEvent }
  | { type: 'graph_state'; nodes: GraphNode[]; edges: GraphEdge[] }
  | { type: 'done' }
  | { type: 'error'; message: string }

export interface RunInfo {
  name: string
  filename: string
  scenario: string
  governance: string
  seed: number | null
  size_kb: number
}
```

### Step 2: Создать `web/frontend/src/hooks/useSimulation.ts`

```ts
import { useCallback, useEffect, useRef, useState } from 'react'
import type { GraphEdge, GraphNode, RunInfo, SimEvent, SimMeta, WsMessage } from '../types'

export type SimMode = 'idle' | 'playback' | 'live'

export interface SimState {
  meta: SimMeta | null
  events: SimEvent[]
  nodes: GraphNode[]
  edges: GraphEdge[]
  currentRound: number
  done: boolean
  error: string | null
}

const INITIAL_STATE: SimState = {
  meta: null,
  events: [],
  nodes: [],
  edges: [],
  currentRound: 0,
  done: false,
  error: null,
}

export function useSimulation() {
  const [state, setState] = useState<SimState>(INITIAL_STATE)
  const [mode, setMode] = useState<SimMode>('idle')
  const wsRef = useRef<WebSocket | null>(null)

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    setMode('idle')
  }, [])

  const connect = useCallback((url: string, newMode: SimMode) => {
    disconnect()
    setState(INITIAL_STATE)
    setMode(newMode)
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onmessage = (evt) => {
      const msg: WsMessage = JSON.parse(evt.data)
      setState((prev) => {
        switch (msg.type) {
          case 'meta':
            return { ...prev, meta: { scenario: msg.scenario, governance: msg.governance, seed: msg.seed } }
          case 'event':
            return {
              ...prev,
              events: [...prev.events, msg.data],
              currentRound: msg.data.round,
            }
          case 'graph_state':
            return { ...prev, nodes: msg.nodes, edges: msg.edges }
          case 'done':
            return { ...prev, done: true }
          case 'error':
            return { ...prev, error: msg.message }
          default:
            return prev
        }
      })
    }

    ws.onerror = () => setState((prev) => ({ ...prev, error: 'WebSocket error' }))
    ws.onclose = () => setMode('idle')
  }, [disconnect])

  const startPlayback = useCallback(
    (run: RunInfo, speed: number = 2.0) => {
      const url = `ws://${window.location.host}/ws/playback/${run.name}?speed=${speed}`
      connect(url, 'playback')
    },
    [connect]
  )

  const startLive = useCallback(() => {
    const url = `ws://${window.location.host}/ws/live`
    connect(url, 'live')
  }, [connect])

  useEffect(() => () => disconnect(), [disconnect])

  return { state, mode, startPlayback, startLive, disconnect }
}
```

### Step 3: Проверить TypeScript

```bash
cd /home/development/MAGISTRY/web/frontend
npm run build 2>&1 | grep -E "error|warning|built"
```

Ожидание: `✓ built in ...` без ошибок TypeScript.

### Step 4: Commit

```bash
cd /home/development/MAGISTRY
git add web/frontend/src/hooks/ web/frontend/src/types.ts
git commit -m "feat: add WebSocket hook and TypeScript types"
```

---

## Task 5: Frontend — компонент SimGraph

**Files:**
- Create: `web/frontend/src/components/SimGraph.tsx`

### Step 1: Создать `web/frontend/src/components/SimGraph.tsx`

```tsx
import ForceGraph2D, { type NodeObject, type LinkObject } from 'react-force-graph-2d'
import { useCallback, useMemo } from 'react'
import type { GraphEdge, GraphNode, SimEvent } from '../types'

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  events: SimEvent[]
  onNodeClick?: (nodeId: string) => void
  selectedNode?: string | null
}

const SUSPICIOUS_THRESHOLD = 3.0

function nodeColor(id: string): string {
  if (id.startsWith('off_')) return '#ef4444'   // красный — чиновник
  if (id.startsWith('biz_')) return '#3b82f6'   // синий — бизнес
  if (id.startsWith('aud_')) return '#f97316'   // оранжевый — аудитор
  return '#6b7280'                               // серый — прочее
}

function edgeColor(strength: number, isPrivate: boolean): string {
  if (strength >= SUSPICIOUS_THRESHOLD) return '#ef4444'
  if (isPrivate) return '#a78bfa'
  return '#94a3b8'
}

export function SimGraph({ nodes, edges, events, onNodeClick, selectedNode }: Props) {
  // Определить, какие пары агентов обменивались приватными сообщениями
  const privateEdges = useMemo(() => {
    const set = new Set<string>()
    for (const e of events) {
      if (e.event_type === 'message_sent' && e.payload.private) {
        const from = e.agent_id
        const to = e.payload.to_id as string
        if (from && to) {
          set.add([from, to].sort().join('|'))
        }
      }
    }
    return set
  }, [events])

  const graphData = useMemo(() => ({
    nodes: nodes.map((n) => ({
      id: n.id,
      reputation: n.reputation,
    })),
    links: edges.map((e) => {
      const key = [e.source, e.target].sort().join('|')
      return {
        source: e.source,
        target: e.target,
        strength: e.strength,
        isPrivate: privateEdges.has(key),
      }
    }),
  }), [nodes, edges, privateEdges])

  const nodeCanvasObject = useCallback(
    (node: NodeObject, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const id = node.id as string
      const reputation = (node as NodeObject & { reputation: number }).reputation ?? 10
      const radius = Math.max(4, Math.min(12, reputation * 0.6))
      const isSelected = id === selectedNode

      ctx.beginPath()
      ctx.arc(node.x!, node.y!, radius, 0, 2 * Math.PI)
      ctx.fillStyle = nodeColor(id)
      ctx.fill()

      if (isSelected) {
        ctx.strokeStyle = '#fbbf24'
        ctx.lineWidth = 2
        ctx.stroke()
      }

      const label = id
      const fontSize = Math.max(8, 10 / globalScale)
      ctx.font = `${fontSize}px sans-serif`
      ctx.fillStyle = '#e2e8f0'
      ctx.textAlign = 'center'
      ctx.fillText(label, node.x!, node.y! + radius + fontSize)
    },
    [selectedNode]
  )

  const linkColor = useCallback(
    (link: LinkObject) => {
      const l = link as LinkObject & { strength: number; isPrivate: boolean }
      return edgeColor(l.strength, l.isPrivate)
    },
    []
  )

  const linkWidth = useCallback(
    (link: LinkObject) => {
      const l = link as LinkObject & { strength: number }
      return Math.min(6, 0.5 + l.strength * 0.4)
    },
    []
  )

  const handleNodeClick = useCallback(
    (node: NodeObject) => {
      onNodeClick?.(node.id as string)
    },
    [onNodeClick]
  )

  if (nodes.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-500">
        Данных нет. Выберите прогон или запустите live-мониторинг.
      </div>
    )
  }

  return (
    <ForceGraph2D
      graphData={graphData}
      nodeCanvasObject={nodeCanvasObject}
      nodePointerAreaPaint={(node, color, ctx) => {
        const radius = 12
        ctx.beginPath()
        ctx.arc(node.x!, node.y!, radius, 0, 2 * Math.PI)
        ctx.fillStyle = color
        ctx.fill()
      }}
      linkColor={linkColor}
      linkWidth={linkWidth}
      linkDirectionalParticles={2}
      linkDirectionalParticleSpeed={0.004}
      onNodeClick={handleNodeClick}
      backgroundColor="#0f172a"
      width={undefined}
      height={undefined}
    />
  )
}
```

### Step 2: Проверить сборку

```bash
cd /home/development/MAGISTRY/web/frontend
npm run build 2>&1 | grep -E "error|built"
```

### Step 3: Commit

```bash
cd /home/development/MAGISTRY
git add web/frontend/src/components/SimGraph.tsx
git commit -m "feat: add SimGraph component with force-directed layout"
```

---

## Task 6: Frontend — боковые панели и лента событий

**Files:**
- Create: `web/frontend/src/components/EventFeed.tsx`
- Create: `web/frontend/src/components/AgentPanel.tsx`
- Create: `web/frontend/src/components/RunSelector.tsx`
- Create: `web/frontend/src/components/PlaybackControls.tsx`

### Step 1: Создать `web/frontend/src/components/EventFeed.tsx`

```tsx
import { useEffect, useRef } from 'react'
import type { SimEvent } from '../types'

const EVENT_ICONS: Record<string, string> = {
  message_sent: '💬',
  graph_updated: '🔗',
  reputation_modified: '⭐',
  case_opened: '📂',
  case_resolved: '✅',
  case_modified: '✏️',
  proposal_submitted: '📋',
  evidence_added: '🔍',
  evidence_removed: '🗑️',
  arbiter_approved: '✔️',
  arbiter_rejected: '✖️',
  auto_transition: '⏭️',
  world_event: '🌍',
}

interface Props {
  events: SimEvent[]
  selectedAgent?: string | null
}

export function EventFeed({ events, selectedAgent }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  const filtered = selectedAgent
    ? events.filter(
        (e) =>
          e.agent_id === selectedAgent ||
          e.payload.to_id === selectedAgent ||
          e.payload.target === selectedAgent
      )
    : events

  return (
    <div className="flex flex-col h-full">
      <div className="text-xs text-slate-400 px-2 py-1 border-b border-slate-700">
        События {selectedAgent ? `(${selectedAgent})` : ''}: {filtered.length}
      </div>
      <div className="flex-1 overflow-y-auto text-xs space-y-0.5 p-1">
        {filtered.map((e, i) => (
          <div
            key={i}
            className="flex gap-1 items-start rounded px-1 py-0.5 hover:bg-slate-800"
          >
            <span className="shrink-0 w-5 text-center">
              {EVENT_ICONS[e.event_type] ?? '•'}
            </span>
            <span className="text-slate-400 shrink-0 w-4">R{e.round}</span>
            <span className="text-slate-300 shrink-0 font-mono w-14 truncate">
              {e.agent_id || '—'}
            </span>
            <span className="text-slate-500 truncate">{e.event_type}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
```

### Step 2: Создать `web/frontend/src/components/AgentPanel.tsx`

```tsx
import type { GraphEdge, GraphNode, SimEvent } from '../types'

interface Props {
  nodeId: string | null
  nodes: GraphNode[]
  edges: GraphEdge[]
  events: SimEvent[]
}

export function AgentPanel({ nodeId, nodes, edges, events }: Props) {
  if (!nodeId) {
    return (
      <div className="p-3 text-slate-500 text-sm">
        Кликните на агента в графе
      </div>
    )
  }

  const node = nodes.find((n) => n.id === nodeId)
  const connections = edges.filter(
    (e) => e.source === nodeId || e.target === nodeId
  )
  const messages = events.filter(
    (e) =>
      e.event_type === 'message_sent' &&
      (e.agent_id === nodeId || e.payload.to_id === nodeId)
  )

  function roleLabel(id: string): string {
    if (id.startsWith('off_')) return 'Чиновник'
    if (id.startsWith('biz_')) return 'Подрядчик'
    if (id.startsWith('aud_')) return 'Аудитор'
    return 'Агент'
  }

  return (
    <div className="p-2 space-y-3 text-sm overflow-y-auto h-full">
      <div>
        <div className="font-mono text-base text-white">{nodeId}</div>
        <div className="text-slate-400">{roleLabel(nodeId)}</div>
        <div className="text-slate-300 mt-1">
          Репутация: <span className="font-bold">{node?.reputation.toFixed(1) ?? '—'}</span>
        </div>
      </div>

      <div>
        <div className="text-slate-400 text-xs mb-1">Связи ({connections.length})</div>
        <div className="space-y-0.5">
          {connections.map((c, i) => {
            const other = c.source === nodeId ? c.target : c.source
            const suspicious = c.strength >= 3.0
            return (
              <div key={i} className="flex justify-between text-xs">
                <span className="font-mono text-slate-300">{String(other)}</span>
                <span className={suspicious ? 'text-red-400' : 'text-slate-500'}>
                  {c.strength.toFixed(1)}
                  {suspicious ? ' ⚠️' : ''}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      <div>
        <div className="text-slate-400 text-xs mb-1">Сообщения ({messages.length})</div>
        <div className="space-y-1 max-h-48 overflow-y-auto">
          {messages.slice(-10).map((e, i) => {
            const isFrom = e.agent_id === nodeId
            const other = isFrom ? e.payload.to_id : e.agent_id
            return (
              <div key={i} className="text-xs text-slate-400">
                <span className={e.payload.private ? 'text-violet-400' : 'text-slate-300'}>
                  R{e.round} {isFrom ? '→' : '←'} {String(other)}
                  {e.payload.private ? ' 🔒' : ''}
                </span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
```

### Step 3: Создать `web/frontend/src/components/RunSelector.tsx`

```tsx
import { useEffect, useState } from 'react'
import type { RunInfo } from '../types'

interface Props {
  onPlayback: (run: RunInfo, speed: number) => void
  onLive: () => void
  speed: number
  onSpeedChange: (s: number) => void
  mode: string
}

export function RunSelector({ onPlayback, onLive, speed, onSpeedChange, mode }: Props) {
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [selected, setSelected] = useState<RunInfo | null>(null)

  useEffect(() => {
    fetch('/api/runs')
      .then((r) => r.json())
      .then(setRuns)
      .catch(console.error)
  }, [])

  return (
    <div className="p-2 space-y-3 text-sm">
      <div>
        <div className="text-slate-400 text-xs mb-1">Прогоны ({runs.length})</div>
        <div className="space-y-0.5 max-h-64 overflow-y-auto">
          {runs.map((r) => (
            <button
              key={r.name}
              onClick={() => setSelected(r)}
              className={`w-full text-left px-2 py-1 rounded text-xs font-mono truncate
                ${selected?.name === r.name
                  ? 'bg-blue-800 text-white'
                  : 'text-slate-300 hover:bg-slate-800'
                }`}
            >
              {r.scenario}/{r.governance}
              {r.seed !== null ? `/seed${r.seed}` : ''}
              <span className="text-slate-500 ml-1">({r.size_kb}KB)</span>
            </button>
          ))}
        </div>
      </div>

      <div>
        <div className="text-slate-400 text-xs mb-1">
          Скорость: {speed.toFixed(1)}x
        </div>
        <input
          type="range"
          min={0.5}
          max={20}
          step={0.5}
          value={speed}
          onChange={(e) => onSpeedChange(Number(e.target.value))}
          className="w-full accent-blue-500"
        />
      </div>

      <button
        disabled={!selected || mode !== 'idle'}
        onClick={() => selected && onPlayback(selected, speed)}
        className="w-full py-1.5 rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-40
                   text-white text-xs font-medium"
      >
        ▶ Воспроизвести
      </button>

      <button
        disabled={mode !== 'idle'}
        onClick={onLive}
        className="w-full py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40
                   text-white text-xs font-medium"
      >
        ● Live-мониторинг
      </button>

      {mode !== 'idle' && (
        <div className="text-xs text-center text-emerald-400">
          {mode === 'live' ? 'Live...' : 'Воспроизведение...'}
        </div>
      )}
    </div>
  )
}
```

### Step 4: Проверить сборку

```bash
cd /home/development/MAGISTRY/web/frontend
npm run build 2>&1 | grep -E "error|built"
```

Ожидание: `✓ built in ...`

### Step 5: Commit

```bash
cd /home/development/MAGISTRY
git add web/frontend/src/components/
git commit -m "feat: add EventFeed, AgentPanel, RunSelector components"
```

---

## Task 7: Frontend — главный компонент App.tsx

**Files:**
- Modify: `web/frontend/src/App.tsx`
- Modify: `web/frontend/src/main.tsx` (добавить импорт CSS)

### Step 1: Заменить `web/frontend/src/App.tsx`

```tsx
import { useState } from 'react'
import { useSimulation } from './hooks/useSimulation'
import { SimGraph } from './components/SimGraph'
import { EventFeed } from './components/EventFeed'
import { AgentPanel } from './components/AgentPanel'
import { RunSelector } from './components/RunSelector'
import type { RunInfo } from './types'

export default function App() {
  const { state, mode, startPlayback, startLive, disconnect } = useSimulation()
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [speed, setSpeed] = useState(3.0)

  const privateRatio = state.events.length
    ? (
        state.events.filter(
          (e) => e.event_type === 'message_sent' && e.payload.private
        ).length /
        Math.max(1, state.events.filter((e) => e.event_type === 'message_sent').length)
      ) * 100
    : 0

  const violations = state.events.filter(
    (e) =>
      e.event_type === 'case_resolved' &&
      String(e.payload.decision ?? '').toLowerCase().includes('нарушен')
  ).length

  return (
    <div className="h-screen bg-slate-900 text-white flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-4 px-4 py-2 bg-slate-800 border-b border-slate-700 shrink-0">
        <span className="font-bold text-slate-200">MAGISTRY Graph UI</span>
        {state.meta && (
          <span className="text-slate-400 text-sm">
            {state.meta.scenario} / {state.meta.governance}
            {state.meta.seed !== null ? ` / seed${state.meta.seed}` : ''}
          </span>
        )}
        <div className="ml-auto flex gap-6 text-xs text-slate-400">
          <span>Раунд: <b className="text-white">{state.currentRound}</b></span>
          <span>Событий: <b className="text-white">{state.events.length}</b></span>
          <span>
            Приватных: <b className="text-white">{privateRatio.toFixed(0)}%</b>
          </span>
          <span>Агентов: <b className="text-white">{state.nodes.length}</b></span>
          {state.done && <span className="text-emerald-400">✓ Завершено</span>}
          {state.error && <span className="text-red-400">⚠ {state.error}</span>}
        </div>
        {mode !== 'idle' && (
          <button
            onClick={disconnect}
            className="text-xs px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600"
          >
            Стоп
          </button>
        )}
      </div>

      {/* Main layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left panel */}
        <div className="w-48 shrink-0 border-r border-slate-700 overflow-y-auto">
          <RunSelector
            onPlayback={(run: RunInfo, spd: number) => {
              setSelectedNode(null)
              startPlayback(run, spd)
            }}
            onLive={() => {
              setSelectedNode(null)
              startLive()
            }}
            speed={speed}
            onSpeedChange={setSpeed}
            mode={mode}
          />
        </div>

        {/* Center: graph */}
        <div className="flex-1 relative overflow-hidden">
          <SimGraph
            nodes={state.nodes}
            edges={state.edges}
            events={state.events}
            onNodeClick={setSelectedNode}
            selectedNode={selectedNode}
          />
        </div>

        {/* Right panel */}
        <div className="w-64 shrink-0 border-l border-slate-700 flex flex-col overflow-hidden">
          <div className="h-1/2 border-b border-slate-700 overflow-hidden">
            <AgentPanel
              nodeId={selectedNode}
              nodes={state.nodes}
              edges={state.edges}
              events={state.events}
            />
          </div>
          <div className="h-1/2 overflow-hidden">
            <EventFeed
              events={state.events}
              selectedAgent={selectedNode}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
```

### Step 2: Убедиться, что `web/frontend/src/main.tsx` импортирует CSS

Проверить содержимое `main.tsx`. Если там нет `import './index.css'`, добавить первой строкой.

### Step 3: Проверить сборку

```bash
cd /home/development/MAGISTRY/web/frontend
npm run build 2>&1 | grep -E "error|warning|built"
```

Ожидание: `✓ built in ...`

### Step 4: Commit

```bash
cd /home/development/MAGISTRY
git add web/frontend/src/App.tsx web/frontend/src/main.tsx
git commit -m "feat: add main App layout with three-column design"
```

---

## Task 8: Интеграционная проверка

**Цель:** запустить backend + frontend dev-сервер и проверить работу через браузер.

### Step 1: Запустить backend

```bash
cd /home/development/MAGISTRY
.venv/bin/uvicorn web.backend.main:app --port 8765 &
sleep 2
curl -s http://localhost:8765/api/runs | python3 -m json.tool | head -20
```

### Step 2: Запустить frontend dev-сервер

```bash
cd /home/development/MAGISTRY/web/frontend
npm run dev -- --port 5173 &
sleep 3
```

### Step 3: Открыть в браузере

Открыть `http://localhost:5173`. Проверить:
- Список прогонов в левой панели
- Выбрать любой прогон, нажать «Воспроизвести»
- Граф должен начать заполняться узлами и рёбрами
- При клике на узел — детали в правой панели
- EventFeed прокручивается по мере событий

### Step 4: Добавить запуск-скрипт

Создать `web/start.sh`:

```bash
#!/bin/bash
# Запуск MAGISTRY Graph UI
# Использование: ./web/start.sh [--port 8765]

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PORT="${2:-8765}"

echo "=== MAGISTRY Graph UI ==="
echo "Сборка фронтенда..."
cd "$SCRIPT_DIR/frontend"
npm run build

echo "Запуск сервера на http://localhost:$PORT"
cd "$ROOT_DIR"
exec .venv/bin/uvicorn web.backend.main:app --port "$PORT" --host 0.0.0.0
```

```bash
chmod +x /home/development/MAGISTRY/web/start.sh
```

### Step 5: Остановить процессы и финальный commit

```bash
kill %1 %2 2>/dev/null || true
cd /home/development/MAGISTRY
git add web/
git commit -m "feat: complete Graph UI — backend + frontend integration"
```

---

## Легенда цветов графа

| Цвет | Тип агента |
|------|-----------|
| Красный | Чиновник (`off_*`) |
| Синий | Подрядчик (`biz_*`) |
| Оранжевый | Аудитор (`aud_*`) |

| Рёбра | Значение |
|-------|---------|
| Красное | Сила связи ≥ 3.0 (подозрительное) |
| Фиолетовое | Приватные сообщения |
| Серое | Публичные взаимодействия |
