"""CLI управления учётными записями пользователей SPHERE.

Использование:
    python -m web.backend.manage_users create --username admin --role admin
    python -m web.backend.manage_users list
    python -m web.backend.manage_users delete --username alice
    python -m web.backend.manage_users change-role --username alice --role admin
"""
from __future__ import annotations

import argparse
import getpass
import sys

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:  # pragma: no cover - dotenv является optional dep
    find_dotenv = None
    load_dotenv = None

if load_dotenv is not None:
    env_path = find_dotenv(usecwd=True) if find_dotenv is not None else ""
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()

from .auth import hash_password
from .database import (
    create_user,
    delete_user,
    get_user_by_username,
    init_db,
    list_users,
    update_role,
)


def cmd_create(args: argparse.Namespace) -> None:
    """Создать нового пользователя с паролем, введённым интерактивно.

    Args:
        args: Аргументы командной строки (username, role).
    """
    init_db()
    if get_user_by_username(args.username) is not None:
        print(f"Ошибка: пользователь '{args.username}' уже существует", file=sys.stderr)
        sys.exit(1)
    password = getpass.getpass(f"Пароль для '{args.username}': ")
    confirm = getpass.getpass("Повторите пароль: ")
    if password != confirm:
        print("Ошибка: пароли не совпадают", file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print("Ошибка: пароль должен быть не короче 8 символов", file=sys.stderr)
        sys.exit(1)
    user = create_user(args.username, hash_password(password), args.role)
    print(f"Создан пользователь '{user.username}' с ролью '{user.role}' (id={user.id})")


def cmd_list(_args: argparse.Namespace) -> None:
    """Вывести всех пользователей.

    Args:
        _args: Аргументы командной строки (не используются).
    """
    init_db()
    users = list_users()
    if not users:
        print("Нет пользователей. Создайте первого:")
        print("  python -m web.backend.manage_users create --username admin --role admin")
        return
    print(f"{'ID':>4}  {'Username':<20}  {'Role':<10}  Created")
    print("-" * 60)
    for u in users:
        print(f"{u.id:>4}  {u.username:<20}  {u.role:<10}  {u.created_at}")


def cmd_delete(args: argparse.Namespace) -> None:
    """Удалить пользователя.

    Args:
        args: Аргументы командной строки (username).
    """
    init_db()
    if not delete_user(args.username):
        print(f"Ошибка: пользователь '{args.username}' не найден", file=sys.stderr)
        sys.exit(1)
    print(f"Пользователь '{args.username}' удалён")


def cmd_change_role(args: argparse.Namespace) -> None:
    """Изменить роль пользователя.

    Args:
        args: Аргументы командной строки (username, role).
    """
    init_db()
    if not update_role(args.username, args.role):
        print(f"Ошибка: пользователь '{args.username}' не найден", file=sys.stderr)
        sys.exit(1)
    print(f"Роль пользователя '{args.username}' изменена на '{args.role}'")


def main() -> None:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description="Управление пользователями SPHERE",
        prog="python -m web.backend.manage_users",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_create = subparsers.add_parser("create", help="Создать пользователя")
    p_create.add_argument("--username", required=True, help="Имя пользователя")
    p_create.add_argument("--role", required=True, choices=["admin", "viewer"], help="Роль")
    p_create.set_defaults(func=cmd_create)

    p_list = subparsers.add_parser("list", help="Список всех пользователей")
    p_list.set_defaults(func=cmd_list)

    p_delete = subparsers.add_parser("delete", help="Удалить пользователя")
    p_delete.add_argument("--username", required=True, help="Имя пользователя")
    p_delete.set_defaults(func=cmd_delete)

    p_change = subparsers.add_parser("change-role", help="Изменить роль")
    p_change.add_argument("--username", required=True, help="Имя пользователя")
    p_change.add_argument("--role", required=True, choices=["admin", "viewer"], help="Новая роль")
    p_change.set_defaults(func=cmd_change_role)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
