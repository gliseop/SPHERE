#!/usr/bin/env python3
"""
Скрипт собирает весь код проекта, первую главу ВКР и исследование в один файл
для загрузки в веб-версию ChatGPT.

Исключает: .gitignore, скрытые папки, бинарные файлы, node_modules, __pycache__ и т.п.

Использование:
  python scripts/collect_for_chatgpt.py           # полный сбор
  python scripts/collect_for_chatgpt.py --compact # без data/scenarios (меньше размер)
"""

from pathlib import Path
import fnmatch
import argparse

# Корень проекта
ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "collected_for_chatgpt.txt"

# Расширения для включения
CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".html", ".css"}
DOC_EXT = {".md"}
ALL_EXT = CODE_EXT | CONFIG_EXT | DOC_EXT

# Файлы/папки, которые всегда исключаем
ALWAYS_SKIP = {
    ".git",
    ".gitignore",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    "dist",
    "dist-ssr",
    "test-results",
    "playwright-report",
    ".playwright-mcp",
    ".idea",
    ".vscode",
    "*.db",
    "*.log",
    "collected_for_chatgpt.txt",  # не включать сам результат
}

# Паттерны из .gitignore (упрощённо)
GITIGNORE_PATTERNS = [
    ".env",
    "*.db",
    ".venv*",
    ".tmp",
    "__pycache__",
    "*egg-info",
    ".playwright-mcp",
    "results",
    "screenshots",
    "test-screenshots",
    "lc_results",
    "node_modules",
    "dist",
    "dist-ssr",
    "*.local",
    "test-results",
    "playwright-report",
    ".idea",
    ".DS_Store",
    "*.suo",
    "*.ntvs*",
    "*.njsproj",
    "*.sln",
    "*.sw?",
    "*.log",
    "logs",
]


def should_skip(path: Path, is_dir: bool) -> bool:
    """Проверяет, нужно ли пропустить путь."""
    name = path.name

    # Скрытые папки/файлы (кроме .env.example)
    if name.startswith(".") and name != ".env.example":
        return True

    # Всегда исключаемые
    if name in ALWAYS_SKIP:
        return True
    for pattern in ALWAYS_SKIP:
        if fnmatch.fnmatch(name, pattern):
            return True

    # Паттерны .gitignore
    rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    rel_str = str(rel).replace("\\", "/")
    for pattern in GITIGNORE_PATTERNS:
        if "/" in pattern:
            if fnmatch.fnmatch(rel_str, pattern) or fnmatch.fnmatch(rel_str, f"**/{pattern}"):
                return True
        else:
            if fnmatch.fnmatch(name, pattern):
                return True

    # Любой путь, содержащий исключённую папку
    for part in path.parts:
        if part.startswith(".") and part != ".env.example":
            return True
        if part in {"node_modules", "__pycache__", ".venv", "venv", "dist", ".git"}:
            return True

    return False


def collect_files(compact: bool = False) -> list[tuple[Path, str]]:
    """Собирает список файлов с категорией для сортировки."""
    result: list[tuple[Path, str]] = []

    for path in ROOT.rglob("*"):
        if path.is_file():
            # Пропускаем пути с исключёнными сегментами
            skip = False
            for part in path.relative_to(ROOT).parts:
                if should_skip(ROOT / part, True):
                    skip = True
                    break
            if skip:
                continue
            if should_skip(path, False):
                continue

            ext = path.suffix.lower()
            if ext not in ALL_EXT:
                continue
            if ext == ".json" and "node_modules" in str(path):
                continue
            # Пропускаем package-lock (слишком большой)
            if path.name == "package-lock.json":
                continue

            # В compact-режиме исключаем data/ и scenarios/
            if compact:
                rel_str_check = str(path.relative_to(ROOT)).replace("\\", "/")
                if rel_str_check.startswith("data/") or rel_str_check.startswith("scenarios/"):
                    continue

            # Категория для порядка вывода
            rel = path.relative_to(ROOT)
            rel_str = str(rel).replace("\\", "/")
            if rel_str == "chapter_1.md":
                cat = "0_thesis"
            elif rel_str.startswith("docs/"):
                cat = "1_research"
            elif rel_str.startswith("src/"):
                cat = "2_src"
            elif rel_str.startswith("web/backend/"):
                cat = "3_backend"
            elif rel_str.startswith("web/frontend/"):
                cat = "4_frontend"
            elif rel_str.startswith("tests/"):
                cat = "5_tests"
            elif rel_str.startswith("data/") or rel_str.startswith("scenarios/"):
                cat = "6_data"
            elif rel_str in ("README.md", "AGENTS.md", ".env.example", "pyproject.toml"):
                cat = "7_root"
            else:
                cat = "8_other"

            result.append((path, cat))

    return sorted(result, key=lambda x: (x[1], str(x[0])))


def read_file(path: Path) -> str:
    """Читает файл с обработкой кодировки."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"# [Ошибка чтения: {e}]\n"


def main():
    parser = argparse.ArgumentParser(description="Collect project code for ChatGPT")
    parser.add_argument("--compact", action="store_true", help="Exclude data/ and scenarios/ (smaller output)")
    args = parser.parse_args()
    files = collect_files(compact=args.compact)
    lines: list[str] = []

    lines.append("=" * 80)
    lines.append("MAGISTRY — Собранный код и документация для ChatGPT")
    lines.append("=" * 80)
    lines.append("")

    current_cat = ""
    for path, cat in files:
        if cat != current_cat:
            current_cat = cat
            label = {
                "0_thesis": "ГЛАВА 1 ВКР",
                "1_research": "ИССЛЕДОВАНИЕ И ДОКУМЕНТАЦИЯ",
                "2_src": "ИСХОДНЫЙ КОД (src)",
                "3_backend": "BACKEND (web)",
                "4_frontend": "FRONTEND (web)",
                "5_tests": "ТЕСТЫ",
                "6_data": "ДАННЫЕ И СЦЕНАРИИ",
                "7_root": "КОРНЕВЫЕ ФАЙЛЫ",
                "8_other": "ПРОЧЕЕ",
            }.get(cat, cat)
            lines.append("")
            lines.append("#" * 80)
            lines.append(f"# {label}")
            lines.append("#" * 80)
            lines.append("")

        rel = path.relative_to(ROOT)
        lines.append("")
        lines.append("-" * 60)
        lines.append(f"FILE: {rel}")
        lines.append("-" * 60)
        lines.append("")
        content = read_file(path)
        # Обрезаем слишком длинные файлы (опционально — можно убрать)
        if len(content) > 50_000:
            content = content[:50_000] + "\n\n... [обрезано, файл слишком большой] ..."
        lines.append(content)
        lines.append("")

    out = "\n".join(lines)
    OUTPUT.write_text(out, encoding="utf-8")
    print(f"Collected {len(files)} files")
    print(f"Output: {OUTPUT}")
    print(f"Size: {len(out) / 1024:.1f} KB")


if __name__ == "__main__":
    main()
