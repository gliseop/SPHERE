"""Утилиты для LLM-подсистемы: вспомогательные функции и исключения."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def _strip_think_tags(text: str) -> str:
    """Убрать блоки <think>...</think> из ответа модели.

    MiniMax-M2.5 оборачивает внутренние рассуждения в теги <think>.
    Для агентов нужен только чистый ответ.

    Args:
        text: Исходный текст ответа.

    Returns:
        Текст без блоков рассуждений.
    """
    cleaned = re.sub(
        r"<think>.*?</think>", "", text, flags=re.DOTALL
    )
    return cleaned.strip()


class LLMCallError(RuntimeError):
    def __init__(self, *, call_id: str, kind: str, attempt: int, original: Exception) -> None:
        self.call_id = call_id
        self.kind = kind
        self.attempt = attempt
        self.original = original
        super().__init__(f"{kind} failed (call_id={call_id}, attempt={attempt}): {original}")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_llm_log_path() -> Path | None:
    """Вернуть путь для debug-лога LLM, если логирование включено."""
    explicit = (os.getenv("SPHERE_LLM_LOG_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if not (_should_log_success() or _should_log_errors()):
        return None
    return (_project_root() / "results" / "llm_debug.jsonl").resolve()


def _should_log_success() -> bool:
    return (os.getenv("SPHERE_LLM_LOG") or "").strip().lower() in ("1", "true", "yes", "on")


def _should_log_errors() -> bool:
    # По умолчанию пишем ошибки (не мусорит при нормальной работе, но помогает дебажить).
    raw = (os.getenv("SPHERE_LLM_LOG_ERRORS") or "").strip()
    if raw == "":
        return True
    return raw.lower() in ("1", "true", "yes", "on")


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _jsonable(value: Any) -> Any:
    """Преобразовать объект в JSON-совместимый вид (best-effort)."""
    try:
        if hasattr(value, "model_dump"):
            return value.model_dump()  # type: ignore[attr-defined]
        if hasattr(value, "to_dict"):
            return value.to_dict()  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _sanitize_create_kwargs(create_kwargs: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    """Обрезать потенциально большие строки в запросе (messages/response_format)."""
    result: dict[str, Any] = {}
    for k, v in create_kwargs.items():
        if k == "messages" and isinstance(v, list):
            sanitized_msgs = []
            for msg in v:
                if not isinstance(msg, dict):
                    sanitized_msgs.append(_jsonable(msg))
                    continue
                content = msg.get("content")
                if isinstance(content, str):
                    msg = dict(msg)
                    msg["content"] = _truncate_text(content, max_chars)
                sanitized_msgs.append(msg)
            result[k] = sanitized_msgs
            continue
        if k in ("response_format", "tools", "tool_choice", "extra_body"):
            result[k] = _jsonable(v)
            continue
        result[k] = _jsonable(v)
    return result


def _extract_json(text: str) -> str:
    """Извлечь JSON из текста, который может содержать markdown-обёртку.

    Модели иногда оборачивают JSON в блоки ```json ... ``` или
    добавляют текст до/после. Функция пытается найти и извлечь
    первый валидный JSON-объект из текста.

    Args:
        text: Текст, потенциально содержащий JSON.

    Returns:
        Извлечённый JSON-текст.
    """
    # Убрать markdown-блоки
    md_match = re.search(
        r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL
    )
    if md_match:
        return md_match.group(1).strip()

    # Найти первый { ... } блок
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return text[start:end]
