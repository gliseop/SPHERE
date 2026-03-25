"""FastAPI-сервер для веб-интерфейса симуляций SPHERE.

Точка входа для ``uvicorn web.backend.main:app``. Создание маршрутов,
middleware и подключение роутеров вынесены в подмодули.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from .auth import validate_jwt_secret  # noqa: E402
from .database import init_db  # noqa: E402
from ._middleware import BodySizeLimitMiddleware  # noqa: E402
from .settings import ALLOWED_ORIGIN, FRONTEND_DIST, MAX_BODY_BYTES  # noqa: E402

# ---------------------------------------------------------------------------
# Routers (импорт отложен до создания app)
# ---------------------------------------------------------------------------
from .websocket import router as _ws_router  # noqa: E402
from .routes.auth import router as _auth_router  # noqa: E402
from .routes.runs import router as _runs_router  # noqa: E402
from .routes.scenarios import router as _scenarios_router  # noqa: E402
from .routes.agent_types import router as _agent_types_router  # noqa: E402
from .routes.templates import router as _templates_router  # noqa: E402
from .routes.run_control import router as _run_control_router  # noqa: E402
from .routes.personalities import router as _personalities_router  # noqa: E402
from .routes.governance import router as _governance_router  # noqa: E402
from .routes.ai import router as _ai_router  # noqa: E402


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Проверить JWT-секрет при старте приложения."""
    validate_jwt_secret()
    yield


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="SPHERE Graph UI", lifespan=_lifespan)

app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_BODY_BYTES)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

# ---------------------------------------------------------------------------
# Подключение роутеров
# ---------------------------------------------------------------------------

app.include_router(_ws_router)
app.include_router(_auth_router)
app.include_router(_runs_router)
app.include_router(_scenarios_router)
app.include_router(_agent_types_router)
app.include_router(_templates_router)
app.include_router(_run_control_router)
app.include_router(_personalities_router)
app.include_router(_governance_router)
app.include_router(_ai_router)

# ---------------------------------------------------------------------------
# Статические файлы фронтенда
# ---------------------------------------------------------------------------

if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")
