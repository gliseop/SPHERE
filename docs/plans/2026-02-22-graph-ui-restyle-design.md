# Дизайн: рестайл Graph UI под HUD/AIComPet

Дата: 2026-02-22
Ветка: feat/live-web-1

## Контекст

MAGISTRY — платформа симуляции AI-агентов с React + Vite фронтендом. Текущий интерфейс
использует тёмную тему (slate-900) и библиотеку react-force-graph-2d для визуализации
графа агентов. Задача — полный рестайл под светлую HUD/AIComPet эстетику из референса
`ui-kit-light.html`, с одновременным улучшением UX: тайтлайн событий, тултипы на узлах
графа, анимации появления элементов, stat-панели.

## Решение

Выбран подход с полной заменой react-force-graph-2d на d3-force + SVG внутри React-компонента.
Это даёт полный контроль над рендером: кастомные формы узлов, SVG-анимации, нативные HTML-тултипы,
таймлайн без ограничений библиотеки.

WebSocket-логика и хук useSimulation не затрагиваются — только визуальный слой.

## Архитектура

```
src/
  styles/
    hud.css           ← все CSS-переменные и компоненты из ui-kit-light
  components/
    SimGraph.tsx      ← D3 SVG граф (заменяет react-force-graph-2d)
    NodeTooltip.tsx   ← floating HUD-панель при hover на узле
    EventTimeline.tsx ← горизонтальная лента событий внизу (заменяет EventFeed)
    AgentPanel.tsx    ← рестайл: hud-panel с угловыми декорациями
    RunSelector.tsx   ← рестайл: btn-3d кнопки, stat-card
  App.tsx             ← новая компоновка
```

## Компоновка интерфейса

```
┌─────────────────────────────────────────────────────────────────┐
│ HUD HEADER: MAGISTRY | scenario/governance | Round Events Stats │
├──────────────┬─────────────────────────────┬────────────────────┤
│  LEFT PANEL  │       GRAPH (D3 SVG)        │   RIGHT PANEL      │
│  RunSelector │   force-directed граф       │   AgentPanel       │
│  + stat-card │   узлы с анимацией          │   hud-panel        │
│  файла       │   рёбра с силой             │   stat-card репут. │
│              │   тултип при hover          │   список связей    │
├──────────────┴─────────────────────────────┴────────────────────┤
│  EVENT TIMELINE: горизонт. лента, автоскролл, wipe-in анимация  │
└─────────────────────────────────────────────────────────────────┘
```

## Дизайн-токены (из ui-kit-light)

- Фон: `#ffffff`, surface-1: `rgba(255,255,255,0.98)`, surface-2: `#f5f5f5`
- Текст: primary `#000000`, secondary `#666666`, tertiary `#999999`
- Акцент: `#f97316` (оранжевый), accent-soft: `rgba(249,115,22,0.1)`
- Граница: `#eaeaea` (обычная), `#d3d3d3` (усиленная)
- Шрифт: JetBrains Mono
- HUD-курсор: кастомный SVG с прицелом

## Граф (SimGraph.tsx)

D3-симуляция: `d3.forceSimulation` + `forceLink` + `forceManyBody` + `forceCenter`.
SVG структура: `<g.links>` → `<line>` per edge, `<g.nodes>` → `<circle>` + `<text>`.

Цвета узлов:
- `off_*` → `#ef4444` (красный, оффшор)
- `biz_*` → `#3b82f6` (синий, бизнес)
- `aud_*` → `#f97316` (оранжевый, аудитор)

Ребро: толщина = `Math.log(strength + 1)`, цвет: подозрительные (strength ≥ порога) → красный,
приватные → `#a78bfa`, обычные → `#94a3b8`.

Анимация появления узла: CSS `@keyframes nodeEnter { from { opacity:0; r:0 } to { opacity:1 } }`.
Анимация появления ребра: `opacity 0→1` за 0.4s.

Выделенный узел: оранжевая обводка 2px + абсолютный `div` с HUD corner-декораторами
поверх SVG-элемента (позиционируется через `node.x/y` из симуляции).

## Тултип (NodeTooltip.tsx)

Floating `div.hud-panel` (glassmorphism) появляется при `mouseenter` на узел.
Содержит: имя агента (card-title), badge с типом, stat-card репутации, счётчик связей.
Позиционируется относительно SVG-контейнера через `getBoundingClientRect`.
Скрывается при `mouseleave` с задержкой 200ms (чтобы не мигал).

## Таймлайн событий (EventTimeline.tsx)

Горизонтальная лента высотой 140px с `overflow-x: auto` и кастомным скроллбаром.
Каждое событие — вертикальная карточка `hud-panel.compact`:
- иконка типа (message_sent, round_end, join, decision)
- агент (badge)
- краткий payload

Новые события появляются с `wipe-in` анимацией из ui-kit-light.
Автоскролл к последнему событию через `scrollLeft = scrollWidth`.
При hover на ленту — автоскролл приостанавливается.

## Статистика в HUD-панелях

Левая панель содержит 4 stat-card:
- Round (текущий раунд)
- Events (количество событий)
- Private% (доля приватных сообщений, badge danger если > 50%)
- Agents (количество агентов)

## Обработка ошибок

`state.error` → `hud-panel` с `badge.danger` поверх графа (абсолютное позиционирование,
центр по вертикали и горизонтали). `state.done` → badge.success в заголовке.

## Зависимости

Добавить: `d3` (npm).
Удалить из логики стилей: Tailwind-классы в компонентах (tailwind.config оставить,
убрать классы из JSX, использовать hud.css).

## Критерии готовности

- Все существующие функции работают (playback, live, RunSelector)
- Граф корректно отображает узлы и рёбра с D3
- Тултип появляется при hover
- EventTimeline отображает события с анимацией
- Интерфейс визуально соответствует ui-kit-light референсу
- Нет регрессий в WebSocket-логике
