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

resolve_python_bin() {
    local candidates=(
        "$ROOT_DIR/.venv/bin/python"
        "$ROOT_DIR/.venv/Scripts/python.exe"
        "$ROOT_DIR/.venv/Scripts/python"
    )
    local candidate
    for candidate in "${candidates[@]}"; do
        if [[ -f "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    if command -v python >/dev/null 2>&1; then
        command -v python
        return 0
    fi
    echo "Не найден Python-интерпретатор (.venv/bin, .venv/Scripts или python из PATH)." >&2
    return 1
}

# Загрузить переменные окружения из .env (если файл существует)
if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$ROOT_DIR/.env"
    set +a
    echo "Загружены переменные из .env"
fi

PORT=8765
if [[ "${1:-}" == "--port" || "${1:-}" == "-p" ]]; then
    PORT="${2:-8765}"
elif [[ "${1:-}" =~ ^--port=([0-9]+)$ ]]; then
    PORT="${BASH_REMATCH[1]}"
elif [[ "${1:-}" =~ ^[0-9]+$ ]]; then
    PORT="$1"
fi

echo "=== MAGISTRY Graph UI ==="
echo "Сборка фронтенда..."
cd "$SCRIPT_DIR/frontend"
npm run build

echo "Запуск сервера на http://localhost:$PORT"
cd "$ROOT_DIR"
PYTHON_BIN="$(resolve_python_bin)"
exec "$PYTHON_BIN" -m uvicorn web.backend.main:app --port "$PORT" --host 0.0.0.0
