#!/bin/sh
set -eu

cd /app

mkdir -p /app/results /app/data /app/scenarios /data

if [ -n "${MAGISTRY_ADMIN_USERNAME:-}" ] && [ -n "${MAGISTRY_ADMIN_PASSWORD:-}" ]; then
python - <<'PY'
from __future__ import annotations

import os

from web.backend.auth import hash_password
from web.backend.database import create_user, get_user_by_username, init_db

username = os.environ["MAGISTRY_ADMIN_USERNAME"].strip()
password = os.environ["MAGISTRY_ADMIN_PASSWORD"]
role = (os.environ.get("MAGISTRY_ADMIN_ROLE") or "admin").strip() or "admin"
force_update = (os.environ.get("MAGISTRY_ADMIN_FORCE_UPDATE") or "1").strip() == "1"

init_db()
user = get_user_by_username(username)

if user is None:
    create_user(username, hash_password(password), role)
    print(f"[magistry-web] created bootstrap user '{username}' with role '{role}'")
elif force_update:
    from web.backend.database import _connect

    with _connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, role = ? WHERE username = ?",
            (hash_password(password), role, username),
        )
    print(f"[magistry-web] updated bootstrap user '{username}' with role '{role}'")
else:
    print(f"[magistry-web] keeping existing bootstrap user '{username}'")
PY
fi

exec python -m uvicorn web.backend.main:app --host 0.0.0.0 --port "${PORT:-8765}"
