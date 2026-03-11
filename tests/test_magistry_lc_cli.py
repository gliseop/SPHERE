from __future__ import annotations

import builtins
from io import BytesIO
from pathlib import Path

import pytest

from magistry_lc.cli import _SafeArgumentParser
from magistry_lc.llm.providers import OpenAICompatibleProvider
from magistry_lc.persona import PersonaLibrary
from magistry_lc.scenario import _load_yaml


class _EncodingFailingStream:
    encoding = "cp1252"

    def __init__(self) -> None:
        self.buffer = BytesIO()

    def write(self, text: str) -> int:
        raise UnicodeEncodeError("charmap", text, 0, len(text), "cannot encode")


def _patch_import_error(monkeypatch: pytest.MonkeyPatch, module_name: str) -> None:
    original_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == module_name:
            raise ImportError(f"missing {module_name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)


def test_safe_argument_parser_replaces_unencodable_help_text() -> None:
    parser = _SafeArgumentParser(description="Описание с кириллицей")
    stream = _EncodingFailingStream()

    parser.print_help(file=stream)

    rendered = stream.buffer.getvalue().decode("cp1252")
    assert "usage:" in rendered


def test_yaml_loader_error_points_to_lc_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_import_error(monkeypatch, "yaml")

    with pytest.raises(ImportError) as exc:
        _load_yaml(Path("dummy.yaml"))

    assert 'pip install -e ".[lc]"' in str(exc.value)
    assert "magistry-sim[lc]" not in str(exc.value)


def test_persona_library_yaml_error_points_to_lc_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_import_error(monkeypatch, "yaml")

    with pytest.raises(ImportError) as exc:
        PersonaLibrary._load_file(Path("dummy.yaml"))

    assert 'pip install -e ".[lc]"' in str(exc.value)
    assert "magistry-sim[lc]" not in str(exc.value)


def test_openai_provider_error_points_to_project_install(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_import_error(monkeypatch, "openai")

    with pytest.raises(ImportError) as exc:
        OpenAICompatibleProvider()

    assert "pip install -e ." in str(exc.value)
    assert "magistry-sim[llm]" not in str(exc.value)
