from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from web.backend.routes import ai as ai_routes
from web.backend.routes import personalities as personalities_routes
from web.backend.database import User
from web.backend import auth as auth_module
from web.backend.models import GenerateInterviewPayload, SecondaryAgentsPayload


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


ADMIN = User(
    id=1,
    username="admin",
    password_hash=auth_module.hash_password("secret"),
    role="admin",
    created_at="2026-01-01T00:00:00+00:00",
)


def _minimal_sim_config() -> dict:
    return {
        "version": 1,
        "title": "Secondary Demo",
        "description": "Проверка генерации вторичных агентов.",
        "ticks": 4,
        "runtime": {"parallel_agents": True},
        "agents": [
            {
                "agent_id": "agent:off_1",
                "name": "Ирина Крылова",
                "internal": True,
                "persona": {
                    "summary": "Руководитель отдела, держит всё под контролем.",
                    "biography": "Много лет работает в муниципалитете, болезненно реагирует на внешнее давление.",
                },
                "capabilities": ["message", "work", "dao"],
                "initial_reputation": 7.0,
                "initial_title": "начальник отдела",
                "wants_promotion": True,
            },
            {
                "agent_id": "agent:off_2",
                "name": "Денис Романов",
                "internal": True,
                "persona": {
                    "summary": "Специалист отдела закупок.",
                    "biography": "Зависим от неформальных отношений и семейных обязательств.",
                },
                "capabilities": ["message", "work"],
                "initial_reputation": 5.0,
                "initial_title": "специалист",
                "wants_promotion": True,
            },
        ],
        "world": {
            "channels": [{"channel_id": "chan:public", "title": "Публичный канал"}],
            "orgs": [],
            "work_items": [],
            "artifacts": [],
            "environment": {},
        },
    }


@pytest.mark.asyncio
async def test_generate_secondary_agents_returns_updated_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def _fake_generate_structured_via_provider(**_: object) -> SimpleNamespace:
        return SimpleNamespace(
            data={
                "agents": [
                    {
                        "kind": "family",
                        "name": "Ольга Крылова",
                        "anchor_agent_id": "agent:off_1",
                        "relation": "жена",
                        "persona_hint": "Супруга руководителя отдела, мягко, но настойчиво продавливает выгодные для семьи решения.",
                        "capabilities": ["message"],
                    },
                    {
                        "kind": "society",
                        "name": "Мария Соболева",
                        "anchor_agent_id": "agent:off_2",
                        "relation": "журналист городского медиа",
                        "persona_hint": "Локальный журналист, ищет истории о сбоях и конфликтах интересов в закупках.",
                        "capabilities": ["message", "work"],
                    },
                ]
            }
        )

    monkeypatch.setattr(ai_routes, "_generate_structured_via_provider", _fake_generate_structured_via_provider)

    payload = SecondaryAgentsPayload(
        scenario="S2",
        governance="G2",
        prompt="Нужны семейные и общественные акторы давления вокруг отдела закупок.",
        family_count=1,
        society_count=1,
        replace_existing=True,
        sim_config=_minimal_sim_config(),
    )

    result = await ai_routes.generate_secondary_agents(payload, _user=ADMIN)

    assert result["secondary_generation"]["added_count"] == 2
    assert result["secondary_generation"]["family_count"] == 1
    assert result["secondary_generation"]["society_count"] == 1
    assert [item["kind"] for item in result["added_agents"]] == ["family", "society"]
    added_ids = [item["agent_id"] for item in result["added_agents"]]
    assert added_ids[0].startswith("agent:fam_")
    assert added_ids[1].startswith("agent:soc_")
    sim_agents = result["sim_config"]["agents"]
    assert any(agent["agent_id"].startswith("agent:fam_") for agent in sim_agents)
    assert any(agent["agent_id"].startswith("agent:soc_") for agent in sim_agents)


@pytest.mark.asyncio
async def test_generate_personality_interview_writes_interview_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    personalities_dir = tmp_path / "personalities"
    interviews_dir = tmp_path / "interviews"
    personalities_dir.mkdir()
    interviews_dir.mkdir()
    personality_path = personalities_dir / "pragmatist.json"
    personality_path.write_text(
        json.dumps(
            {
                "id": "pragmatist",
                "name": "Прагматик",
                "description": "Держит баланс между правилами и выгодой.",
                "biography": "Опытный муниципальный менеджер, который любит порядок и плохо переносит хаос.",
                "hexaco": {"honesty_humility": 60},
                "dark_triad": {"machiavellianism": 30},
                "neutralization_techniques": ["defense_of_necessity"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(personalities_routes, "PERSONALITIES_DIR", personalities_dir)
    monkeypatch.setattr(personalities_routes, "INTERVIEWS_DIR", interviews_dir)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def _fake_generate_structured_via_provider(**_: object) -> SimpleNamespace:
        return SimpleNamespace(
            data={
                "interview": [
                    {"question": question, "answer": f"Ответ на вопрос: {idx + 1}"}
                    for idx, question in enumerate(personalities_routes.INTERVIEW_QUESTIONS_V2)
                ],
                "expert_psychologist": "Психолог видит устойчивый паттерн контроля, осторожности и стремления удерживать порядок в сложной среде.",
                "expert_economist": "Экономист видит рационального актёра, который избегает резкого риска и предпочитает управляемые стимулы.",
            }
        )

    monkeypatch.setattr(
        personalities_routes,
        "_generate_structured_via_provider",
        _fake_generate_structured_via_provider,
    )

    result = await personalities_routes.generate_personality_interview(
        "pragmatist",
        GenerateInterviewPayload(role="чиновник"),
        _user=ADMIN,
    )

    assert result["id"] == "pragmatist"
    assert result["protocol_version"] == "v2"
    assert len(result["interview"]) == len(personalities_routes.INTERVIEW_QUESTIONS_V2)
    saved = json.loads((interviews_dir / "pragmatist.json").read_text(encoding="utf-8"))
    assert saved["expert_psychologist"].startswith("Психолог видит")
    assert saved["interview"][personalities_routes.INTERVIEW_QUESTIONS_V2[0]] == "Ответ на вопрос: 1"
