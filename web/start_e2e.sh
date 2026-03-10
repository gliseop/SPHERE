#!/bin/bash
# Запуск сервера для Playwright E2E тестов.
#
# Особенности:
# - Использует отдельную SQLite БД пользователей (чтобы не трогать dev users.db)
# - Создаёт admin пользователя с известным паролем
# - Сборит фронтенд и стартует uvicorn на localhost
#
# Использование:
#   ./web/start_e2e.sh [порт]
#
# Переопределения через env:
#   PW_USERS_DB, PW_JWT_SECRET, PW_ADMIN_USER, PW_ADMIN_PASS

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

PORT="${1:-8767}"

JWT_SECRET="${PW_JWT_SECRET:-playwright-secret-0123456789abcdef0123456789abcdef}"
ADMIN_USER="${PW_ADMIN_USER:-pw_admin}"
ADMIN_PASS="${PW_ADMIN_PASS:-pw_password}"
DB_PATH="${PW_USERS_DB:-$ROOT_DIR/.tmp/playwright-users-$PORT.db}"

mkdir -p "$ROOT_DIR/.tmp"
rm -f "$DB_PATH"

echo "=== MAGISTRY E2E server ==="
echo "PORT=$PORT"
echo "DB=$DB_PATH"

echo "Сборка фронтенда..."
cd "$ROOT_DIR/web/frontend"
npm run build

echo "Инициализация users DB и тестового admin..."
cd "$ROOT_DIR"
MAGISTRY_USERS_DB="$DB_PATH" "$ROOT_DIR/.venv/bin/python" - <<PY
from web.backend.auth import hash_password
from web.backend.database import create_user, init_db

init_db()
create_user("$ADMIN_USER", hash_password("$ADMIN_PASS"), "admin")
PY

echo "Запуск сервера: http://127.0.0.1:$PORT"
export MAGISTRY_USERS_DB="$DB_PATH"
export JWT_SECRET="$JWT_SECRET"
export JWT_EXPIRE_HOURS=24
export ALLOWED_ORIGIN="http://127.0.0.1:$PORT"
exec "$ROOT_DIR/.venv/bin/uvicorn" web.backend.main:app --port "$PORT" --host 127.0.0.1

