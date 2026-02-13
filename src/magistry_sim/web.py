"""Веб-дашборд реального времени для MAGISTRY.

FastAPI-приложение с WebSocket-стримингом событий симуляции.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.engine import SimulationEngine
from magistry_sim.llm import LLMProvider, MockLLMProvider
from magistry_sim.models import Event, GovernanceConfig
from magistry_sim.scenarios import SCENARIOS

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="MAGISTRY Dashboard")


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    """Отдать HTML-дашборд."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/api/scenarios")
async def list_scenarios() -> list[dict[str, Any]]:
    """Список доступных сценариев."""
    return [
        {
            "id": s.id.value,
            "title": s.title,
            "description": s.description,
            "ticks": s.ticks,
        }
        for s in SCENARIOS.values()
    ]


@app.get("/api/governance-modes")
async def list_governance_modes() -> list[dict[str, str]]:
    """Список режимов управления."""
    labels = {
        GovernanceMode.G0_BASELINE: "G0 -- Без контроля",
        GovernanceMode.G1_AUDIT: "G1 -- Аудитор",
        GovernanceMode.G2_AUDIT_REPUTATION: "G2 -- Аудитор + Репутация",
        GovernanceMode.G3_FULL: "G3 -- Полный (Аудитор + Репутация + Трибунал)",
    }
    return [
        {"id": mode.value, "label": labels.get(mode, mode.value)}
        for mode in GovernanceMode
    ]


@app.websocket("/ws/run")
async def ws_run(ws: WebSocket) -> None:
    """WebSocket-обработчик запуска симуляции с потоковой передачей событий."""
    await ws.accept()
    try:
        raw = await ws.receive_text()
        params = json.loads(raw)

        scenario_id = ScenarioId(params.get("scenario", "S1"))
        governance_mode = GovernanceMode(params.get("governance", "G3"))
        seed = int(params.get("seed", 42))
        use_mock = params.get("mock", True)

        governance = GovernanceConfig(mode=governance_mode)
        llm: LLMProvider = MockLLMProvider() if use_mock else _get_real_llm()

        queue: asyncio.Queue[Event | None] = asyncio.Queue()

        def on_event(event: Event) -> None:
            """Синхронный обратный вызов, складывающий событие в очередь."""
            queue.put_nowait(event)

        engine = SimulationEngine(governance=governance, llm=llm)

        async def run_simulation() -> dict[str, Any]:
            """Запустить симуляцию и вернуть итоговые метрики."""
            result = await engine.run(
                scenario_id=scenario_id,
                seed=seed,
                on_event=on_event,
            )
            queue.put_nowait(None)
            return {
                "scenario": result.scenario.value,
                "governance": result.governance.value,
                "seed": result.seed,
                "ticks": result.ticks,
                "total_events": len(result.events),
                "outcomes": [
                    {
                        "tick": o.tick,
                        "corruption": o.corruption,
                        "audit_risk": o.audit_risk,
                        "audit_flagged": o.audit_flagged,
                        "tribunal_triggered": o.tribunal_triggered,
                        "tribunal_guilty": o.tribunal_guilty,
                    }
                    for o in result.outcomes
                ],
            }

        sim_task = asyncio.create_task(run_simulation())

        while True:
            event = await queue.get()
            if event is None:
                break
            await ws.send_json({
                "type": "event",
                "tick": event.tick,
                "event_type": event.event_type,
                "payload": _serialize_payload(event.payload),
            })

        metrics = await sim_task
        await ws.send_json({"type": "run_complete", "metrics": metrics})

    except WebSocketDisconnect:
        logger.info("WebSocket-клиент отключился")
    except Exception as exc:
        logger.exception("Ошибка в WebSocket-обработчике")
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass


def _serialize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Сериализовать payload события в JSON-совместимый формат."""
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, float):
            if value != value:  # NaN
                result[key] = None
            else:
                result[key] = round(value, 4)
        elif isinstance(value, list):
            result[key] = [
                _serialize_item(item) for item in value
            ]
        else:
            result[key] = value
    return result


def _serialize_item(item: Any) -> Any:
    """Сериализовать элемент списка."""
    if isinstance(item, dict):
        return _serialize_payload(item)
    if isinstance(item, float):
        return round(item, 4) if item == item else None
    return item


def _get_real_llm() -> LLMProvider:
    """Получить реальный LLM-провайдер с загрузкой переменных окружения."""
    from dotenv import load_dotenv
    load_dotenv()

    from magistry_sim.llm import OpenAICompatibleProvider
    return OpenAICompatibleProvider()


def serve() -> None:
    """Точка входа для magistry-web."""
    import uvicorn
    uvicorn.run(
        "magistry_sim.web:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    serve()
