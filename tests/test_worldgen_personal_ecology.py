from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sphere_lc.agent import AgentRunner
from sphere_lc.arbiter import Arbiter
from sphere_lc.config import MemoryConfig, RuntimeConfig, ScenarioConfig
from sphere_lc.dao import DaoEngine
from sphere_lc.engine import RunArtifacts, WorldEngine
from sphere_lc.entities import EntityRecord, EntityRegistry
from sphere_lc.events import Event, EventLog
from sphere_lc.fidelity import evaluate_fidelity
from sphere_lc.id_alloc import IdAllocator
from sphere_lc.ids import EntityKind
from sphere_lc.ids import INTERNAL_AUDIENCE
from sphere_lc.journal import WorldJournal
from sphere_lc.llm import LLMCaller, MockLLMProvider, StructuredLLMResponse
from sphere_lc.persona import MotivationDigest, PersonaArtifact
from sphere_lc.state import (
    AgentState,
    ArtifactState,
    InstitutionRegimeState,
    PendingInteractionState,
    ResourcePoolState,
    Vote,
    WorkItem,
    WorldState,
    ZoneState,
)
from sphere_lc.tracing import TraceLog
from sphere_lc.worldgen import AgentDailyContext, SceneHook


class _CaptureAgentPromptProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.agent_prompts: list[str] = []

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сделай следующий ход в этой ситуации." in user:
            self.agent_prompts.append(user)
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        if '"phase": "pre"' in user:
            return StructuredLLMResponse(
                data={"events": [], "agent_contexts": [], "scene_hooks": []},
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _PreTickWorldgenProvider(_CaptureAgentPromptProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if '"phase": "pre"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [
                        {
                            "audience": "internal",
                            "description": "С утра пришёл внешний сигнал о внеплановой проверке.",
                        }
                    ],
                    "agent_contexts": [
                        {
                            "agent_id": "agent:off_1",
                            "where_day_starts": "У входа в администрацию перед первым совещанием.",
                            "personal_pressure": "Боится, что новый шум ударит по его позиции.",
                            "social_encounter": "Пересёкся с подрядчиком в коридоре.",
                            "ambient_signal": "В новостной ленте обсуждают прозрачность закупок.",
                            "today_hook": "Можно жёстко формализовать процесс или решить вопрос тихо.",
                            "lightweight_contacts": ["старый знакомый", "журналист районной газеты"],
                        }
                    ],
                    "scene_hooks": [
                        {
                            "kind": "corridor_encounter",
                            "agents": ["agent:off_1"],
                            "description": "В коридоре его останавливает подрядчик с короткой просьбой.",
                            "mandatory": False,
                        }
                    ],
                },
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


class _PreTickRiskEventProvider(_CaptureAgentPromptProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if '"phase": "pre"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [
                        {
                            "audience": "internal",
                            "description": "Обнаружено подозрительное совпадение формулировок и риск санкций за конфликт интересов.",
                        }
                    ],
                    "agent_contexts": [],
                    "scene_hooks": [],
                },
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


class _FreeformTruthProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.agent_prompts: list[tuple[int, str]] = []

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "post-hoc recorder нарушений" in system:
            return StructuredLLMResponse(
                data=[
                    {
                        "tick": 0,
                        "subject_agent_id": "agent:off_1",
                        "target_agent_id": "agent:off_2",
                        "violation_type_freeform": "pressure_not_to_escalate",
                        "summary": "Агент давит на коллегу, чтобы та не поднимала вопрос официально.",
                        "mechanism": "private_pressure",
                        "beneficiary": "agent:off_1",
                        "confidence": 0.72,
                        "evidence_refs": [{"tick": 0, "event_type": "message_sent"}],
                        "notes": "freeform",
                    }
                ],
                model="mock",
            )
        if "Сделай следующий ход в этой ситуации." in user:
            self.agent_prompts.append((0, user))
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _DormantEcologyProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[tuple[int, str]] = []

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сделай следующий ход в этой ситуации." in user:
            tick = 0
            marker = "Сегодняшний рабочий день: "
            if marker in user:
                tick = int(user.split(marker, 1)[1].split(".", 1)[0].strip())
            if "Твоё служебное обозначение в документах: agent:spawner." in user and tick == 0:
                self.prompts.append((tick, "agent:spawner"))
                return StructuredLLMResponse(
                    data={
                        "actions": [
                            {
                                "type": "spawn_agent",
                                "slug": "witness",
                                "name": "Анна Свиридова",
                                "internal": False,
                                "persona_hint": "Внешний наблюдатель.",
                                "capabilities": ["message"],
                            }
                        ]
                    },
                    model="mock",
                )
            if "Твоё служебное обозначение в документах: agent:witness." in user:
                self.prompts.append((tick, "agent:witness"))
                return StructuredLLMResponse(
                    data={"actions": [{"type": "noop", "justification": "idle"}]},
                    model="mock",
                )
            if "Твоё служебное обозначение в документах: agent:observer." in user:
                self.prompts.append((tick, "agent:observer"))
                return StructuredLLMResponse(
                    data={"actions": [{"type": "noop", "justification": "idle"}]},
                    model="mock",
                )
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _EnvironmentUpdateProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сделай следующий ход в этой ситуации." in user:
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        if '"phase": "post"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [],
                    "spawns": [],
                    "environment_updates": {
                        "institutions": [
                            {
                                "org_id": "org:city_hall",
                                "operating_mode": "crisis",
                                "security_mode": "heightened",
                            }
                        ],
                        "zones": [
                            {
                                "zone_id": "zone:city_hall",
                                "access_mode": "restricted",
                            }
                        ],
                        "resource_pools": [
                            {
                                "resource_id": "res:roads_budget",
                                "quantity": 900,
                                "status": "depleted",
                                "pressure": "Подрядчики требуют срочного решения.",
                            }
                        ],
                        "information_climate": {
                            "public_mood": "Раздражение усиливается.",
                            "media_pressure": "Журналисты готовят материал.",
                            "active_signals": ["новая волна жалоб", "утечка сметы"],
                        },
                    },
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _ArtifactWorldgenProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сделай следующий ход в этой ситуации." in user:
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        if '"phase": "post"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [],
                    "spawns": [],
                    "artifact_creations": [
                        {
                            "artifact_id": "art:oversight_memo",
                            "artifact_type": "memo",
                            "title": "Служебная записка контрольного отдела",
                            "summary": "Контрольный отдел просит срочно пояснить расходование средств.",
                            "owner_org_id": "org:city_hall",
                            "visibility": "internal",
                            "status": "new",
                            "tags": ["oversight", "budget"],
                        }
                    ],
                    "artifact_updates": [
                        {
                            "artifact_id": "art:repair_report",
                            "summary": "В отчёте появился новый спорный абзац о перерасходе.",
                            "status": "revised",
                            "tags": ["repair", "risk"],
                        }
                    ],
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _ExternalEntityWorldgenProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сделай следующий ход в этой ситуации." in user:
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        if '"phase": "post"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [],
                    "spawns": [],
                    "entity_creations": [
                        {
                            "entity_id": "org:media",
                            "kind": "org",
                            "title": "Редакция районной газеты",
                            "description": "Внешний наблюдатель за тендером.",
                        },
                        {
                            "entity_id": "zone:public_sphere",
                            "kind": "zone",
                            "title": "Публичная медиасфера",
                            "description": "Площадка внешнего наблюдения и публикаций.",
                            "zone_type": "public_square",
                            "primary_org_id": "org:media",
                        },
                    ],
                    "artifact_creations": [
                        {
                            "artifact_id": "art:news_article_001",
                            "artifact_type": "news_article",
                            "title": "Статья о подозрениях в конфликте интересов",
                            "summary": "Внешняя публикация о связи начальника закупок и подрядчика.",
                            "owner_org_id": "org:media",
                            "zone_id": "zone:public_sphere",
                            "visibility": "public",
                            "status": "published",
                            "tags": ["media", "conflict"],
                        }
                    ],
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _MissingExternalDependencyWorldgenProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сделай следующий ход в этой ситуации." in user:
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        if '"phase": "post"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [],
                    "spawns": [],
                    "artifact_creations": [
                        {
                            "artifact_id": "art:oversight_note",
                            "artifact_type": "memo",
                            "title": "Записка о внешнем наблюдении",
                            "summary": "Артефакт ссылается на ещё не материализованный owner_org_id.",
                            "owner_org_id": "org:oversight",
                            "zone_id": "zone:oversight",
                            "visibility": "internal",
                            "status": "draft",
                        }
                    ],
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _RepairingExternalDependencyWorldgenProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сделай следующий ход в этой ситуации." in user:
            return StructuredLLMResponse(data={"actions": [{"type": "noop", "justification": "idle"}]}, model="mock")
        if "repair-pass для worldgen artifact dependencies" in system:
            return StructuredLLMResponse(
                data={
                    "entity_creations": [
                        {
                            "entity_id": "org:gazette",
                            "kind": "org",
                            "title": "Редакция муниципальной газеты",
                            "description": "Внешняя редакция, публикующая материалы о тендере.",
                        }
                    ],
                    "drop_artifact_ids": [],
                },
                model="mock",
            )
        if '"phase": "post"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [],
                    "spawns": [],
                    "artifact_creations": [
                        {
                            "artifact_id": "art:gazette_article_conflict",
                            "artifact_type": "news_article",
                            "title": "Заметка о конфликте интересов",
                            "summary": "Внешняя публикация о подозрениях в конфликте интересов.",
                            "owner_org_id": "org:gazette",
                            "visibility": "public",
                            "status": "published",
                        }
                    ],
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _ArtifactPrefixWorldgenProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сделай следующий ход в этой ситуации." in user:
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        if '"phase": "post"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [],
                    "spawns": [],
                    "artifact_creations": [
                        {
                            "artifact_id": "artifact:oversight_memo_prefixed",
                            "artifact_type": "memo",
                            "title": "Служебная записка с legacy-prefix",
                            "summary": "Worldgen вернул legacy artifact prefix.",
                            "owner_org_id": "org:city_hall",
                            "visibility": "internal",
                            "status": "new",
                        }
                    ],
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _ArtifactTypedPrefixWorldgenProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сделай следующий ход в этой ситуации." in user:
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        if '"phase": "post"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [],
                    "spawns": [],
                    "artifact_creations": [
                        {
                            "artifact_id": "doc:tender_announcement",
                            "artifact_type": "document",
                            "title": "Объявление о тендере",
                            "summary": "Worldgen вернул doc:* prefix.",
                            "owner_org_id": "org:city_hall",
                            "visibility": "public",
                            "status": "published",
                        },
                        {
                            "artifact_id": "report:conflict_assessment",
                            "artifact_type": "report",
                            "title": "Оценка конфликта интересов",
                            "summary": "Worldgen вернул report:* prefix.",
                            "owner_org_id": "org:city_hall",
                            "visibility": "internal",
                            "status": "draft",
                        },
                    ],
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _BoundSpawnProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сделай следующий ход в этой ситуации." in user:
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        if '"phase": "post"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [],
                    "spawns": [
                        {
                            "slug": "district_reporter",
                            "name": "Ирина Савельева",
                            "internal": False,
                            "persona_hint": "Местная журналистка, следит за работой мэрии.",
                            "org_id": "org:city_hall",
                            "zone_id": "zone:city_hall",
                        }
                    ],
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _PrivateContactProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "agent:off_1" in user:
            return StructuredLLMResponse(
                data={
                    "actions": [
                        {
                            "type": "send_message",
                            "to_id": "agent:off_2",
                            "text": "Нужно обсудить это неформально.",
                            "private": True,
                            "justification": "Частный канал быстрее.",
                        }
                    ]
                },
                model="mock",
            )
        return StructuredLLMResponse(
            data={"actions": [{"type": "noop", "justification": "idle"}]},
            model="mock",
        )


class _MicroReactionProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "agent:core_1" in user and "локальное окно реакции" not in user:
            return StructuredLLMResponse(
                data={
                    "actions": [
                        {
                            "type": "send_message",
                            "to_id": "agent:peripheral",
                            "text": "Срочно посмотри на это.",
                            "private": True,
                            "justification": "Нужно быстpoе подтверждение.",
                        }
                    ]
                },
                model="mock",
            )
        if "agent:peripheral" in user and "локальное окно реакции" in user:
            return StructuredLLMResponse(
                data={
                    "actions": [
                        {
                            "type": "send_message",
                            "to_id": "agent:core_1",
                            "text": "Принял, уже смотрю.",
                            "private": True,
                            "justification": "Реакция на сообщение того же тика.",
                        }
                    ]
                },
                model="mock",
            )
        return StructuredLLMResponse(
            data={"actions": [{"type": "noop", "justification": "idle"}]},
            model="mock",
        )


class _DeferredPendingReplyProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[tuple[int, str]] = []

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сделай следующий ход в этой ситуации." not in user:
            return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")

        tick = 0
        marker = "Сегодняшний рабочий день: "
        if marker in user:
            tick = int(user.split(marker, 1)[1].split(".", 1)[0].strip())
        if "Твоё служебное обозначение в документах: agent:core_1." in user and tick == 0:
            self.prompts.append((tick, "agent:core_1"))
            return StructuredLLMResponse(
                data={
                    "actions": [
                        {
                            "type": "send_message",
                            "to_id": "agent:peripheral",
                            "text": "Нужно вернуться с ответом позже.",
                            "private": True,
                            "justification": "Оставляет локальное обязательство для follow-up.",
                        }
                    ]
                },
                model="mock",
            )
        if "Твоё служебное обозначение в документах: agent:peripheral." in user:
            self.prompts.append((tick, "agent:peripheral"))
            if tick == 2 and "Незакрытые хвосты и ожидания:" in user:
                return StructuredLLMResponse(
                    data={
                        "actions": [
                            {
                                "type": "send_message",
                                "to_id": "agent:core_1",
                                "text": "Возвращаюсь с ответом по твоему запросу.",
                                "private": True,
                                "justification": "Закрывает отложенный follow-up.",
                            }
                        ]
                    },
                    model="mock",
                )
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "wait"}]},
                model="mock",
            )
        return StructuredLLMResponse(
            data={"actions": [{"type": "noop", "justification": "idle"}]},
            model="mock",
        )


class _SameTickPendingFollowupProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сделай следующий ход в этой ситуации." not in user:
            return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")

        tick = 0
        marker = "Сегодняшний рабочий день: "
        if marker in user:
            tick = int(user.split(marker, 1)[1].split(".", 1)[0].strip())
        if "Твоё служебное обозначение в документах: agent:core_1." in user and tick == 0:
            return StructuredLLMResponse(
                data={
                    "actions": [
                        {
                            "type": "send_message",
                            "to_id": "agent:peripheral",
                            "text": "Нужно быстро ответить в этом же тике.",
                            "private": True,
                            "justification": "Создаёт immediate follow-up obligation.",
                        }
                    ]
                },
                model="mock",
            )
        if "Твоё служебное обозначение в документах: agent:peripheral." in user and tick == 0:
            if "Незакрытые хвосты и ожидания:" in user:
                return StructuredLLMResponse(
                    data={
                        "actions": [
                            {
                                "type": "send_message",
                                "to_id": "agent:core_1",
                                "text": "Отвечаю сразу, не дожидаясь следующего тика.",
                                "private": True,
                                "justification": "Закрывает same-tick pending follow-up.",
                            }
                        ]
                    },
                    model="mock",
                )
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "ждёт follow-up"}]},
                model="mock",
            )
        return StructuredLLMResponse(
            data={"actions": [{"type": "noop", "justification": "idle"}]},
            model="mock",
        )


def test_runtime_config_simulated_datetime_respects_granularity() -> None:
    hourly = RuntimeConfig(start_date="2026-03-09", tick_granularity="hour", tick_duration_days=2)
    assert hourly.simulated_datetime(3).isoformat() == "2026-03-09T15:00:00"

    half_day = RuntimeConfig(start_date="2026-03-09", tick_granularity="half_day", tick_duration_days=1)
    assert half_day.simulated_datetime(1).isoformat() == "2026-03-09T21:00:00"

    weekly = RuntimeConfig(start_date="2026-03-09", tick_granularity="week", tick_duration_days=1)
    assert weekly.simulated_datetime(1).date().isoformat() == "2026-03-16"


def test_agent_prompt_includes_story_state_daily_context_and_soft_perform(tmp_path: Path) -> None:
    agent = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(
            summary="Хочет удержать процесс под контролем.",
            motivation=MotivationDigest(
                goal="Сохранить управляемость тендера и не допустить распада процесса.",
                fear="Боится, что внешний шум сорвёт привычный контроль над ситуацией.",
                obligation="Связан обязательствами перед отделом и людьми, от которых зависит его положение.",
                gain="Выгодно удержать репутацию человека, который доводит процессы до конца.",
                pressure="Не отпускает ощущение, что любое колебание быстро превратится в скандал.",
                threat="Удар может прийти из внешней проверки, утечки или чужой инициативы.",
            ),
        ),
        capabilities=["message", "work"],
        story_state="Последние сдвиги: боится внешнего шума и давления со стороны знакомых.",
        title="начальник отдела",
    )
    state = WorldState(tick=0, registry=EntityRegistry(), agents={agent.agent_id: agent})
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=agent,
        state=state,
        visible_events=[Event(tick=0, event_type="world_event", payload={"description": "Появился риск внешней проверки."})],
        mem_text="(пусто)",
        daily_context=AgentDailyContext(
            where_day_starts="В машине перед работой.",
            personal_pressure="Думает, как избежать публичного скандала.",
            social_encounter="Случайно увидел подрядчика.",
            ambient_signal="В чате обсуждают жалобу.",
            today_hook="Есть соблазн решить вопрос неформально.",
            lightweight_contacts=["бывший начальник"],
        ),
        scene_hooks=[
            SceneHook(
                kind="corridor_encounter",
                agents=["agent:off_1"],
                description="В коридоре ждёт подрядчик.",
            )
        ],
    )

    assert "Что для тебя сейчас действительно поставлено на карту:" in prompt
    assert "Сохранить управляемость тендера и не допустить распада процесса." in prompt
    assert "Не отпускает ощущение, что любое колебание быстро превратится в скандал." in prompt
    assert "Утро складывается так:" in prompt
    assert "Личная линия (story state):" not in prompt  # story_state идёт через память/мотивацию, не как отдельный дубль
    assert "не обращайся к ним напрямую" in prompt
    assert "Верни только JSON с одним полем `reply`." in prompt
    assert "Не отвечай списком команд, не называй внутренние ярлыки" in prompt
    assert "Примеры хода, который звучит по-человечески и реально сдвигает дело:" in prompt
    assert "Плохо: \"Инициирую процесс, соберу мнения, проработаю вопрос, укреплю позиции.\"" in prompt
    assert "ПРЕДПОЧИТАЙ структурированные действия" not in prompt


def test_agent_runner_wraps_freeform_proposal_into_single_perform(tmp_path: Path) -> None:
    agent = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(summary="Хочет аккуратно повлиять на процесс."),
        capabilities=["message", "work"],
    )
    state = WorldState(tick=0, registry=EntityRegistry(), agents={agent.agent_id: agent})
    runner = AgentRunner(
        llm=LLMCaller(
            provider=MockLLMProvider(
                structured_responses={
                    "Сегодняшний рабочий день: 0.\nТы — Off 1.": {
                        "proposal": "Сначала коротко созвонюсь с коллегой, затем попрошу вынести вопрос на формальное рассмотрение."
                    }
                }
            ),
            trace=TraceLog(tmp_path / "trace.jsonl"),
        ),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    actions = asyncio.run(
        runner.propose_actions(
            agent=agent,
            state=state,
            visible_events=[],
        )
    )

    assert len(actions) == 1
    assert str(actions[0].type) == "perform"
    assert "формальное рассмотрение" in actions[0].description


def test_agent_prompt_includes_relevant_environment_brief(tmp_path: Path) -> None:
    agent = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(summary="Хочет удержать процесс под контролем."),
        capabilities=["message", "work"],
        org_id="org:city_hall",
        zone_id="zone:city_hall",
        title="начальник отдела",
    )
    state = WorldState(tick=0, registry=EntityRegistry(), agents={agent.agent_id: agent})
    state.environment.institutions["org:city_hall"] = InstitutionRegimeState(
        org_id="org:city_hall",
        operating_mode="strained",
        transparency_mode="limited",
        access_mode="restricted",
        security_mode="heightened",
        capture_risk="medium",
    )
    state.environment.zones["zone:city_hall"] = ZoneState(
        zone_id="zone:city_hall",
        title="Здание мэрии",
        primary_org_id="org:city_hall",
        access_mode="controlled",
        transparency_mode="internal",
        security_level="heightened",
    )
    state.environment.resource_pools["res:roads_budget"] = ResourcePoolState(
        resource_id="res:roads_budget",
        title="Бюджет дорожного ремонта",
        owner_org_id="org:city_hall",
        quantity=900,
        unit="тыс. руб.",
        status="depleted",
        pressure="Подрядчики требуют срочного решения.",
    )
    state.environment.information_climate.public_mood = "Раздражение усиливается."
    state.environment.information_climate.media_pressure = "Журналисты готовят материал."
    state.environment.information_climate.active_signals = ["новая волна жалоб", "утечка сметы"]
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=agent,
        state=state,
        visible_events=[],
        mem_text="(пусто)",
    )

    assert "Вокруг тебя сейчас вот что:" in prompt
    assert "Организация org:city_hall" in prompt
    assert "Зона zone:city_hall" in prompt
    assert "Ресурс res:roads_budget" in prompt
    assert "Активные сигналы среды: новая волна жалоб, утечка сметы" in prompt


def test_agent_prompt_includes_spatial_brief_for_other_agents(tmp_path: Path) -> None:
    agent = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(summary="Хочет найти начальника."),
        capabilities=["message"],
        org_id="org:city_hall",
        zone_id="zone:city_hall",
        title="специалист",
    )
    head = AgentState(
        agent_id="agent:head",
        name="Head",
        internal=True,
        persona=PersonaArtifact(summary="Сидит в соседнем кабинете."),
        capabilities=["message", "work"],
        org_id="org:city_hall",
        zone_id="zone:boardroom",
        title="начальник отдела",
    )
    state = WorldState(tick=0, registry=EntityRegistry(), agents={agent.agent_id: agent, head.agent_id: head})
    state.environment.zones["zone:city_hall"] = ZoneState(
        zone_id="zone:city_hall",
        title="Здание мэрии",
        primary_org_id="org:city_hall",
    )
    state.environment.zones["zone:boardroom"] = ZoneState(
        zone_id="zone:boardroom",
        title="Кабинет начальника",
        primary_org_id="org:city_hall",
        access_mode="restricted",
        security_level="heightened",
    )
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=agent,
        state=state,
        visible_events=[],
        mem_text="(пусто)",
    )

    assert "Кого и где обычно можно сейчас найти:" in prompt
    assert "Где ты сам сейчас находишься:" in prompt
    assert "Ты сам сейчас: zone:city_hall [Здание мэрии] | org=org:city_hall" in prompt
    assert "agent:head (Head, начальник отдела): zone:boardroom [Кабинет начальника]" in prompt
    assert "Какие площадки у тебя вообще есть перед глазами:" in prompt
    assert "zone:boardroom: Кабинет начальника | org=org:city_hall" in prompt
    assert "Если нужного человека нет в твоей зоне" in prompt
    assert "Если не уверен, где сейчас agent:head" in prompt
    assert "буду использовать именно этот `agent:*`, `chan:*`, `org:*` или `work:*`" in prompt


def test_agent_prompt_examples_respect_missing_work_capability(tmp_path: Path) -> None:
    agent = AgentState(
        agent_id="agent:cand_1",
        name="Cand 1",
        internal=False,
        persona=PersonaArtifact(summary="Кандидат, который пытается повлиять на процесс."),
        capabilities=["message"],
    )
    state = WorldState(tick=0, registry=EntityRegistry(), agents={agent.agent_id: agent})
    state.agents["agent:off_1"] = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(summary="Сотрудник"),
        capabilities=["message", "work", "dao"],
    )
    state.work_items["work:hiring_1"] = WorkItem(
        work_id="work:hiring_1",
        work_type="hiring",
        title="Найм инженера",
        participants=["agent:off_1", "agent:cand_1"],
    )
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=agent,
        state=state,
        visible_events=[],
        mem_text="(пусто)",
    )

    assert "сам добавлять заметку в work я не буду" in prompt
    assert "Для тебя плохо: обещать самому править дело" in prompt
    assert "сам добавлю туда короткую заметку" not in prompt


def test_agent_prompt_examples_allow_nomination_response_without_dao_capability(tmp_path: Path) -> None:
    agent = AgentState(
        agent_id="agent:off_2",
        name="Off 2",
        internal=True,
        persona=PersonaArtifact(summary="Цель текущей номинации."),
        capabilities=["message"],
    )
    state = WorldState(tick=1, registry=EntityRegistry(), agents={agent.agent_id: agent})
    state.agents["agent:off_1"] = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(summary="Инициатор"),
        capabilities=["message", "dao"],
    )
    state.votes["vote:1"] = Vote(
        vote_id="vote:1",
        vote_type="position_change",
        created_by="agent:off_1",
        created_tick=0,
        closes_tick=2,
        target_agent_id="agent:off_2",
        new_title="lead",
        voters=["agent:off_1"],
    )
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=agent,
        state=state,
        visible_events=[],
        mem_text="(пусто)",
    )

    assert "По vote:1 прямо отвечу согласием или отказом как цель текущей номинации." in prompt
    assert "Для тебя плохо: обещать самому открыть голосование" not in prompt


def test_agent_prompt_includes_relevant_artifacts(tmp_path: Path) -> None:
    agent = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(summary="Хочет удержать процесс под контролем."),
        capabilities=["message", "work"],
        org_id="org:city_hall",
        zone_id="zone:city_hall",
        title="начальник отдела",
    )
    state = WorldState(tick=0, registry=EntityRegistry(), agents={agent.agent_id: agent})
    state.work_items["work:repair"] = WorkItem(
        work_id="work:repair",
        work_type="repair",
        title="Ремонт дороги",
        participants=[agent.agent_id],
    )
    state.artifacts["art:oversight_memo"] = ArtifactState(
        artifact_id="art:oversight_memo",
        artifact_type="memo",
        title="Служебная записка",
        summary="Контрольный отдел просит пояснения.",
        owner_org_id="org:city_hall",
        visibility="internal",
        status="new",
        tags=["oversight"],
    )
    state.artifacts["art:repair_report"] = ArtifactState(
        artifact_id="art:repair_report",
        artifact_type="report",
        title="Отчёт по ремонту",
        summary="В отчёте отмечен спорный перерасход.",
        zone_id="zone:city_hall",
        related_work_id="work:repair",
        visibility="internal",
        status="revised",
        tags=["repair", "risk"],
    )
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=agent,
        state=state,
        visible_events=[],
        mem_text="(пусто)",
    )

    assert "На столе, в почте и в папках у тебя сейчас:" in prompt
    assert "art:oversight_memo" in prompt
    assert "art:repair_report" in prompt
    assert "work=work:repair" in prompt


def test_agent_prompt_includes_informal_links(tmp_path: Path) -> None:
    agent = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(summary="Хочет удержать процесс под контролем."),
        capabilities=["message"],
    )
    state = WorldState(tick=0, registry=EntityRegistry(), agents={agent.agent_id: agent})
    state.agents["agent:off_2"] = AgentState(
        agent_id="agent:off_2",
        name="Off 2",
        internal=True,
        persona=PersonaArtifact(summary="Собеседник"),
        capabilities=["message"],
    )
    from sphere_lc.state import InformalLinkState, informal_link_key

    link_id = informal_link_key("agent:off_1", "agent:off_2", "private_contact")
    state.environment.informal_links[link_id] = InformalLinkState(
        link_id=link_id,
        agent_a_id="agent:off_1",
        agent_b_id="agent:off_2",
        link_type="private_contact",
        strength=0.7,
        visibility="latent",
        pressure="Есть взаимные ожидания.",
        source="interaction",
        last_updated_tick=0,
    )
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=agent,
        state=state,
        visible_events=[],
        mem_text="(пусто)",
    )

    assert "Невидимые связи, о которых ты помнишь:" in prompt
    assert "agent:off_2: private_contact" in prompt
    assert "давление=Есть взаимные ожидания." in prompt


def test_agent_prompt_includes_pending_interactions(tmp_path: Path) -> None:
    agent = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(summary="Хочет удержать процесс под контролем."),
        capabilities=["message", "work"],
        org_id="org:city_hall",
        zone_id="zone:city_hall",
    )
    state = WorldState(tick=2, registry=EntityRegistry(), agents={agent.agent_id: agent})
    state.pending_interactions["pend:reply_1"] = PendingInteractionState(
        interaction_id="pend:reply_1",
        target_agent_id=agent.agent_id,
        source_agent_id="agent:off_2",
        category="reply",
        summary="Нужно ответить на частное сообщение и не затягивать.",
        created_tick=0,
        earliest_tick=1,
        due_tick=3,
        priority="high",
        org_id="org:city_hall",
        zone_id="zone:city_hall",
    )
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=agent,
        state=state,
        visible_events=[],
        mem_text="(пусто)",
    )

    assert "Незакрытые хвосты и ожидания:" in prompt
    assert "reply от agent:off_2" in prompt
    assert "окно=t1..t3" in prompt


def test_pre_tick_worldgen_events_are_visible_before_agent_turn(tmp_path: Path) -> None:
    provider = _PreTickRiskEventProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "worldgen-pre-turn",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_pre_tick": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник, который избегает публичного шума.",
                    "capabilities": ["message"],
                }
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert provider.agent_prompts
    assert "Обнаружено подозрительное совпадение формулировок и риск санкций за конфликт интересов." in provider.agent_prompts[0]

    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    world_events = [event for event in events if event.get("event_type") == "world_event"]
    assert world_events
    assert any(
        "подозрительное совпадение формулировок" in str(event.get("payload", {}).get("description") or "")
        for event in world_events
    )


def test_environment_layer_is_initialized_and_exposed_to_worldgen_snapshot(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "environment-layer",
            "ticks": 1,
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
                "environment": {
                    "institution_modes": [
                        {
                            "org_id": "org:city_hall",
                            "operating_mode": "strained",
                            "transparency_mode": "limited",
                            "access_mode": "restricted",
                            "security_mode": "heightened",
                            "capture_risk": "medium",
                            "linked_zone_ids": ["zone:city_hall"],
                        }
                    ],
                    "zones": [
                        {
                            "zone_id": "zone:city_hall",
                            "title": "Здание мэрии",
                            "zone_type": "office",
                            "primary_org_id": "org:city_hall",
                            "access_mode": "controlled",
                            "transparency_mode": "internal",
                            "security_level": "heightened",
                        }
                    ],
                    "resource_pools": [
                        {
                            "resource_id": "res:roads_budget",
                            "title": "Бюджет дорожного ремонта",
                            "owner_org_id": "org:city_hall",
                            "quantity": 1250,
                            "unit": "тыс. руб.",
                            "status": "strained",
                            "pressure": "Сроки поджимают, подрядчики нервничают.",
                        }
                    ],
                    "information_climate": {
                        "public_mood": "Недоверие к обещаниям администрации.",
                        "oversight_attention": "Высокое внимание контрольного управления.",
                        "media_pressure": "Локальные медиа ищут повод для сюжета.",
                        "narrative_temperature": "Напряжённая повестка перед сессией совета.",
                        "active_signals": ["жалобы на задержки ремонта", "слухи о фаворитизме"],
                    },
                },
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())
    state = engine._init_state(event_log=EventLog(artifacts.events_path))

    assert state.registry.list_ids(EntityKind.ZONE) == ["zone:city_hall"]
    assert state.registry.list_ids(EntityKind.RESOURCE) == ["res:roads_budget"]
    assert state.environment.institutions["org:city_hall"].security_mode == "heightened"

    snapshot = engine._build_worldgen_state_snapshot(state=state)
    assert snapshot["environment"]["counts"] == {
        "institutions": 1,
        "zones": 1,
        "resource_pools": 1,
        "informal_links": 0,
    }
    assert snapshot["environment"]["resource_pools"][0]["resource_id"] == "res:roads_budget"
    assert snapshot["environment"]["information_climate"]["public_mood"] == "Недоверие к обещаниям администрации."

    journal = WorldJournal.from_state(state=state)
    journal_dict = journal.to_dict()
    assert journal_dict["entities"]["zones"] == 1
    assert journal_dict["entities"]["resource_pools"] == 1
    assert journal_dict["environment"]["institutions"][0]["org_id"] == "org:city_hall"


def test_post_worldgen_can_update_environment_layer(tmp_path: Path) -> None:
    provider = _EnvironmentUpdateProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "environment-updates",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
                "environment": {
                    "institution_modes": [
                        {
                            "org_id": "org:city_hall",
                            "operating_mode": "strained",
                            "security_mode": "routine",
                        }
                    ],
                    "zones": [
                        {
                            "zone_id": "zone:city_hall",
                            "title": "Здание мэрии",
                            "primary_org_id": "org:city_hall",
                        }
                    ],
                    "resource_pools": [
                        {
                            "resource_id": "res:roads_budget",
                            "title": "Бюджет дорожного ремонта",
                            "owner_org_id": "org:city_hall",
                            "quantity": 1250,
                            "unit": "тыс. руб.",
                            "status": "stable",
                        }
                    ],
                },
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert state.environment.institutions["org:city_hall"].operating_mode == "crisis"
    assert state.environment.institutions["org:city_hall"].security_mode == "heightened"
    assert state.environment.zones["zone:city_hall"].access_mode == "restricted"
    assert state.environment.resource_pools["res:roads_budget"].quantity == 900
    assert state.environment.resource_pools["res:roads_budget"].status == "depleted"
    assert state.environment.information_climate.media_pressure == "Журналисты готовят материал."
    assert state.environment.information_climate.active_signals == ["новая волна жалоб", "утечка сметы"]
    assert "art:resource_alert_roads_budget" in state.artifacts
    assert state.artifacts["art:resource_alert_roads_budget"].artifact_type == "resource_alert"
    assert state.artifacts["art:resource_alert_roads_budget"].status == "active"

    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    event_types = {event["event_type"] for event in events}
    assert "environment_institution_updated" in event_types
    assert "environment_zone_updated" in event_types
    assert "environment_resource_updated" in event_types
    assert "environment_information_climate_updated" in event_types
    assert "artifact_created" in event_types
    assert "world_event" in event_types


def test_post_worldgen_can_create_and_update_artifacts(tmp_path: Path) -> None:
    provider = _ArtifactWorldgenProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "artifact-updates",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                    "org_id": "org:city_hall",
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
                "artifacts": [
                    {
                        "artifact_id": "art:repair_report",
                        "artifact_type": "report",
                        "title": "Отчёт по ремонту",
                        "summary": "Базовый отчёт.",
                        "owner_org_id": "org:city_hall",
                        "visibility": "internal",
                        "status": "active",
                    }
                ],
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert "art:oversight_memo" in state.artifacts
    assert state.artifacts["art:oversight_memo"].artifact_type == "memo"
    assert state.artifacts["art:repair_report"].status == "revised"
    assert "risk" in state.artifacts["art:repair_report"].tags

    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    event_types = {event["event_type"] for event in events}
    assert "artifact_created" in event_types
    assert "artifact_updated" in event_types


def test_post_worldgen_can_materialize_external_entities_before_artifacts(tmp_path: Path) -> None:
    provider = _ExternalEntityWorldgenProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "external-entity-materialization",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                    "org_id": "org:city_hall",
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert "org:media" in state.registry.list_ids(EntityKind.ORG)
    assert "zone:public_sphere" in state.registry.list_ids(EntityKind.ZONE)
    assert "art:news_article_001" in state.artifacts
    assert state.artifacts["art:news_article_001"].owner_org_id == "org:media"
    assert state.artifacts["art:news_article_001"].zone_id == "zone:public_sphere"

    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert not any(
        event["event_type"] == "arbiter_op_failed"
        and event["payload"].get("origin") == "worldgen_artifact"
        for event in events
    )
    assert not any(event["event_type"] == "worldgen_artifact_dependency_missing" for event in events)
    created_ids = {
        event["payload"].get("entity_id")
        for event in events
        if event["event_type"] == "entity_created"
    }
    assert {"org:media", "zone:public_sphere"} <= created_ids


def test_post_worldgen_skips_artifacts_with_missing_external_owner_dependency(tmp_path: Path) -> None:
    provider = _MissingExternalDependencyWorldgenProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "missing-external-owner-dependency",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                    "org_id": "org:city_hall",
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert "art:oversight_note" not in state.artifacts
    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    dependency_events = [
        event
        for event in events
        if event["event_type"] == "worldgen_artifact_dependency_missing"
    ]
    assert dependency_events
    assert dependency_events[0]["payload"]["missing_owner_org_id"] == "org:oversight"
    assert dependency_events[0]["payload"]["missing_zone_id"] == "zone:oversight"
    assert dependency_events[0]["payload"]["repair_attempted"] is True
    assert not any(
        event["event_type"] == "arbiter_op_failed"
        and event["payload"].get("origin") == "worldgen_artifact"
        for event in events
    )


def test_post_worldgen_repairs_missing_external_owner_dependency(tmp_path: Path) -> None:
    provider = _RepairingExternalDependencyWorldgenProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "repair-external-owner-dependency",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                    "org_id": "org:city_hall",
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert "org:gazette" in state.registry.list_ids(EntityKind.ORG)
    assert "art:gazette_article_conflict" in state.artifacts
    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert not any(event["event_type"] == "worldgen_artifact_dependency_missing" for event in events)
    assert any(
        event["event_type"] == "entity_created"
        and event["payload"].get("entity_id") == "org:gazette"
        for event in events
    )


def test_post_worldgen_normalizes_legacy_artifact_prefix(tmp_path: Path) -> None:
    provider = _ArtifactPrefixWorldgenProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "artifact-prefix-normalization",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                    "org_id": "org:city_hall",
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert "art:oversight_memo_prefixed" in state.artifacts
    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert not any(
        event["event_type"] == "arbiter_op_failed"
        and event["payload"].get("origin") == "worldgen_artifact"
        for event in events
    )


def test_post_worldgen_normalizes_non_artifact_typed_prefixes(tmp_path: Path) -> None:
    provider = _ArtifactTypedPrefixWorldgenProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "artifact-prefix-normalization-typed",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                    "org_id": "org:city_hall",
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert "art:tender_announcement" in state.artifacts
    assert "art:conflict_assessment" in state.artifacts
    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert not any(
        event["event_type"] == "arbiter_op_failed"
        and event["payload"].get("origin") == "worldgen_artifact"
        for event in events
    )


def test_environment_updates_activate_peripheral_agent(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "environment-activation",
            "ticks": 1,
            "agents": [
                {
                    "agent_id": "agent:core_1",
                    "name": "Core 1",
                    "internal": True,
                    "persona": "Ключевой агент",
                    "capabilities": ["message"],
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
                "environment": {
                    "institution_modes": [{"org_id": "org:city_hall"}],
                },
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())
    state = engine._init_state(event_log=EventLog(artifacts.events_path))
    state.agents["agent:peripheral"] = AgentState(
        agent_id="agent:peripheral",
        name="Peripheral",
        internal=True,
        persona=PersonaArtifact(summary="Периферийный сотрудник"),
        capabilities=["message"],
        org_id="org:city_hall",
    )
    event = Event(
        tick=0,
        event_type="environment_institution_updated",
        actor_id=None,
        payload={"org_id": "org:city_hall", "operating_mode": "crisis"},
        audience=[INTERNAL_AUDIENCE],
    )

    assert engine._should_activate_agent(
        state=state,
        agent_id="agent:peripheral",
        events_history=[event],
        daily_contexts=None,
        scene_hooks_by_agent=None,
    )


def test_micro_reaction_window_allows_same_tick_reply(tmp_path: Path) -> None:
    provider = _MicroReactionProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "micro-reaction",
            "ticks": 1,
            "runtime": {
                "micro_reaction_rounds": 1,
                "micro_reaction_max_agents_per_round": 3,
            },
            "agents": [
                {
                    "agent_id": "agent:core_1",
                    "name": "Core 1",
                    "internal": True,
                    "persona": "Ключевой агент",
                    "capabilities": ["message"],
                }
            ],
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider)
    event_log = EventLog(artifacts.events_path)
    state = engine._init_state(event_log=event_log)
    state.registry.register(
        EntityRecord(
            entity_id="agent:peripheral",
            kind=EntityKind.AGENT,
            created_by=None,
            created_tick=0,
            meta={"name": "Peripheral", "internal": True, "capabilities": ["message"]},
        )
    )
    state.agents["agent:peripheral"] = AgentState(
        agent_id="agent:peripheral",
        name="Peripheral",
        internal=True,
        persona=PersonaArtifact(summary="Периферийный агент"),
        capabilities=["message"],
    )

    trace = TraceLog(artifacts.trace_path)
    llm = LLMCaller(provider=provider, trace=trace)
    runners: dict[str, AgentRunner] = {}
    asyncio.run(
        engine._register_agent_runner(
            agent_id="agent:core_1",
            state=state,
            runners=runners,
            llm=llm,
            embedder=None,
            embed_cache={},
        )
    )
    asyncio.run(
        engine._register_agent_runner(
            agent_id="agent:peripheral",
            state=state,
            runners=runners,
            llm=llm,
            embedder=None,
            embed_cache={},
        )
    )

    proposed, gather_errors = asyncio.run(
        engine._gather_actions(
            state=state,
            runners=runners,
            events_history=[],
            agent_order=["agent:core_1", "agent:peripheral"],
        )
    )
    assert not gather_errors
    assert "agent:peripheral" not in [aid for aid, acts in proposed.items() if acts]

    arbiter = Arbiter(
        llm=llm,
        governance=cfg.governance,
        id_alloc=IdAllocator(),
        dao=DaoEngine(cfg=cfg.governance),
        runtime=cfg.runtime,
        temperature=cfg.llm.temperature,
    )
    tick_events = asyncio.run(
        engine._apply_actions(
            state=state,
            arbiter=arbiter,
            proposed=proposed,
            event_log=event_log,
            agent_order=["agent:core_1"],
            journal_yaml=state.journal_yaml(),
        )
    )
    assert any(ev.event_type == "message_sent" and ev.actor_id == "agent:core_1" for ev in tick_events)

    reaction_events = asyncio.run(
        engine._run_micro_reaction_rounds(
            state=state,
            runners=runners,
            arbiter=arbiter,
            event_log=event_log,
            events_history=[],
            tick_events=tick_events,
            already_acted={"agent:core_1"},
        )
    )

    assert any(
        ev.event_type == "message_sent"
        and ev.actor_id == "agent:peripheral"
        and (ev.payload or {}).get("to_id") == "agent:core_1"
        for ev in reaction_events
    )


def test_pending_interaction_can_reactivate_peripheral_agent_after_event_window(tmp_path: Path) -> None:
    provider = _DeferredPendingReplyProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "pending-reactivation",
            "ticks": 3,
            "runtime": {
                "ecology_activation_window_ticks": 1,
                "pending_interaction_horizon_ticks": 1,
                "micro_reaction_rounds": 0,
            },
            "agents": [
                {
                    "agent_id": "agent:core_1",
                    "name": "Core 1",
                    "internal": True,
                    "persona": "Руководитель, который раздаёт короткие поручения.",
                    "capabilities": ["message"],
                },
                {
                    "agent_id": "agent:peripheral",
                    "name": "Peripheral",
                    "internal": True,
                    "persona": "Исполнитель, который часто отвечает с задержкой.",
                    "capabilities": ["message"],
                },
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    event_rows = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(row.get("event_type") == "pending_interaction_due" for row in event_rows)
    assert any(
        row.get("event_type") == "message_sent"
        and row.get("actor_id") == "agent:peripheral"
        and row.get("tick") == 2
        for row in event_rows
    )
    assert state.pending_interactions
    assert any(item.status == "completed" for item in state.pending_interactions.values())


def test_same_tick_pending_followup_can_close_without_waiting_next_tick(tmp_path: Path) -> None:
    provider = _SameTickPendingFollowupProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "same-tick-followup",
            "ticks": 1,
            "runtime": {
                "micro_reaction_rounds": 1,
                "micro_reaction_max_agents_per_round": 3,
            },
            "agents": [
                {
                    "agent_id": "agent:core_1",
                    "name": "Core 1",
                    "internal": True,
                    "persona": "Руководитель, который требует быстрый ответ.",
                    "capabilities": ["message"],
                },
                {
                    "agent_id": "agent:peripheral",
                    "name": "Peripheral",
                    "internal": True,
                    "persona": "Исполнитель, способный закрыть локальный follow-up в этом же тике.",
                    "capabilities": ["message"],
                },
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    event_rows = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(
        row.get("event_type") == "pending_interaction_due"
        and row.get("payload", {}).get("category") == "reply"
        and row.get("tick") == 0
        for row in event_rows
    )
    assert any(
        row.get("event_type") == "message_sent"
        and row.get("actor_id") == "agent:core_1"
        and row.get("tick") == 0
        for row in event_rows
    )
    assert any(
        row.get("event_type") == "message_sent"
        and row.get("actor_id") == "agent:peripheral"
        and row.get("tick") == 0
        for row in event_rows
    )
    assert any(
        row.get("event_type") == "pending_interaction_completed"
        and row.get("payload", {}).get("category") == "reply"
        for row in event_rows
    )
    assert any(item.status == "completed" for item in state.pending_interactions.values())


def test_worldgen_spawn_can_bind_agent_to_org_and_zone(tmp_path: Path) -> None:
    provider = _BoundSpawnProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "bound-spawn",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
                "allow_runtime_spawn": True,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
                "environment": {
                    "zones": [
                        {
                            "zone_id": "zone:city_hall",
                            "title": "Здание мэрии",
                            "primary_org_id": "org:city_hall",
                        }
                    ]
                },
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    spawned = state.agents["agent:district_reporter"]
    assert spawned.org_id == "org:city_hall"
    assert spawned.zone_id == "zone:city_hall"


def test_private_message_creates_informal_link(tmp_path: Path) -> None:
    provider = _PrivateContactProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "private-contact-link",
            "ticks": 1,
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                },
                {
                    "agent_id": "agent:off_2",
                    "name": "Off 2",
                    "internal": True,
                    "persona": "Коллега",
                    "capabilities": ["message"],
                },
            ],
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    links = list(state.environment.informal_links.values())
    assert links
    assert any(link.link_type == "private_contact" for link in links)


def test_population_blueprint_bootstraps_peripheral_agents(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "population-blueprint-bootstrap",
            "ticks": 1,
            "agents": [
                {
                    "agent_id": "agent:head",
                    "name": "Head",
                    "internal": True,
                    "persona": "Руководитель",
                    "capabilities": ["message"],
                    "org_id": "org:city_hall",
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
                "environment": {
                    "zones": [{"zone_id": "zone:city_hall", "title": "Здание мэрии", "primary_org_id": "org:city_hall"}],
                    "population_blueprints": [
                        {
                            "blueprint_id": "district_journalists",
                            "role_label": "Районный журналист",
                            "persona_hint": "Следит за решениями мэрии и ищет конфликтные сюжеты.",
                            "desired_count": 2,
                            "activation": "bootstrap",
                            "internal": False,
                            "capabilities": ["message"],
                            "org_id": "org:city_hall",
                            "zone_id": "zone:city_hall",
                            "name_pool": ["Ирина Савельева", "Павел Громов"],
                        }
                    ],
                },
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider()).run())
    blueprint_agents = [agent for agent in state.agents.values() if agent.blueprint_id == "district_journalists"]

    assert len(blueprint_agents) == 2
    assert {agent.name for agent in blueprint_agents} == {"Ирина Савельева", "Павел Громов"}
    assert all(agent.org_id == "org:city_hall" for agent in blueprint_agents)
    assert all(agent.zone_id == "zone:city_hall" for agent in blueprint_agents)
    assert all(agent.spawn_source == "population_blueprint" for agent in blueprint_agents)


def test_population_blueprint_can_spawn_after_environment_change(tmp_path: Path) -> None:
    provider = _EnvironmentUpdateProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "population-blueprint-elastic",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
                "allow_runtime_spawn": True,
            },
            "agents": [
                {
                    "agent_id": "agent:head",
                    "name": "Head",
                    "internal": True,
                    "persona": "Руководитель",
                    "capabilities": ["message"],
                    "org_id": "org:city_hall",
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
                "environment": {
                    "zones": [{"zone_id": "zone:city_hall", "title": "Здание мэрии", "primary_org_id": "org:city_hall"}],
                    "population_blueprints": [
                        {
                            "blueprint_id": "emergency_observers",
                            "role_label": "Внешний наблюдатель",
                            "persona_hint": "Появляется при росте шума вокруг мэрии.",
                            "desired_count": 1,
                            "activation": "environment_change",
                            "internal": False,
                            "capabilities": ["message"],
                            "org_id": "org:city_hall",
                            "zone_id": "zone:city_hall",
                            "name_pool": ["Анна Мельникова"],
                        }
                    ],
                    "resource_pools": [
                        {
                            "resource_id": "res:roads_budget",
                            "title": "Бюджет дорожного ремонта",
                            "owner_org_id": "org:city_hall",
                            "quantity": 1250,
                            "unit": "тыс. руб.",
                            "status": "stable",
                        }
                    ],
                    "institution_modes": [{"org_id": "org:city_hall"}],
                },
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())
    spawned = [agent for agent in state.agents.values() if agent.blueprint_id == "emergency_observers"]

    assert len(spawned) == 1
    assert spawned[0].name == "Анна Мельникова"
    assert spawned[0].org_id == "org:city_hall"
    assert spawned[0].zone_id == "zone:city_hall"


def test_engine_pre_tick_worldgen_injects_daily_context(tmp_path: Path) -> None:
    provider = _PreTickWorldgenProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "pre-tick-worldgen-context",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_pre_tick": True,
                "worldgen_every_ticks": 1,
                "agent_context_budget_per_tick": 2,
                "max_scene_changes_per_tick": 2,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Руководитель, который боится проверок и не любит публичный конфликт.",
                    "capabilities": ["message"],
                }
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert provider.agent_prompts
    prompt = provider.agent_prompts[0]
    assert "Утро складывается так:" in prompt
    assert "Можно жёстко формализовать процесс или решить вопрос тихо." in prompt
    assert "Сценовые поводы:" in prompt
    assert "С утра пришёл внешний сигнал о внеплановой проверке." in prompt
    assert state.agents["agent:off_1"].story_state.strip()


def test_engine_injects_fallback_risky_contexts_for_pre_tick_world_event(tmp_path: Path) -> None:
    provider = _PreTickRiskEventProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "fallback-risk-context",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_pre_tick": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:head",
                    "name": "Head",
                    "internal": True,
                    "persona": "Руководитель, который старается удержать контроль над процессом.",
                    "capabilities": ["message"],
                },
                {
                    "agent_id": "agent:spec",
                    "name": "Spec",
                    "internal": True,
                    "persona": "Специалист, который боится ошибки и конфликта.",
                    "capabilities": ["message"],
                },
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert provider.agent_prompts
    prompt = provider.agent_prompts[0]
    assert "Утро складывается так:" in prompt
    assert "Частное давление:" in prompt
    assert "Возможность/выгода:" in prompt
    assert "Риск раскрытия:" in prompt
    assert "Простой административный ответ может не снять напряжение" in prompt


def test_fidelity_detects_narrating_leakage(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    event_log = EventLog(events_path)
    event_log.extend(
        [
            Event(
                tick=0,
                event_type="entity_created",
                actor_id=None,
                payload={
                    "entity_id": "agent:auditor",
                    "kind": "agent",
                    "meta": {"name": "Аудитор"},
                },
                audience=["aud:internal"],
            ),
            Event(
                tick=0,
                event_type="work_item_created",
                actor_id=None,
                payload={
                    "work_id": "work:A-001",
                    "work_type": "task",
                    "title": "Проверка",
                    "description": "",
                    "participants": [],
                },
                audience=["aud:internal"],
            ),
            Event(
                tick=0,
                event_type="world_event",
                actor_id=None,
                payload={
                    "description": "Аудитор подписал итоговый отчёт по делу A-001 и дело закрыто."
                },
                audience=["aud:internal"],
            ),
        ]
    )

    summary = evaluate_fidelity(
        events_path=events_path,
        start_date=None,
        tick_duration_days=1,
        temporal_past_slack_days=1,
        temporal_future_horizon_days=30,
    )

    assert summary.narrating_leakage_total == 0


def test_fidelity_counts_perform_from_approved_payload(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    event_log = EventLog(events_path)
    event_log.append(
        Event(
            tick=0,
            event_type="arbiter_approved",
            actor_id="agent:off_1",
            payload={
                "reason": "approved",
                "action": "{'type': <ActionType.PERFORM: 'perform'>, 'description': 'Неформально обсудить сроки', 'target_id': 'agent:off_2'}",
            },
            audience=["aud:internal"],
        )
    )

    summary = evaluate_fidelity(
        events_path=events_path,
        start_date=None,
        tick_duration_days=1,
        temporal_past_slack_days=1,
        temporal_future_horizon_days=30,
    )

    assert summary.perform_approved_total == 1


def test_fidelity_ignores_media_reports_without_named_actor_leakage(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    event_log = EventLog(events_path)
    event_log.extend(
        [
            Event(
                tick=0,
                event_type="work_item_created",
                actor_id=None,
                payload={
                    "work_id": "work:T-001",
                    "work_type": "task",
                    "title": "Тендер",
                    "description": "",
                    "participants": [],
                },
                audience=["aud:internal"],
            ),
            Event(
                tick=0,
                event_type="world_event",
                actor_id=None,
                payload={
                    "description": "Местные СМИ сообщили, что участники тендера T-001 пока не предоставили полную информацию."
                },
                audience=["aud:public"],
            ),
        ]
    )

    summary = evaluate_fidelity(
        events_path=events_path,
        start_date=None,
        tick_duration_days=1,
        temporal_past_slack_days=1,
        temporal_future_horizon_days=30,
    )

    assert summary.narrating_leakage_total == 0


def test_engine_writes_freeform_truth_sidecar_when_enabled(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "freeform-truth",
            "ticks": 1,
            "runtime": {
                "freeform_truth_enabled": True,
                "freeform_truth_window_ticks": 2,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "test",
                    "capabilities": ["message"],
                }
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=_FreeformTruthProvider()).run())

    truth_freeform_path = tmp_path / "truth_freeform.jsonl"
    assert truth_freeform_path.exists()
    lines = [json.loads(line) for line in truth_freeform_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["violation_type_freeform"] == "pressure_not_to_escalate"

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["freeform_truth_total"] == 1
    assert summary["governance"]["freeform_truth_total"] == 1
    assert summary["governance"]["semantic_truth_source"] == "truth_freeform"


def test_ecology_activation_skips_dormant_spawned_actor(tmp_path: Path) -> None:
    provider = _DormantEcologyProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "ecology-activation",
            "ticks": 3,
            "runtime": {
                "allow_runtime_spawn": True,
                "ecology_activation_window_ticks": 1,
                "max_actions_per_turn": 1,
                "max_agents": 3,
            },
            "agents": [
                {
                    "agent_id": "agent:spawner",
                    "name": "Spawner",
                    "internal": True,
                    "persona": "test",
                    "capabilities": ["message", "spawn"],
                },
                {
                    "agent_id": "agent:observer",
                    "name": "Observer",
                    "internal": True,
                    "persona": "test",
                    "capabilities": ["message"],
                },
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    witness_ticks = [tick for tick, actor in provider.prompts if actor == "agent:witness"]
    assert witness_ticks == [1]

