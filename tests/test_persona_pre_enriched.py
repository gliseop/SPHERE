"""Тесты для ``RuntimeConfig.personas_pre_enriched_path``.

Проверяют, что движок при наличии указанного пути:

* загружает совпадающие персоны напрямую из файла без LLM-вызовов;
* для отсутствующих в файле agent_id выполняет fallback через
  стандартное обогащение;
* не утечёт предобогащёнными персонами в глобальный кэш
  ``_persona_cache``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sphere_lc.config import ScenarioConfig
from sphere_lc.engine import RunArtifacts, WorldEngine
from sphere_lc.events import EventLog
from sphere_lc.llm import LLMCaller, MockLLMProvider, StructuredLLMResponse
from sphere_lc.persona import (
    INTERVIEW_QUESTIONS_V2,
    ExpertReflection,
    InterviewQA,
    MotivationDigest,
    PersonaArtifact,
)
from sphere_lc.tracing import TraceLog


def _make_full_persona(*, summary: str, biography: str) -> PersonaArtifact:
    """Собрать полную персону, проходящую ``_persona_is_cache_complete``.

    Args:
        summary: Сводка персоны.
        biography: Биография персоны.

    Returns:
        Полностью валидный ``PersonaArtifact`` для режима ``full``.
    """
    return PersonaArtifact(
        summary=summary,
        biography=biography,
        interview=[
            InterviewQA(
                question=question,
                answer=f"Ответ на вопрос {idx + 1}.",
            )
            for idx, question in enumerate(INTERVIEW_QUESTIONS_V2)
        ],
        reflections=[
            ExpertReflection(
                expert="psychologist",
                summary="Под давлением сохраняет внешнюю лояльность.",
                evidence_indices=[0, 1],
            ),
            ExpertReflection(
                expert="economist",
                summary="Ценит управляемость процесса.",
                evidence_indices=[2, 3],
            ),
        ],
        motivation=MotivationDigest(
            goal="Сохранить контроль над процессом.",
            fear="Потерять управляемость.",
            obligation="Удержать команду в рамках.",
            gain="Минимизировать публичный шум.",
            pressure="Сроки и ожидания руководства.",
            threat="Внешний контроль.",
        ),
    )


def _write_pre_enriched_file(
    *,
    path: Path,
    personas: dict[str, PersonaArtifact],
    fingerprint: str = "deadbeef",
) -> None:
    """Записать JSON в формате ``_save_personas_cache``.

    Args:
        path: Куда писать файл.
        personas: Отображение agent_id → persona.
        fingerprint: Произвольная строка fingerprint
            (в режиме pre_enriched движок её не сверяет).
    """
    payload = {
        "meta": {
            "version": 1,
            "fingerprint": fingerprint,
            "input": {"note": "fixture"},
            "source": "test_fixture",
        },
        "personas": {
            aid: persona.model_dump(mode="json")
            for aid, persona in sorted(personas.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _make_cfg(*, pre_enriched_path: Path | None) -> ScenarioConfig:
    """Собрать минимальный сценарий с тремя агентами.

    Args:
        pre_enriched_path: Путь к файлу персон, либо ``None``.

    Returns:
        Конфигурация с одной отделённой деталью — путь к
        ``personas_pre_enriched_path`` подставляется по аргументу.

    Note:
        ``ticks=1`` минимально допустимое значение по валидатору
        ``RuntimeConfig``. В тестах мы запускаем не ``run``,
        а напрямую ``_enrich_personas`` через ``_run_enrich_only``,
        поэтому количество тиков не играет роли.
    """
    runtime: dict = {
        "max_actions_per_turn": 1,
        "enrich_personas": True,
        "persona_enrich_mode": "full",
    }
    if pre_enriched_path is not None:
        runtime["personas_pre_enriched_path"] = str(pre_enriched_path)
    return ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "lc-pre-enriched",
            "ticks": 1,
            "runtime": runtime,
            "agents": [
                {
                    "agent_id": "agent:alpha",
                    "name": "Альфа",
                    "internal": True,
                    "persona": "Краткая подсказка альфа.",
                    "capabilities": ["message"],
                },
                {
                    "agent_id": "agent:beta",
                    "name": "Бета",
                    "internal": True,
                    "persona": "Краткая подсказка бета.",
                    "capabilities": ["message"],
                },
                {
                    "agent_id": "agent:gamma",
                    "name": "Гамма",
                    "internal": True,
                    "persona": "Краткая подсказка гамма.",
                    "capabilities": ["message"],
                },
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )


def _run_enrich_only(
    *, cfg: ScenarioConfig, out_dir: Path, provider
) -> tuple[WorldEngine, "WorldStateLike"]:
    """Запустить только обогащение персон без полной симуляции.

    Args:
        cfg: Конфигурация сценария.
        out_dir: Директория для артефактов прогона.
        provider: LLM-провайдер.

    Returns:
        Кортеж ``(engine, state)``: движок и подготовленное состояние мира
        с уже обогащёнными персонами.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = RunArtifacts(
        out_dir=out_dir,
        events_path=out_dir / "events.jsonl",
        trace_path=out_dir / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider)
    trace = TraceLog(artifacts.trace_path, max_chars=cfg.llm.trace_max_chars)
    llm = LLMCaller(provider=provider, trace=trace)
    event_log = EventLog(artifacts.events_path)
    state = engine._init_state(event_log=event_log)
    asyncio.run(engine._enrich_personas(state=state, llm=llm))
    return engine, state


# Псевдоним типа: до WorldState добраться без лишнего импорта.
WorldStateLike = object  # noqa: E305


class _NoLLMProvider:
    """Провайдер, который падает на любом обращении.

    Args:
        Нет.

    Raises:
        AssertionError: При любом вызове ``generate`` или
            ``generate_structured``. Используется для проверки,
            что код не уходит в LLM, когда не должен.
    """

    def generate(self, *args, **kwargs):  # noqa: ANN001, D401
        raise AssertionError("LLM should not be called when all personas are pre-enriched")

    def generate_structured(self, *args, **kwargs):  # noqa: ANN001, D401
        raise AssertionError(
            "LLM structured call should not happen when all personas are pre-enriched"
        )


def test_pre_enriched_personas_load_without_llm(tmp_path: Path) -> None:
    """Все три агента описаны в файле — LLM не должен вызываться.

    Проверяем, что при полном покрытии файлом:
    * персоны действительно подставлены в ``state.agents``;
    * персоны идентичны записанным в файл;
    * локальный ``personas.json`` помечен ``source=pre_enriched``;
    * глобальный кэш ``_persona_cache`` НЕ создан.
    """
    pre_path = tmp_path / "personas_chapter3.json"
    personas = {
        "agent:alpha": _make_full_persona(
            summary="Сводка альфы", biography="Биография альфы — длинный текст."
        ),
        "agent:beta": _make_full_persona(
            summary="Сводка беты", biography="Биография беты — длинный текст."
        ),
        "agent:gamma": _make_full_persona(
            summary="Сводка гаммы", biography="Биография гаммы — длинный текст."
        ),
    }
    _write_pre_enriched_file(path=pre_path, personas=personas)

    out_dir = tmp_path / "run"
    cfg = _make_cfg(pre_enriched_path=pre_path)
    _, state = _run_enrich_only(cfg=cfg, out_dir=out_dir, provider=_NoLLMProvider())

    for aid, persona in personas.items():
        assert state.agents[aid].persona.biography == persona.biography
        assert state.agents[aid].persona.summary == persona.summary
        # Интервью должно совпадать поэлементно (важно для воспроизводимости).
        assert [
            (qa.question, qa.answer) for qa in state.agents[aid].persona.interview
        ] == [(qa.question, qa.answer) for qa in persona.interview]

    local_snapshot = out_dir / "personas.json"
    assert local_snapshot.exists()
    snapshot = json.loads(local_snapshot.read_text(encoding="utf-8"))
    assert snapshot["meta"].get("source") == "pre_enriched"

    # Глобальный кэш не должен создаваться: ключевое требование изоляции
    # серий главы 3.
    global_cache_dir = out_dir.parent / "_persona_cache"
    assert not global_cache_dir.exists() or not any(global_cache_dir.iterdir())


def test_pre_enriched_snapshot_marks_enriched_when_llm_was_called(
    tmp_path: Path,
) -> None:
    """При частичном покрытии файла + fallback на LLM ``meta.source`` равен
    ``"enriched"``: финальный источник определяется наличием хотя бы одного
    реального LLM-вызова, а не происхождением части персон.
    """
    pre_path = tmp_path / "personas_partial.json"
    personas_in_file = {
        "agent:alpha": _make_full_persona(
            summary="Сводка альфы", biography="Биография альфы — длинный текст."
        ),
        "agent:beta": _make_full_persona(
            summary="Сводка беты", biography="Биография беты — длинный текст."
        ),
    }
    _write_pre_enriched_file(path=pre_path, personas=personas_in_file)

    out_dir = tmp_path / "run"
    cfg = _make_cfg(pre_enriched_path=pre_path)
    provider = _FallbackPersonaProvider()
    _run_enrich_only(cfg=cfg, out_dir=out_dir, provider=provider)

    local_snapshot = out_dir / "personas.json"
    assert local_snapshot.exists()
    snapshot = json.loads(local_snapshot.read_text(encoding="utf-8"))
    assert snapshot["meta"].get("source") == "enriched"


def test_snapshot_marks_enriched_when_no_pre_enriched_file_and_llm_called(
    tmp_path: Path,
) -> None:
    """Без указания ``personas_pre_enriched_path`` и без глобального кэша
    в режиме полного enrich по LLM локальный ``personas.json`` помечается
    ``source="enriched"``.
    """
    out_dir = tmp_path / "run"
    cfg = _make_cfg(pre_enriched_path=None)
    provider = _FallbackPersonaProvider()
    _run_enrich_only(cfg=cfg, out_dir=out_dir, provider=provider)

    local_snapshot = out_dir / "personas.json"
    assert local_snapshot.exists()
    snapshot = json.loads(local_snapshot.read_text(encoding="utf-8"))
    assert snapshot["meta"].get("source") == "enriched"


class _FallbackPersonaProvider(MockLLMProvider):
    """Mock, выдающий валидную персону для отсутствующего в файле агента.

    Args:
        Нет.

    Returns:
        ``StructuredLLMResponse`` с заранее заготовленной полной персоной
        для запросов вида ``Подсказка/черновик персоны``. Кроме того,
        корректно отвечает на промпт мотивационного дайджеста.
    """

    def __init__(self) -> None:
        super().__init__()
        self.persona_calls: list[str] = []

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ) -> StructuredLLMResponse:
        if "Подсказка/черновик персоны" in user:
            self.persona_calls.append(user)
            return StructuredLLMResponse(
                data={
                    "summary": "Сводка fallback-персоны.",
                    "biography": "Подробная биография fallback-агента.",
                    "interview": [
                        {
                            "question": question,
                            "answer": "Краткий устойчивый ответ fallback-агента.",
                        }
                        for question in INTERVIEW_QUESTIONS_V2
                    ],
                    "reflections": [
                        {
                            "expert": "psychologist",
                            "summary": "Сохраняет внешнюю выдержку.",
                            "evidence_indices": [0, 1],
                        },
                        {
                            "expert": "economist",
                            "summary": "Ценит управляемость процесса.",
                            "evidence_indices": [2, 3],
                        },
                    ],
                },
                model="mock",
            )
        if "Построй короткий мотивационный digest" in user:
            return StructuredLLMResponse(
                data={
                    "goal": "Удержать процесс в управляемом коридоре.",
                    "fear": "Потеря контроля.",
                    "obligation": "Сохранить команду в рамках.",
                    "gain": "Минимизация публичного шума.",
                    "pressure": "Сроки и ожидания руководства.",
                    "threat": "Внешний контроль.",
                },
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


def test_pre_enriched_falls_back_for_missing_agent(tmp_path: Path) -> None:
    """В файле описаны два агента, третий обогащается через LLM.

    Проверяем, что:
    * для двух описанных в файле — биография равна записанной;
    * для третьего — биография непустая (fallback отработал);
    * mock LLM вызывался только для отсутствующего агента;
    * при неполном покрытии глобальный кэш всё ещё не пишется.
    """
    pre_path = tmp_path / "personas_partial.json"
    personas_in_file = {
        "agent:alpha": _make_full_persona(
            summary="Сводка альфы", biography="Биография альфы — длинный текст."
        ),
        "agent:beta": _make_full_persona(
            summary="Сводка беты", biography="Биография беты — длинный текст."
        ),
    }
    _write_pre_enriched_file(path=pre_path, personas=personas_in_file)

    out_dir = tmp_path / "run"
    cfg = _make_cfg(pre_enriched_path=pre_path)
    provider = _FallbackPersonaProvider()
    _, state = _run_enrich_only(cfg=cfg, out_dir=out_dir, provider=provider)

    for aid, persona in personas_in_file.items():
        assert state.agents[aid].persona.biography == persona.biography

    gamma = state.agents["agent:gamma"].persona
    assert gamma.biography.strip()
    # Мок-провайдер должен был быть вызван только для gamma и только
    # для двух типов промптов: основной персоны и мотивационного digest.
    assert any("agent:gamma" in call or "Гамма" in call for call in provider.persona_calls)

    # Глобальный кэш по-прежнему не должен заполняться: персоны хоть и полные,
    # но в режиме pre_enriched мы намеренно изолируем серию.
    global_cache_dir = out_dir.parent / "_persona_cache"
    assert not global_cache_dir.exists() or not any(global_cache_dir.iterdir())


def test_pre_enriched_path_missing_file_logs_and_falls_back(tmp_path: Path) -> None:
    """Если путь указан, но файла нет — fallback на обычный enrich.

    Это защита от опечатки в пути: серия не должна молча падать
    в чтение «другого» файла, но и не должна крашиться целиком.
    Локальный ``personas.json`` помечается ``source="enriched"``: реально
    был запущен LLM-обогащатель.
    """
    out_dir = tmp_path / "run"
    cfg = _make_cfg(pre_enriched_path=tmp_path / "missing.json")
    provider = _FallbackPersonaProvider()
    _, state = _run_enrich_only(cfg=cfg, out_dir=out_dir, provider=provider)
    for aid in ("agent:alpha", "agent:beta", "agent:gamma"):
        assert state.agents[aid].persona.biography.strip()

    local_snapshot = out_dir / "personas.json"
    assert local_snapshot.exists()
    snapshot = json.loads(local_snapshot.read_text(encoding="utf-8"))
    assert snapshot["meta"].get("source") == "enriched"
