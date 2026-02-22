# Graph UI HUD Restyle Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Полностью переписать визуальный слой фронтенда MAGISTRY под HUD/AIComPet light-тему из ui-kit-light.html, заменив react-force-graph-2d на D3 SVG с анимациями, тултипами и горизонтальным таймлайном.

**Architecture:** Удаляем react-force-graph-2d, устанавливаем d3 + @types/d3. Добавляем `src/styles/hud.css` со всеми токенами из ui-kit-light.html. Компоненты переписываются с Tailwind-классов на hud.css классы. D3-симуляция управляется внутри SimGraph через useRef + useEffect без внешних React-состояний для координат узлов.

**Tech Stack:** React 19, TypeScript, D3 v7, Vite, hud.css (кастомный), JetBrains Mono (Google Fonts)

---

## Task 1: Установка D3 и удаление react-force-graph-2d

**Files:**
- Modify: `web/frontend/package.json`

**Step 1: Установить D3 и типы**

```bash
cd /home/development/MAGISTRY/web/frontend
npm install d3
npm install --save-dev @types/d3
```

Ожидаем: `added N packages` без ошибок.

**Step 2: Удалить react-force-graph-2d**

```bash
cd /home/development/MAGISTRY/web/frontend
npm uninstall react-force-graph-2d
```

**Step 3: Проверить сборку (ожидаем ошибки — SimGraph ещё не переписан)**

```bash
cd /home/development/MAGISTRY/web/frontend
npm run build 2>&1 | head -30
```

Ожидаем: ошибки про `react-force-graph-2d` — это нормально, продолжаем.

**Step 4: Commit**

```bash
cd /home/development/MAGISTRY/web/frontend
git add package.json package-lock.json
git commit -m "chore: replace react-force-graph-2d with d3"
```

---

## Task 2: Создать hud.css — полная CSS-система из референса

**Files:**
- Create: `web/frontend/src/styles/hud.css`

**Step 1: Создать файл со всеми переменными и компонентами**

Создай `web/frontend/src/styles/hud.css` со следующим содержимым:

```css
/* ===== HUD / AIComPet Design System — Light Theme ===== */

@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap');

:root {
  --bg: #ffffff;
  --surface-1: rgba(255, 255, 255, 0.98);
  --surface-2: #f5f5f5;
  --surface-3: #eaeaea;
  --border: #eaeaea;
  --border-strong: #d3d3d3;
  --text-primary: #000000;
  --text-secondary: #666666;
  --text-tertiary: #999999;
  --accent: #f97316;
  --accent-soft: rgba(249, 115, 22, 0.1);

  --cursor-hud-default: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24'%3E%3Cpath fill='none' stroke='%23ea580c' stroke-width='1.5' d='M2 2h6M2 2v6M22 2h-6M22 2v6M2 22h6M2 22v-6M22 22h-6M22 22v-6'/%3E%3Ccircle cx='12' cy='12' r='2' fill='%23ea580c'/%3E%3C/svg%3E") 12 12, crosshair;
  --cursor-hud-pointer: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24'%3E%3Cpath fill='none' stroke='%23000000' stroke-width='1.5' d='M2 2h6M2 2v6M22 2h-6M22 2v6M2 22h6M2 22v-6M22 22h-6M22 22v-6'/%3E%3Crect x='10' y='10' width='4' height='4' fill='%23ea580c'/%3E%3C/svg%3E") 12 12, pointer;
}

/* Reset */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: 'JetBrains Mono', monospace;
  background: var(--bg);
  color: var(--text-primary);
  cursor: var(--cursor-hud-default);
}

html, body, #root {
  height: 100%;
  overflow: hidden;
}

/* Cursors */
html, body, *:not(input):not(textarea):not(select):not([contenteditable]) {
  cursor: var(--cursor-hud-default);
}
button:not(:disabled), a, [role="button"], .clickable {
  cursor: var(--cursor-hud-pointer) !important;
}
button:disabled { cursor: not-allowed !important; }

/* ===== LAYOUT ===== */
.app-root {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  background: var(--bg);
}

.app-main {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* ===== HUD HEADER ===== */
.hud-header {
  position: relative;
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.625rem 1.25rem;
  background: var(--surface-1);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  z-index: 10;
}

.hud-header-logo {
  font-size: 0.8125rem;
  font-weight: 700;
  color: var(--text-primary);
  text-transform: uppercase;
  letter-spacing: 0.15em;
}

.hud-header-meta {
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.hud-header-stats {
  margin-left: auto;
  display: flex;
  gap: 1.5rem;
  align-items: center;
}

.hud-header-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.125rem;
}

.hud-header-stat-label {
  font-size: 0.5625rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.hud-header-stat-value {
  font-size: 0.875rem;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1;
}

.hud-header-stat-value.accent { color: var(--accent); }
.hud-header-stat-value.danger { color: #ef4444; }
.hud-header-stat-value.success { color: #10b981; }

/* ===== CORNER DECORATIONS ===== */
.corner { position: absolute; width: 8px; height: 8px; z-index: 2; pointer-events: none; }
.corner::before, .corner::after { content: ''; position: absolute; background: var(--text-primary); }
.corner.accent::before, .corner.accent::after { background: var(--accent); }
.corner.tl { top: -1px; left: -1px; }
.corner.tr { top: -1px; right: -1px; }
.corner.bl { bottom: -1px; left: -1px; }
.corner.br { bottom: -1px; right: -1px; }
.corner.tl::before { width: 8px; height: 1px; top: 0; left: 0; }
.corner.tl::after  { width: 1px; height: 8px; top: 0; left: 0; }
.corner.tr::before { width: 8px; height: 1px; top: 0; right: 0; }
.corner.tr::after  { width: 1px; height: 8px; top: 0; right: 0; }
.corner.bl::before { width: 8px; height: 1px; bottom: 0; left: 0; }
.corner.bl::after  { width: 1px; height: 8px; bottom: 0; left: 0; }
.corner.br::before { width: 8px; height: 1px; bottom: 0; right: 0; }
.corner.br::after  { width: 1px; height: 8px; bottom: 0; right: 0; }

/* ===== HUD PANEL ===== */
.hud-panel {
  position: relative;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--border);
  padding: 1rem;
}

.hud-panel.compact { padding: 0.625rem 0.875rem; }

.hud-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--border);
}

.hud-panel-title {
  font-size: 0.625rem;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.15em;
}

.hud-panel-status {
  font-size: 0.5625rem;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

/* ===== STAT CARD ===== */
.stat-card {
  position: relative;
  background: var(--surface-1);
  border: 1px solid var(--border);
  padding: 0.75rem 1rem;
}

.stat-label {
  font-size: 0.5625rem;
  font-weight: 500;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.15em;
  margin-bottom: 0.375rem;
}

.stat-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1;
}

.stat-value.accent { color: var(--accent); }
.stat-value.danger { color: #ef4444; }
.stat-value.success { color: #10b981; }

.stat-unit {
  font-size: 0.75rem;
  font-weight: 400;
  color: var(--text-tertiary);
  margin-left: 0.125rem;
}

/* ===== BADGE ===== */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.5rem;
  font-size: 0.5625rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  background: var(--surface-2);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  white-space: nowrap;
}

.badge.success { background: rgba(16,185,129,0.1); border-color: rgba(16,185,129,0.3); color: #10b981; }
.badge.warning { background: rgba(245,158,11,0.1); border-color: rgba(245,158,11,0.3); color: #d97706; }
.badge.danger  { background: rgba(239,68,68,0.1);  border-color: rgba(239,68,68,0.3);  color: #ef4444; }
.badge.accent  { background: var(--accent-soft);   border-color: rgba(249,115,22,0.3); color: var(--accent); }
.badge.info    { background: rgba(59,130,246,0.1); border-color: rgba(59,130,246,0.3); color: #3b82f6; }
.badge.violet  { background: rgba(167,139,250,0.1);border-color: rgba(167,139,250,0.3);color: #a78bfa; }

/* ===== BUTTON CLIPPED ===== */
.btn-clipped {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.625rem 1.25rem;
  background: transparent;
  border: none;
  color: var(--text-primary);
  font-family: inherit;
  font-size: 0.75rem;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  cursor: pointer;
  transition: all 0.2s ease;
  z-index: 0;
  --btn-cut: 7px;
  --btn-border-color: var(--border-strong);
  --btn-fill: var(--bg);
  clip-path: polygon(0 0, calc(100% - var(--btn-cut)) 0, 100% var(--btn-cut), 100% 100%, var(--btn-cut) 100%, 0 calc(100% - var(--btn-cut)));
}

.btn-clipped::before, .btn-clipped::after { content: ''; position: absolute; inset: 0; pointer-events: none; }
.btn-clipped::before { background: var(--btn-border-color); z-index: -2; }
.btn-clipped::after {
  inset: 1px;
  background: var(--btn-fill);
  clip-path: polygon(0 0, calc(100% - var(--btn-cut)) 0, 100% var(--btn-cut), 100% 100%, var(--btn-cut) 100%, 0 calc(100% - var(--btn-cut)));
  z-index: -1;
  transition: background-color 0.2s ease;
}

.btn-clipped:hover { --btn-border-color: var(--text-tertiary); --btn-fill: var(--surface-2); }
.btn-clipped:disabled { opacity: 0.4; cursor: not-allowed !important; }

.btn-clipped.primary {
  --btn-border-color: var(--accent);
  --btn-fill: var(--accent);
  color: #ffffff;
}
.btn-clipped.primary:hover:not(:disabled) {
  --btn-border-color: #ea580c;
  --btn-fill: #ea580c;
}

.btn-clipped.success {
  --btn-border-color: #10b981;
  --btn-fill: #10b981;
  color: #ffffff;
}
.btn-clipped.success:hover:not(:disabled) {
  --btn-border-color: #059669;
  --btn-fill: #059669;
}

.btn-clipped.danger {
  --btn-border-color: #ef4444;
  --btn-fill: #ef4444;
  color: #ffffff;
}

.btn-clipped.small { padding: 0.375rem 0.75rem; font-size: 0.6875rem; }
.btn-clipped.full-width { width: 100%; }

/* ===== PROGRESS BAR ===== */
.progress-bar {
  position: relative;
  width: 100%;
  height: 3px;
  background: var(--surface-3);
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #ea580c, var(--accent), #fb923c);
  transition: width 0.3s ease;
}

/* ===== SCROLLBAR ===== */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: var(--surface-2); }
::-webkit-scrollbar-thumb { background: var(--border-strong); }
::-webkit-scrollbar-thumb:hover { background: var(--text-tertiary); }

/* ===== DIVIDER ===== */
.hud-divider {
  width: 100%;
  height: 1px;
  background: var(--border);
  margin: 0.75rem 0;
}

/* ===== ANIMATIONS ===== */
@keyframes nodeEnter {
  from { opacity: 0; transform: scale(0.3); }
  to   { opacity: 1; transform: scale(1); }
}

@keyframes edgeEnter {
  from { opacity: 0; }
  to   { opacity: 1; }
}

@keyframes wipeIn {
  from { clip-path: inset(0 100% 0 0); }
  to   { clip-path: inset(0 0 0 0); }
}

@keyframes slideUpFade {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

.anim-node-enter {
  animation: nodeEnter 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

.anim-wipe-in {
  animation: wipeIn 0.5s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

.anim-slide-up {
  animation: slideUpFade 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

/* ===== GRAPH CONTAINER ===== */
.graph-container {
  flex: 1;
  position: relative;
  overflow: hidden;
  background: var(--bg);
}

.graph-container svg {
  width: 100%;
  height: 100%;
}

.graph-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 0.5rem;
  color: var(--text-tertiary);
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.graph-empty-icon {
  font-size: 2rem;
  opacity: 0.3;
}

/* ===== TOOLTIP ===== */
.node-tooltip {
  position: fixed;
  z-index: 100;
  min-width: 180px;
  max-width: 240px;
  pointer-events: none;
  animation: slideUpFade 0.2s ease forwards;
}

/* ===== LEFT PANEL ===== */
.panel-left {
  width: 200px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* ===== RIGHT PANEL ===== */
.panel-right {
  width: 260px;
  flex-shrink: 0;
  border-left: 1px solid var(--border);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

/* ===== TIMELINE ===== */
.event-timeline {
  height: 130px;
  flex-shrink: 0;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  background: var(--surface-1);
}

.event-timeline-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.375rem 0.875rem;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.event-timeline-title {
  font-size: 0.5625rem;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.15em;
}

.event-timeline-count {
  font-size: 0.5625rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.event-timeline-track {
  display: flex;
  gap: 0.5rem;
  padding: 0.5rem 0.875rem;
  overflow-x: auto;
  flex: 1;
  align-items: stretch;
  scrollbar-width: thin;
}

.event-card {
  flex-shrink: 0;
  width: 120px;
  border: 1px solid var(--border);
  background: var(--surface-1);
  padding: 0.4rem 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  position: relative;
  clip-path: inset(0 100% 0 0);
}

.event-card.visible {
  clip-path: inset(0 0 0 0);
  transition: clip-path 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

.event-card-type {
  font-size: 0.5rem;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.event-card-agent {
  font-size: 0.625rem;
  font-weight: 500;
  color: var(--text-primary);
  font-family: 'JetBrains Mono', monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.event-card-round {
  font-size: 0.5rem;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

/* ===== RUN SELECTOR ===== */
.run-list {
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  max-height: 200px;
  border-bottom: 1px solid var(--border);
}

.run-item {
  position: relative;
  padding: 0.5rem 0.875rem;
  border-bottom: 1px solid var(--border);
  cursor: var(--cursor-hud-pointer) !important;
  transition: background 0.15s;
  font-size: 0.625rem;
  color: var(--text-secondary);
}

.run-item:hover { background: var(--surface-2); }
.run-item.selected { background: var(--accent-soft); color: var(--accent); }
.run-item.selected::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 2px;
  background: var(--accent);
}

.run-item-name {
  font-weight: 500;
  letter-spacing: 0.05em;
  margin-bottom: 0.125rem;
}

.run-item-meta {
  color: var(--text-tertiary);
  font-size: 0.5625rem;
}

/* ===== SPEED SLIDER ===== */
.speed-slider-wrap {
  padding: 0.75rem 0.875rem;
  border-bottom: 1px solid var(--border);
}

.speed-slider-label {
  font-size: 0.5625rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 0.5rem;
  display: flex;
  justify-content: space-between;
}

input[type="range"] {
  width: 100%;
  height: 3px;
  appearance: none;
  background: var(--surface-3);
  outline: none;
}

input[type="range"]::-webkit-slider-thumb {
  appearance: none;
  width: 10px;
  height: 10px;
  background: var(--accent);
  cursor: var(--cursor-hud-pointer) !important;
  clip-path: polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%);
}

/* ===== AGENT PANEL ===== */
.agent-panel-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 0.375rem;
  color: var(--text-tertiary);
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  padding: 1rem;
  text-align: center;
}

.conn-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.25rem 0;
  border-bottom: 1px solid var(--border);
  font-size: 0.625rem;
}

.conn-item-name {
  color: var(--text-primary);
  font-family: 'JetBrains Mono', monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 120px;
}

.conn-item-strength {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.conn-item-strength.suspicious { color: #ef4444; }

.msg-item {
  display: flex;
  align-items: baseline;
  gap: 0.375rem;
  padding: 0.2rem 0;
  font-size: 0.5625rem;
  border-bottom: 1px solid var(--border);
  color: var(--text-secondary);
}

.msg-item-round { color: var(--accent); flex-shrink: 0; }
.msg-item-dir { color: var(--text-tertiary); flex-shrink: 0; }
.msg-item-agent { color: var(--text-primary); font-family: 'JetBrains Mono', monospace; }
.msg-item-private { color: #a78bfa; }

/* ===== ERROR / STATUS OVERLAY ===== */
.graph-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  z-index: 20;
}

.graph-overlay-inner {
  padding: 1rem 1.5rem;
  background: rgba(255,255,255,0.95);
  border: 1px solid var(--border);
  backdrop-filter: blur(8px);
  text-align: center;
  font-size: 0.75rem;
  color: var(--text-secondary);
}

/* ===== MODE INDICATOR ===== */
.mode-indicator {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.5625rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.mode-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--text-tertiary);
}

.mode-dot.live { background: #10b981; box-shadow: 0 0 6px rgba(16,185,129,0.6); }
.mode-dot.playback { background: var(--accent); }

/* ===== SECTION LABEL ===== */
.section-label {
  font-size: 0.5625rem;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.15em;
  padding: 0.5rem 0.875rem 0.375rem;
}
```

**Step 2: Проверить что файл создан**

```bash
wc -l /home/development/MAGISTRY/web/frontend/src/styles/hud.css
```

Ожидаем: >= 300 строк.

**Step 3: Commit**

```bash
cd /home/development/MAGISTRY/web/frontend
git add src/styles/hud.css
git commit -m "feat: add hud.css design system from ui-kit-light"
```

---

## Task 3: Переписать SimGraph.tsx на D3 SVG

**Files:**
- Modify: `web/frontend/src/components/SimGraph.tsx`

Эта задача — ключевая. Полностью заменяем react-force-graph-2d на D3.

**Step 1: Написать новый SimGraph.tsx**

Полностью замени содержимое `web/frontend/src/components/SimGraph.tsx`:

```tsx
import { useEffect, useRef, useState, useCallback } from 'react'
import * as d3 from 'd3'
import type { GraphEdge, GraphNode, SimEvent } from '../types'
import { SUSPICIOUS_THRESHOLD } from '../constants'
import { NodeTooltip } from './NodeTooltip'

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  events: SimEvent[]
  onNodeClick?: (nodeId: string) => void
  selectedNode?: string | null
}

interface D3Node extends d3.SimulationNodeDatum {
  id: string
  reputation: number
}

interface D3Link extends d3.SimulationLinkDatum<D3Node> {
  source: string | D3Node
  target: string | D3Node
  strength: number
  isPrivate: boolean
}

function nodeColor(id: string): string {
  if (id.startsWith('off_')) return '#ef4444'
  if (id.startsWith('biz_')) return '#3b82f6'
  if (id.startsWith('aud_')) return '#f97316'
  return '#6b7280'
}

function nodeRadius(reputation: number): number {
  return Math.max(5, Math.min(14, reputation * 0.7))
}

function edgeColor(strength: number, isPrivate: boolean): string {
  if (strength >= SUSPICIOUS_THRESHOLD) return '#ef4444'
  if (isPrivate) return '#a78bfa'
  return '#d3d3d3'
}

function edgeWidth(strength: number): number {
  return Math.min(5, 0.5 + strength * 0.4)
}

export function SimGraph({ nodes, edges, events, onNodeClick, selectedNode }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const simRef = useRef<d3.Simulation<D3Node, D3Link> | null>(null)
  const nodesRef = useRef<Map<string, D3Node>>(new Map())
  const [tooltip, setTooltip] = useState<{ node: D3Node; x: number; y: number } | null>(null)
  const selectedRef = useRef(selectedNode)

  // Строим Set приватных рёбер из событий
  const privateEdges = new Set<string>()
  for (const e of events) {
    if (e.event_type === 'message_sent' && e.payload.private) {
      const from = e.agent_id
      const to = e.payload.to_id as string
      if (from && to) privateEdges.add([from, to].sort().join('|'))
    }
  }

  // Синхронизируем ref selectedNode чтобы tick не захватывал устаревший closure
  useEffect(() => {
    selectedRef.current = selectedNode
    // Перерисовываем узлы при смене выделения
    if (!svgRef.current) return
    const svg = d3.select(svgRef.current)
    svg.selectAll<SVGCircleElement, D3Node>('circle.node')
      .attr('stroke', (d) => d.id === selectedNode ? '#f97316' : 'none')
      .attr('stroke-width', (d) => d.id === selectedNode ? 2.5 : 0)
  }, [selectedNode])

  // Инициализация D3 симуляции при монтировании
  useEffect(() => {
    const container = containerRef.current
    const svgEl = svgRef.current
    if (!container || !svgEl) return

    const { width, height } = container.getBoundingClientRect()

    const svg = d3.select(svgEl)
      .attr('width', width)
      .attr('height', height)

    // Группы в правильном порядке: рёбра под узлами
    svg.append('g').attr('class', 'links-group')
    svg.append('g').attr('class', 'nodes-group')
    svg.append('g').attr('class', 'labels-group')

    // Zoom
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 4])
      .on('zoom', (event) => {
        svg.select('g.links-group').attr('transform', event.transform)
        svg.select('g.nodes-group').attr('transform', event.transform)
        svg.select('g.labels-group').attr('transform', event.transform)
      })
    svg.call(zoom)

    // Симуляция
    simRef.current = d3.forceSimulation<D3Node>()
      .force('link', d3.forceLink<D3Node, D3Link>().id((d) => d.id).strength(0.08).distance(80))
      .force('charge', d3.forceManyBody().strength(-180))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(20))
      .alphaDecay(0.02)
      .velocityDecay(0.3)

    simRef.current.on('tick', () => {
      const s = d3.select(svgRef.current)
      s.selectAll<SVGLineElement, D3Link>('line.edge')
        .attr('x1', (d) => (d.source as D3Node).x ?? 0)
        .attr('y1', (d) => (d.source as D3Node).y ?? 0)
        .attr('x2', (d) => (d.target as D3Node).x ?? 0)
        .attr('y2', (d) => (d.target as D3Node).y ?? 0)

      s.selectAll<SVGCircleElement, D3Node>('circle.node')
        .attr('cx', (d) => d.x ?? 0)
        .attr('cy', (d) => d.y ?? 0)

      s.selectAll<SVGTextElement, D3Node>('text.node-label')
        .attr('x', (d) => d.x ?? 0)
        .attr('y', (d) => (d.y ?? 0) + nodeRadius(d.reputation) + 12)
    })

    // ResizeObserver
    const ro = new ResizeObserver(() => {
      const { width: w, height: h } = container.getBoundingClientRect()
      svg.attr('width', w).attr('height', h)
      simRef.current?.force('center', d3.forceCenter(w / 2, h / 2))
      simRef.current?.alpha(0.3).restart()
    })
    ro.observe(container)

    return () => {
      ro.disconnect()
      simRef.current?.stop()
      svg.selectAll('*').remove()
    }
  }, [])

  // Обновление данных при изменении nodes/edges
  useEffect(() => {
    const sim = simRef.current
    const svgEl = svgRef.current
    if (!sim || !svgEl) return

    const svg = d3.select(svgEl)

    // Строим новый Map узлов, сохраняя позиции существующих
    const newNodesMap = new Map<string, D3Node>()
    for (const n of nodes) {
      const existing = nodesRef.current.get(n.id)
      if (existing) {
        existing.reputation = n.reputation
        newNodesMap.set(n.id, existing)
      } else {
        newNodesMap.set(n.id, { id: n.id, reputation: n.reputation })
      }
    }
    nodesRef.current = newNodesMap
    const d3Nodes = Array.from(newNodesMap.values())

    // Строим рёбра
    const d3Links: D3Link[] = edges.map((e) => ({
      source: e.source,
      target: e.target,
      strength: e.strength,
      isPrivate: privateEdges.has([e.source, e.target].sort().join('|')),
    }))

    // === РЁБРА ===
    const linkSel = svg.select('g.links-group')
      .selectAll<SVGLineElement, D3Link>('line.edge')
      .data(d3Links, (d) => `${String(d.source)}|${String(d.target)}`)

    linkSel.exit().remove()

    const linkEnter = linkSel.enter()
      .append('line')
      .attr('class', 'edge')
      .style('opacity', 0)
      .transition().duration(400)
      .style('opacity', 1)

    // После transition нужно обновить атрибуты на selection (не transition)
    svg.select('g.links-group')
      .selectAll<SVGLineElement, D3Link>('line.edge')
      .attr('stroke', (d) => edgeColor(d.strength, d.isPrivate))
      .attr('stroke-width', (d) => edgeWidth(d.strength))
      .attr('stroke-opacity', 0.7)

    // === УЗЛЫ ===
    const nodeSel = svg.select('g.nodes-group')
      .selectAll<SVGCircleElement, D3Node>('circle.node')
      .data(d3Nodes, (d) => d.id)

    nodeSel.exit().remove()

    nodeSel.enter()
      .append('circle')
      .attr('class', 'node')
      .attr('r', 0)
      .attr('fill', (d) => nodeColor(d.id))
      .attr('stroke', 'none')
      .attr('stroke-width', 2.5)
      .style('cursor', 'pointer')
      .call(
        d3.drag<SVGCircleElement, D3Node>()
          .on('start', (event, d) => {
            if (!event.active) sim.alphaTarget(0.3).restart()
            d.fx = d.x; d.fy = d.y
          })
          .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y })
          .on('end', (event, d) => {
            if (!event.active) sim.alphaTarget(0)
            d.fx = null; d.fy = null
          })
      )
      .on('click', (_event, d) => { onNodeClick?.(d.id) })
      .on('mouseenter', (event, d) => {
        setTooltip({ node: d, x: event.clientX, y: event.clientY })
      })
      .on('mousemove', (event) => {
        setTooltip((prev) => prev ? { ...prev, x: event.clientX, y: event.clientY } : null)
      })
      .on('mouseleave', () => setTooltip(null))
      .transition().duration(350)
      .attr('r', (d) => nodeRadius(d.reputation))

    // Обновляем существующие узлы (radius мог измениться)
    svg.select('g.nodes-group')
      .selectAll<SVGCircleElement, D3Node>('circle.node')
      .attr('fill', (d) => nodeColor(d.id))
      .attr('stroke', (d) => d.id === selectedRef.current ? '#f97316' : 'none')
      .transition().duration(200)
      .attr('r', (d) => nodeRadius(d.reputation))

    // === ЛЕЙБЛЫ ===
    const labelSel = svg.select('g.labels-group')
      .selectAll<SVGTextElement, D3Node>('text.node-label')
      .data(d3Nodes, (d) => d.id)

    labelSel.exit().remove()

    labelSel.enter()
      .append('text')
      .attr('class', 'node-label')
      .style('opacity', 0)
      .transition().duration(400)
      .style('opacity', 1)

    svg.select('g.labels-group')
      .selectAll<SVGTextElement, D3Node>('text.node-label')
      .text((d) => d.id)
      .attr('text-anchor', 'middle')
      .attr('font-family', "'JetBrains Mono', monospace")
      .attr('font-size', '9px')
      .attr('fill', '#666666')
      .style('pointer-events', 'none')
      .style('user-select', 'none')

    // Обновляем симуляцию
    sim.nodes(d3Nodes)
    ;(sim.force('link') as d3.ForceLink<D3Node, D3Link>).links(d3Links)
    sim.alpha(0.3).restart()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, events])

  if (nodes.length === 0) {
    return (
      <div ref={containerRef} className="graph-container">
        <div className="graph-empty">
          <div className="graph-empty-icon">◈</div>
          <div>Нет данных</div>
          <div>Выберите прогон или запустите live-мониторинг</div>
        </div>
      </div>
    )
  }

  return (
    <div ref={containerRef} className="graph-container">
      <svg ref={svgRef} style={{ width: '100%', height: '100%' }} />
      {tooltip && (
        <NodeTooltip
          node={tooltip.node}
          edges={edges}
          x={tooltip.x}
          y={tooltip.y}
        />
      )}
    </div>
  )
}
```

**Step 2: Проверить TypeScript**

```bash
cd /home/development/MAGISTRY/web/frontend
npx tsc --noEmit 2>&1 | head -40
```

Ожидаем: ошибки только про NodeTooltip (ещё не создан) — это нормально.

---

## Task 4: Создать NodeTooltip.tsx

**Files:**
- Create: `web/frontend/src/components/NodeTooltip.tsx`

**Step 1: Создать компонент**

```tsx
import type { GraphEdge } from '../types'
import { SUSPICIOUS_THRESHOLD } from '../constants'

interface D3Node {
  id: string
  reputation: number
  x?: number
  y?: number
}

interface Props {
  node: D3Node
  edges: GraphEdge[]
  x: number
  y: number
}

function roleLabel(id: string): string {
  if (id.startsWith('off_')) return 'Чиновник'
  if (id.startsWith('biz_')) return 'Подрядчик'
  if (id.startsWith('aud_')) return 'Аудитор'
  return 'Агент'
}

function roleBadgeClass(id: string): string {
  if (id.startsWith('off_')) return 'badge danger'
  if (id.startsWith('biz_')) return 'badge info'
  if (id.startsWith('aud_')) return 'badge accent'
  return 'badge'
}

export function NodeTooltip({ node, edges, x, y }: Props) {
  const connections = edges.filter(
    (e) => e.source === node.id || e.target === node.id
  )
  const suspiciousCount = connections.filter(
    (e) => e.strength >= SUSPICIOUS_THRESHOLD
  ).length

  // Смещаем тултип чтобы не перекрывал курсор
  const style: React.CSSProperties = {
    left: x + 14,
    top: y - 10,
  }

  // Если тултип выходит за правый край — сдвигаем влево
  if (x > window.innerWidth - 260) {
    style.left = x - 200
  }

  return (
    <div className="node-tooltip hud-panel" style={style}>
      <div className="corner tl accent" />
      <div className="corner tr accent" />
      <div className="corner bl" />
      <div className="corner br" />

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
        <span style={{ fontSize: '0.625rem', fontWeight: 700, letterSpacing: '0.05em' }}>
          {node.id}
        </span>
        <span className={roleBadgeClass(node.id)}>{roleLabel(node.id)}</span>
      </div>

      <div className="hud-divider" />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <div className="stat-card" style={{ padding: '0.375rem 0.5rem' }}>
          <div className="stat-label">Репутация</div>
          <div className="stat-value" style={{ fontSize: '1rem' }}>
            {node.reputation.toFixed(1)}
          </div>
        </div>
        <div className="stat-card" style={{ padding: '0.375rem 0.5rem' }}>
          <div className="stat-label">Связи</div>
          <div className={`stat-value ${suspiciousCount > 0 ? 'danger' : ''}`} style={{ fontSize: '1rem' }}>
            {connections.length}
            {suspiciousCount > 0 && (
              <span className="stat-unit" style={{ color: '#ef4444', fontSize: '0.5rem' }}>
                {' '}⚠{suspiciousCount}
              </span>
            )}
          </div>
        </div>
      </div>

      {connections.length > 0 && (
        <div style={{ fontSize: '0.5rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
          {connections
            .sort((a, b) => b.strength - a.strength)
            .slice(0, 3)
            .map((c, i) => {
              const other = c.source === node.id ? c.target : c.source
              const sus = c.strength >= SUSPICIOUS_THRESHOLD
              return (
                <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '0.15rem 0', borderBottom: '1px solid var(--border)' }}>
                  <span style={{ color: 'var(--text-primary)' }}>{String(other)}</span>
                  <span style={{ color: sus ? '#ef4444' : 'var(--text-tertiary)' }}>
                    {c.strength.toFixed(1)}
                  </span>
                </div>
              )
            })}
        </div>
      )}
    </div>
  )
}
```

---

## Task 5: Переписать EventTimeline.tsx (горизонтальная лента)

**Files:**
- Create: `web/frontend/src/components/EventTimeline.tsx`
- Note: EventFeed.tsx оставляем (используется в AgentPanel через App), потом удалим из App

**Step 1: Создать EventTimeline.tsx**

```tsx
import { useEffect, useRef, useState } from 'react'
import type { SimEvent } from '../types'

const EVENT_LABELS: Record<string, string> = {
  message_sent: 'MSG',
  graph_updated: 'GRAPH',
  reputation_modified: 'REP',
  case_opened: 'CASE+',
  case_resolved: 'CASE✓',
  case_modified: 'CASE~',
  proposal_submitted: 'PROP',
  evidence_added: 'EVID+',
  evidence_removed: 'EVID-',
  arbiter_approved: 'ARB+',
  arbiter_rejected: 'ARB-',
  auto_transition: 'AUTO',
  world_event: 'WORLD',
}

const EVENT_BADGE_CLASS: Record<string, string> = {
  message_sent: 'badge',
  reputation_modified: 'badge warning',
  case_opened: 'badge accent',
  case_resolved: 'badge success',
  arbiter_approved: 'badge success',
  arbiter_rejected: 'badge danger',
  world_event: 'badge info',
}

interface Props {
  events: SimEvent[]
  selectedAgent?: string | null
}

export function EventTimeline({ events, selectedAgent }: Props) {
  const trackRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)
  const [visibleIds, setVisibleIds] = useState<Set<number>>(new Set())

  const filtered = selectedAgent
    ? events.filter(
        (e) =>
          e.agent_id === selectedAgent ||
          e.payload.to_id === selectedAgent ||
          e.payload.target === selectedAgent
      )
    : events

  // Анимируем новые карточки
  useEffect(() => {
    const timer = setTimeout(() => {
      setVisibleIds(new Set(filtered.map((_, i) => i)))
    }, 50)
    return () => clearTimeout(timer)
  }, [filtered.length])

  // Автоскролл к последнему событию
  useEffect(() => {
    if (!autoScroll || !trackRef.current) return
    trackRef.current.scrollLeft = trackRef.current.scrollWidth
  }, [events.length, autoScroll])

  return (
    <div className="event-timeline">
      <div className="event-timeline-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span className="event-timeline-title">
            {selectedAgent ? `Фильтр: ${selectedAgent}` : 'Лента событий'}
          </span>
          {selectedAgent && (
            <span className="badge accent">{selectedAgent}</span>
          )}
        </div>
        <span className="event-timeline-count">{filtered.length} событий</span>
      </div>

      <div
        ref={trackRef}
        className="event-timeline-track"
        onMouseEnter={() => setAutoScroll(false)}
        onMouseLeave={() => setAutoScroll(true)}
      >
        {filtered.map((e, i) => (
          <div
            key={i}
            className={`event-card ${visibleIds.has(i) ? 'visible' : ''}`}
          >
            <div className="corner tl" />
            <div className="corner br" />
            <div className="event-card-round">R{e.round}</div>
            <div>
              <span className={EVENT_BADGE_CLASS[e.event_type] ?? 'badge'}>
                {EVENT_LABELS[e.event_type] ?? e.event_type.slice(0, 6)}
              </span>
            </div>
            <div className="event-card-agent">{e.agent_id || '—'}</div>
            {e.payload.private && (
              <span className="badge violet" style={{ fontSize: '0.45rem' }}>PRIV</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
```

---

## Task 6: Рестайл AgentPanel.tsx

**Files:**
- Modify: `web/frontend/src/components/AgentPanel.tsx`

**Step 1: Заменить содержимое файла**

```tsx
import type { GraphEdge, GraphNode, SimEvent } from '../types'
import { SUSPICIOUS_THRESHOLD } from '../constants'

interface Props {
  nodeId: string | null
  nodes: GraphNode[]
  edges: GraphEdge[]
  events: SimEvent[]
}

function roleLabel(id: string): string {
  if (id.startsWith('off_')) return 'Чиновник'
  if (id.startsWith('biz_')) return 'Подрядчик'
  if (id.startsWith('aud_')) return 'Аудитор'
  return 'Агент'
}

function roleBadgeClass(id: string): string {
  if (id.startsWith('off_')) return 'badge danger'
  if (id.startsWith('biz_')) return 'badge info'
  if (id.startsWith('aud_')) return 'badge accent'
  return 'badge'
}

export function AgentPanel({ nodeId, nodes, edges, events }: Props) {
  if (!nodeId) {
    return (
      <div className="agent-panel-empty">
        <div style={{ fontSize: '1.5rem', opacity: 0.3 }}>◈</div>
        <div>Выберите агента</div>
        <div style={{ opacity: 0.6 }}>Кликните на узел графа</div>
      </div>
    )
  }

  const node = nodes.find((n) => n.id === nodeId)
  const connections = edges
    .filter((e) => e.source === nodeId || e.target === nodeId)
    .sort((a, b) => b.strength - a.strength)
  const messages = events
    .filter(
      (e) =>
        e.event_type === 'message_sent' &&
        (e.agent_id === nodeId || e.payload.to_id === nodeId)
    )
    .slice(-20)
    .reverse()

  const suspiciousCount = connections.filter((c) => c.strength >= SUSPICIOUS_THRESHOLD).length
  const reputation = node?.reputation ?? 0
  const reputationClass = reputation < 5 ? 'danger' : reputation < 8 ? '' : 'success'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Заголовок */}
      <div className="hud-panel compact" style={{ borderBottom: '1px solid var(--border)', borderLeft: 'none', borderRight: 'none', borderTop: 'none' }}>
        <div className="corner tl accent" />
        <div className="corner tr" />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
          <span style={{ fontSize: '0.75rem', fontWeight: 700, letterSpacing: '0.05em' }}>
            {nodeId}
          </span>
          <span className={roleBadgeClass(nodeId)}>{roleLabel(nodeId)}</span>
        </div>
        {/* Stat-карточки */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
          <div className="stat-card" style={{ padding: '0.4rem 0.5rem' }}>
            <div className="stat-label">Репутация</div>
            <div className={`stat-value ${reputationClass}`} style={{ fontSize: '1.125rem' }}>
              {reputation.toFixed(1)}
            </div>
          </div>
          <div className="stat-card" style={{ padding: '0.4rem 0.5rem' }}>
            <div className="stat-label">Связи</div>
            <div className={`stat-value ${suspiciousCount > 0 ? 'danger' : ''}`} style={{ fontSize: '1.125rem' }}>
              {connections.length}
              {suspiciousCount > 0 && (
                <span className="stat-unit" style={{ color: '#ef4444', fontSize: '0.5rem' }}>
                  {' '}⚠{suspiciousCount}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Связи */}
      <div style={{ flex: '0 0 auto', borderBottom: '1px solid var(--border)', padding: '0 0.875rem' }}>
        <div className="section-label" style={{ padding: '0.5rem 0 0.375rem' }}>
          Связи ({connections.length})
        </div>
        <div style={{ maxHeight: '100px', overflowY: 'auto' }}>
          {connections.map((c, i) => {
            const other = c.source === nodeId ? c.target : c.source
            const sus = c.strength >= SUSPICIOUS_THRESHOLD
            return (
              <div key={i} className="conn-item">
                <span className="conn-item-name">{String(other)}</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
                  <span className={`conn-item-strength ${sus ? 'suspicious' : ''}`}>
                    {c.strength.toFixed(1)}
                  </span>
                  {sus && <span className="badge danger" style={{ fontSize: '0.45rem' }}>⚠</span>}
                </div>
              </div>
            )
          })}
          {connections.length === 0 && (
            <div style={{ fontSize: '0.6rem', color: 'var(--text-tertiary)', padding: '0.25rem 0' }}>
              Нет связей
            </div>
          )}
        </div>
      </div>

      {/* Сообщения */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <div className="section-label">Сообщения ({messages.length})</div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 0.875rem 0.5rem' }}>
          {messages.map((e, i) => {
            const isFrom = e.agent_id === nodeId
            const other = isFrom ? e.payload.to_id : e.agent_id
            return (
              <div key={i} className={`msg-item ${e.payload.private ? 'msg-item-private' : ''}`}>
                <span className="msg-item-round">R{e.round}</span>
                <span className="msg-item-dir">{isFrom ? '→' : '←'}</span>
                <span className="msg-item-agent">{String(other)}</span>
                {e.payload.private && (
                  <span className="badge violet" style={{ fontSize: '0.45rem', marginLeft: 'auto' }}>PRIV</span>
                )}
              </div>
            )
          })}
          {messages.length === 0 && (
            <div style={{ fontSize: '0.6rem', color: 'var(--text-tertiary)', paddingTop: '0.25rem' }}>
              Нет сообщений
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
```

---

## Task 7: Рестайл RunSelector.tsx

**Files:**
- Modify: `web/frontend/src/components/RunSelector.tsx`

**Step 1: Заменить содержимое файла**

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

  const isIdle = mode === 'idle'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Заголовок */}
      <div style={{ padding: '0.5rem 0.875rem', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <div style={{ fontSize: '0.5625rem', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.15em' }}>
          Прогоны ({runs.length})
        </div>
      </div>

      {/* Список прогонов */}
      <div className="run-list" style={{ flex: '0 0 auto' }}>
        {runs.map((r) => (
          <div
            key={r.name}
            className={`run-item ${selected?.name === r.name ? 'selected' : ''}`}
            onClick={() => setSelected(r)}
          >
            <div className="run-item-name">
              {r.scenario}/{r.governance}
              {r.seed !== null ? `/s${r.seed}` : ''}
            </div>
            <div className="run-item-meta">{r.size_kb} KB</div>
          </div>
        ))}
        {runs.length === 0 && (
          <div style={{ padding: '0.75rem 0.875rem', fontSize: '0.5625rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
            Нет прогонов
          </div>
        )}
      </div>

      {/* Скорость */}
      <div className="speed-slider-wrap">
        <div className="speed-slider-label">
          <span>Скорость</span>
          <span style={{ color: 'var(--accent)', fontWeight: 600 }}>{speed.toFixed(1)}x</span>
        </div>
        <input
          type="range"
          min={0.5}
          max={20}
          step={0.5}
          value={speed}
          onChange={(e) => onSpeedChange(Number(e.target.value))}
        />
      </div>

      {/* Кнопки */}
      <div style={{ padding: '0.5rem 0.875rem', display: 'flex', flexDirection: 'column', gap: '0.5rem', borderTop: '1px solid var(--border)' }}>
        <button
          className="btn-clipped primary full-width"
          disabled={!selected || !isIdle}
          onClick={() => selected && onPlayback(selected, speed)}
        >
          ▶ Воспроизвести
        </button>

        <button
          className="btn-clipped success full-width"
          disabled={!isIdle}
          onClick={onLive}
        >
          ● Live
        </button>
      </div>

      {/* Статус режима */}
      {!isIdle && (
        <div style={{ padding: '0.375rem 0.875rem', borderTop: '1px solid var(--border)' }}>
          <div className="mode-indicator">
            <div className={`mode-dot ${mode}`} />
            <span>{mode === 'live' ? 'Live-мониторинг' : 'Воспроизведение'}</span>
          </div>
        </div>
      )}
    </div>
  )
}
```

---

## Task 8: Переписать App.tsx

**Files:**
- Modify: `web/frontend/src/App.tsx`

**Step 1: Заменить содержимое App.tsx**

```tsx
import { useMemo, useState } from 'react'
import { useSimulation } from './hooks/useSimulation'
import { SimGraph } from './components/SimGraph'
import { EventTimeline } from './components/EventTimeline'
import { AgentPanel } from './components/AgentPanel'
import { RunSelector } from './components/RunSelector'
import type { RunInfo } from './types'
import './styles/hud.css'

export default function App() {
  const { state, mode, startPlayback, startLive, disconnect } = useSimulation()
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [speed, setSpeed] = useState(3.0)

  const privateRatio = useMemo(() => {
    if (!state.events.length) return 0
    const msgs = state.events.filter((e) => e.event_type === 'message_sent')
    const priv = msgs.filter((e) => e.payload.private).length
    return (priv / Math.max(1, msgs.length)) * 100
  }, [state.events])

  return (
    <div className="app-root">
      {/* HUD Header */}
      <header className="hud-header">
        <div className="corner tl accent" />
        <div className="corner tr" />
        <div className="corner bl" />
        <div className="corner br accent" />

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <span className="hud-header-logo">MAGISTRY</span>
          {state.meta && (
            <span className="hud-header-meta">
              {state.meta.scenario} / {state.meta.governance}
              {state.meta.seed !== null ? ` / seed${state.meta.seed}` : ''}
            </span>
          )}
        </div>

        <div className="hud-header-stats">
          <div className="hud-header-stat">
            <span className="hud-header-stat-label">Раунд</span>
            <span className="hud-header-stat-value accent">{state.currentRound}</span>
          </div>
          <div className="hud-header-stat">
            <span className="hud-header-stat-label">Событий</span>
            <span className="hud-header-stat-value">{state.events.length}</span>
          </div>
          <div className="hud-header-stat">
            <span className="hud-header-stat-label">Приватных</span>
            <span className={`hud-header-stat-value ${privateRatio > 50 ? 'danger' : ''}`}>
              {privateRatio.toFixed(0)}%
            </span>
          </div>
          <div className="hud-header-stat">
            <span className="hud-header-stat-label">Агентов</span>
            <span className="hud-header-stat-value">{state.nodes.length}</span>
          </div>

          {state.done && <span className="badge success">✓ Завершено</span>}
          {state.error && <span className="badge danger">⚠ Ошибка</span>}

          {mode !== 'idle' && (
            <button className="btn-clipped danger small" onClick={disconnect}>
              Стоп
            </button>
          )}
        </div>
      </header>

      {/* Main */}
      <div className="app-main">
        {/* Left */}
        <aside className="panel-left">
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
        </aside>

        {/* Graph */}
        <main style={{ flex: 1, position: 'relative', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <SimGraph
            nodes={state.nodes}
            edges={state.edges}
            events={state.events}
            onNodeClick={setSelectedNode}
            selectedNode={selectedNode}
          />
          {state.error && (
            <div className="graph-overlay">
              <div className="graph-overlay-inner">
                <div className="badge danger" style={{ marginBottom: '0.5rem' }}>Ошибка</div>
                <div>{state.error}</div>
              </div>
            </div>
          )}
        </main>

        {/* Right */}
        <aside className="panel-right">
          <AgentPanel
            nodeId={selectedNode}
            nodes={state.nodes}
            edges={state.edges}
            events={state.events}
          />
        </aside>
      </div>

      {/* Timeline */}
      <EventTimeline
        events={state.events}
        selectedAgent={selectedNode}
      />
    </div>
  )
}
```

---

## Task 9: Обновить index.css и убрать конфликты Tailwind

**Files:**
- Modify: `web/frontend/src/index.css`

**Step 1: Очистить index.css**

Hud.css подключается в App.tsx через import. index.css должен содержать минимум:

```css
/* Tailwind utilities оставляем для возможных вспомогательных классов */
@tailwind base;
@tailwind utilities;
```

Убираем `@tailwind components` — он создаёт конфликты со стилями hud.css.

**Step 2: Проверить сборку**

```bash
cd /home/development/MAGISTRY/web/frontend
npm run build 2>&1
```

Ожидаем: сборка без ошибок.

**Step 3: Commit**

```bash
cd /home/development/MAGISTRY/web/frontend
git add -A
git commit -m "feat: restyle graph UI to HUD/light theme with D3 force graph

- Replace react-force-graph-2d with D3 SVG simulation
- Add hud.css design system from ui-kit-light reference
- Add NodeTooltip with agent stats on hover
- Add EventTimeline horizontal strip with wipe-in animation
- Restyle AgentPanel, RunSelector, App.tsx to HUD/light theme
- Add drag, zoom, node/edge animation support"
```

---

## Task 10: Проверка в браузере

**Step 1: Запустить стек**

```bash
cd /home/development/MAGISTRY
bash web/start.sh
```

Убедиться что backend и frontend запущены.

**Step 2: Открыть в браузере и проверить**

Открой `http://localhost:5173` (или порт из vite) и проверь:

- [ ] Белый фон, JetBrains Mono, оранжевый акцент
- [ ] HUD-заголовок с угловыми декорациями
- [ ] Левая панель: список прогонов, слайдер скорости, clipped-кнопки
- [ ] Граф: D3 SVG, узлы с цветом по типу, рёбра
- [ ] Hover на узел → тултип с репутацией и связями
- [ ] Клик на узел → подсветка + правая панель обновляется
- [ ] Нижняя лента: карточки событий с анимацией
- [ ] Drag узлов, zoom графа работают
- [ ] Воспроизведение прогона работает (если есть данные)

**Step 3: Commit (если финальные правки были)**

```bash
cd /home/development/MAGISTRY/web/frontend
git add -A
git commit -m "fix: post-review UI tweaks"
```
