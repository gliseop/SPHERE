"""Централизованный реестр LLM-промптов SPHERE."""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


class PromptTemplateError(RuntimeError):
    """Ошибка доступа к YAML-реестру промптов."""


def _resource_text() -> str:
    try:
        return resources.files("sphere_lc").joinpath("prompts.yaml").read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover - инфраструктурный guardrail
        raise PromptTemplateError(f"Failed to read prompts.yaml: {exc}") from exc


@lru_cache(maxsize=1)
def _prompt_tree() -> dict[str, Any]:
    try:
        data = yaml.safe_load(_resource_text())
    except Exception as exc:  # pragma: no cover - инфраструктурный guardrail
        raise PromptTemplateError(f"Failed to parse prompts.yaml: {exc}") from exc
    if not isinstance(data, dict):
        raise PromptTemplateError("prompts.yaml must contain a top-level mapping")
    return data


def _lookup(path: str) -> Any:
    node: Any = _prompt_tree()
    if not path:
        return node
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise PromptTemplateError(f"Unknown prompt path: {path}")
        node = node[part]
    return node


def get_prompt_template(path: str) -> str:
    """Вернуть сырой шаблон промпта по dot-path."""

    value = _lookup(path)
    if not isinstance(value, str):
        raise PromptTemplateError(f"Prompt path is not a string template: {path}")
    return value


def get_prompt_subtree(path: str = "") -> Any:
    """Вернуть копию поддерева prompt-реестра."""

    return copy.deepcopy(_lookup(path))


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_prompt(path: str, /, **values: Any) -> str:
    """Отрендерить шаблон промпта из YAML-реестра."""

    template = get_prompt_template(path)

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise PromptTemplateError(f"Missing prompt variable {key!r} for template {path}")
        return _stringify(values[key])

    return _PLACEHOLDER_RE.sub(_replace, template)
