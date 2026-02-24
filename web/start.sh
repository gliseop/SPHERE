#!/bin/bash
# Запуск MAGISTRY Graph UI.
# Собирает фронтенд и запускает FastAPI-сервер, который раздаёт как API,
# так и статику фронтенда по единому адресу.
#
# Использование:
#   ./web/start.sh [--port 8765]
#
# По умолчанию порт 8765.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Загрузить переменные окружения из .env (если файл существует)
if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$ROOT_DIR/.env"
    set +a
    echo "Загружены переменные из .env"
fi

PORT="${2:-8765}"

echo "=== MAGISTRY Graph UI ==="
echo "Сборка фронтенда..."
cd "$SCRIPT_DIR/frontend"
npm run build

echo "Запуск сервера на http://localhost:$PORT"
cd "$ROOT_DIR"
exec .venv/bin/uvicorn web.backend.main:app --port "$PORT" --host 0.0.0.0
