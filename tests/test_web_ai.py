from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from web.backend.routes import ai as ai_routes


@pytest.mark.asyncio
async def test_generate_structured_via_provider_uses_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class _FakeProvider:
        def generate_structured(
            self,
            system: str,
            user: str,
            schema: dict,
            temperature: float = 0.0,
        ) -> SimpleNamespace:
            calls["provider_args"] = (system, user, schema, temperature)
            return SimpleNamespace(data={"ok": True})

    async def _fake_to_thread(func, /, *args, **kwargs):
        calls["to_thread_called"] = True
        return func(*args, **kwargs)

    def _fake_create_provider(**kwargs):
        calls["create_provider_kwargs"] = kwargs
        return _FakeProvider()

    monkeypatch.setattr(ai_routes.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(ai_routes, "create_provider", _fake_create_provider)

    response = await ai_routes._generate_structured_via_provider(
        system_prompt="sys",
        user_prompt="usr",
        schema={"type": "object"},
        temperature=0.25,
    )

    assert calls["to_thread_called"] is True
    assert calls["create_provider_kwargs"] == {
        "mock": False,
        "cache_path": ".llm_cache.db",
        "use_tool_calls": True,
    }
    assert calls["provider_args"] == ("sys", "usr", {"type": "object"}, 0.25)
    assert response.data == {"ok": True}
