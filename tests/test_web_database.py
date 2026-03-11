"""Тесты SQLite-хранилища учётных записей."""
import pytest
import sqlite3
import web.backend.database as db_module


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_users.db")
    db_module.init_db()


def test_create_and_get_user():
    user = db_module.create_user("alice", "hash123", "admin")
    assert user.username == "alice"
    assert user.role == "admin"
    got = db_module.get_user_by_username("alice")
    assert got is not None
    assert got.id == user.id


def test_get_nonexistent_user():
    assert db_module.get_user_by_username("nobody") is None


def test_duplicate_username_raises():
    db_module.create_user("alice", "hash1", "admin")
    with pytest.raises(sqlite3.IntegrityError):
        db_module.create_user("alice", "hash2", "viewer")


def test_list_users():
    db_module.create_user("alice", "h1", "admin")
    db_module.create_user("bob", "h2", "viewer")
    users = db_module.list_users()
    assert len(users) == 2
    assert {u.username for u in users} == {"alice", "bob"}


def test_delete_user():
    db_module.create_user("alice", "h1", "admin")
    assert db_module.delete_user("alice") is True
    assert db_module.get_user_by_username("alice") is None


def test_update_role():
    db_module.create_user("alice", "h1", "viewer")
    assert db_module.update_role("alice", "admin") is True
    assert db_module.get_user_by_username("alice").role == "admin"


def test_init_db_reads_magistry_users_db_from_dotenv(tmp_path, monkeypatch):
    env_root = tmp_path / "env_root"
    env_root.mkdir()
    db_path = env_root / "custom" / "users.db"
    (env_root / ".env").write_text(
        f"MAGISTRY_USERS_DB={db_path.as_posix()}\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(env_root)
    monkeypatch.setattr(db_module, "DB_PATH", None)
    monkeypatch.setattr(db_module, "_DOTENV_LOADED", False)
    monkeypatch.delenv("MAGISTRY_USERS_DB", raising=False)

    db_module.init_db()

    assert db_path.exists()
