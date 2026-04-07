"""Персоны для SPHERE-LC (PersonaLibrary / PersonaGenerator).

Персона в greenfield-ветке — это не одна строка, а набор артефактов:
- краткая сводка (summary) для быстрого контекста;
- развернутая биография (biography) для долгосрочной памяти;
- интервью (30 вопросов) как устойчивые поведенческие якоря.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .prompts import render_prompt

if TYPE_CHECKING:
    from .llm import LLMCaller


INTERVIEW_QUESTIONS_V2: list[str] = [
    # Повседневная жизнь (3)
    "Опишите обычный день из вашей жизни — как он устроен, что для вас важно в повседневном распорядке?",
    "Как вы проводите свободное время и что это говорит о вас как о человеке?",
    "Какие бытовые привычки или ритуалы для вас принципиальны и почему?",
    # Работа и карьера (4)
    "Как вы принимаете решения под давлением на работе?",
    "Что вас мотивирует в работе больше всего?",
    "Расскажите о карьерном решении, которым вы гордитесь.",
    "Был ли момент, когда вы сомневались в правильности своих действий на работе? Что произошло?",
    # Финансы и деньги (3)
    "Как вы относитесь к деньгам — что они для вас значат помимо материального обеспечения?",
    "Расскажите о ситуации, когда вы стояли перед выбором между финансовой выгодой и чем-то другим.",
    "Как вы реагируете, когда видите возможность заработать больше, но это связано с определённым риском?",
    # Отношения и доверие (4)
    "Что для вас значит лояльность коллегам и начальству?",
    "Как вы выбираете, кому доверять в профессиональной среде?",
    "Расскажите о случае, когда вас подвёл человек, которому вы доверяли. Как вы это пережили?",
    "Как вы строите отношения с людьми, от которых зависит ваш успех?",
    # Ценности и мораль (4)
    "Как вы относитесь к ситуациям, когда формальные правила мешают достижению результата?",
    "Были ли в вашей жизни моменты, когда приходилось поступаться принципами ради практической пользы?",
    "Что для вас справедливость — абстрактное понятие или руководство к действию?",
    "Как вы реагируете, когда видите, что другие нарушают правила и остаются безнаказанными?",
    # Конфликты и давление (4)
    "Как вы обычно реагируете на конфликты с коллегами?",
    "Расскажите о ситуации, когда вам приходилось действовать под серьёзным давлением со стороны руководства или окружения.",
    "Как вы ведёте себя, когда кто-то пытается вами манипулировать?",
    "Были ли ситуации, когда вы сами оказывали давление на других? Как вы это обосновывали?",
    # Власть и иерархия (4)
    "Как вы относитесь к власти — стремитесь к ней или она вас тяготит?",
    "Расскажите о ситуации, когда вы имели контроль над важным ресурсом или решением. Как вы этим распорядились?",
    "Как вы воспринимаете людей, которые занимают более высокое положение, чем вы?",
    "Что вы думаете о людях, которые используют служебное положение в личных целях?",
    # Нарративная идентичность (4)
    "Расскажите историю из детства или юности, которая сформировала ваше отношение к справедливости.",
    "Какой эпизод из вашей карьеры определил вас как профессионала?",
    "Если бы вы могли изменить одно решение в своей жизни, какое бы выбрали и почему?",
    "Как бы вы описали себя через 10 лет?",
]

_NAME_KEY_RE = re.compile(r"[^\w]+", flags=re.UNICODE)


def chunk_text(text: str, *, max_chars: int = 900) -> list[str]:
    """Разбить большой текст на чанки по границе абзацев/предложений."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        if size + len(para) + 1 > max_chars and buf:
            chunks.append("\n".join(buf).strip())
            buf = []
            size = 0
        buf.append(para)
        size += len(para) + 1
    if buf:
        chunks.append("\n".join(buf).strip())
    return [c for c in chunks if c]


class InterviewQA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    answer: str


class ExpertReflection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expert: str
    summary: str
    evidence_indices: list[int] = Field(default_factory=list)


class MotivationDigest(BaseModel):
    """Краткий мотивационный слой, извлечённый из интервью и рефлексий."""

    model_config = ConfigDict(extra="forbid")

    goal: str = ""
    fear: str = ""
    obligation: str = ""
    gain: str = ""
    pressure: str = ""
    threat: str = ""

    @field_validator("goal", "fear", "obligation", "gain", "pressure", "threat")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        return (v or "").strip()

    def has_content(self) -> bool:
        return any(
            [
                self.goal.strip(),
                self.fear.strip(),
                self.obligation.strip(),
                self.gain.strip(),
                self.pressure.strip(),
                self.threat.strip(),
            ]
        )


class PersonaArtifact(BaseModel):
    """Полный артефакт персоны."""

    model_config = ConfigDict(extra="forbid")

    persona_id: str | None = None
    summary: str = ""
    biography: str = ""
    interview: list[InterviewQA] = Field(default_factory=list)
    reflections: list[ExpertReflection] = Field(default_factory=list)
    motivation: MotivationDigest | None = None

    @field_validator("summary", "biography")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        return (v or "").strip()

    def interview_as_text(self) -> str:
        lines = []
        for qa in self.interview:
            q = (qa.question or "").strip()
            a = (qa.answer or "").strip()
            if not q or not a:
                continue
            lines.append(f"Q: {q}\nA: {a}")
        return "\n\n".join(lines).strip()

    def reflections_as_text(self) -> str:
        lines = []
        for item in self.reflections:
            expert = (item.expert or "").strip()
            summary = (item.summary or "").strip()
            if not expert or not summary:
                continue
            lines.append(f"{expert}: {summary}")
        return "\n".join(lines).strip()


@dataclass(slots=True)
class SocialLink:
    """Связь социального графа, извлечённая из биографии/интервью."""

    name: str
    relation: str
    relevance: str
    persona_hint: str
    internal: bool
    capabilities: list[str]


def social_link_name_key(name: str) -> str:
    """Нормализованный ключ для дедупликации ссылок на одного человека."""

    parts = [p for p in _NAME_KEY_RE.split((name or "").casefold()) if p]
    return "_".join(sorted(parts))


def _levenshtein_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr.append(
                min(
                    prev[j] + 1,
                    curr[j - 1] + 1,
                    prev[j - 1] + cost,
                )
            )
        prev = curr
    return prev[-1]


def social_link_match_key(name: str, existing_keys: list[str]) -> str:
    """Подобрать ключ уже встречавшегося имени по нечёткому совпадению."""

    key = social_link_name_key(name)
    if not key:
        return ""

    best_key = key
    best_distance: int | None = None
    for candidate in existing_keys:
        if not candidate:
            continue
        dist = _levenshtein_distance(key, candidate)
        max_len = max(len(key), len(candidate))
        if dist == 0:
            return candidate
        if max_len > 0 and (dist <= 2 or (dist / max_len) <= 0.18):
            if best_distance is None or dist < best_distance:
                best_key = candidate
                best_distance = dist
    return best_key


@dataclass(slots=True)
class PersonaLibrary:
    """Локальная библиотека персон (YAML/JSON)."""

    root_dir: Path

    def load(self) -> dict[str, PersonaArtifact]:
        items: dict[str, PersonaArtifact] = {}
        if not self.root_dir.exists():
            return items
        for path in sorted(self.root_dir.glob("**/*")):
            if path.is_dir():
                continue
            if path.suffix.lower() not in (".yaml", ".yml", ".json"):
                continue
            try:
                raw = self._load_file(path)
                persona = PersonaArtifact.model_validate(raw)
            except Exception:
                continue
            if not persona.persona_id:
                # Без стабильного ID библиотека бесполезна.
                continue
            items[persona.persona_id] = persona
        return items

    @staticmethod
    def _load_file(path: Path) -> dict[str, Any]:
        if path.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore
            except ImportError as exc:
                raise ImportError(
                    'Для PersonaLibrary YAML установите зависимости: pip install -e ".[lc]"'
                ) from exc
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Persona file must be a mapping/object")
        return data


def _persona_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "biography": {"type": "string"},
            "interview": {
                "type": "array",
                "minItems": 10,
                "maxItems": 40,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "question": {"type": "string"},
                        "answer": {"type": "string"},
                    },
                    "required": ["question", "answer"],
                },
            },
            "reflections": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "expert": {"type": "string"},
                        "summary": {"type": "string"},
                        "evidence_indices": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["expert", "summary"],
                },
            },
        },
        "required": ["summary", "biography", "interview"],
    }


def _persona_core_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "biography": {"type": "string"},
        },
        "required": ["summary", "biography"],
    }


def _reflection_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reflections": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "expert": {"type": "string"},
                        "summary": {"type": "string"},
                        "evidence_indices": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["expert", "summary"],
                },
            }
        },
        "required": ["reflections"],
    }


def _motivation_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "goal": {"type": "string"},
            "fear": {"type": "string"},
            "obligation": {"type": "string"},
            "gain": {"type": "string"},
            "pressure": {"type": "string"},
            "threat": {"type": "string"},
        },
        "required": ["goal", "fear", "obligation", "gain", "pressure", "threat"],
    }


def _interview_answers_schema(*, n: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answers": {
                "type": "array",
                "minItems": int(n),
                "maxItems": int(n),
                "items": {"type": "string"},
            }
        },
        "required": ["answers"],
    }


def _social_graph_schema(*, max_links: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "links": {
                "type": "array",
                "maxItems": int(max_links),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "relation": {"type": "string"},
                        "relevance": {"type": "string"},
                        "persona_hint": {"type": "string"},
                        "internal": {"type": "boolean"},
                        "capabilities": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "name",
                        "relation",
                        "relevance",
                        "persona_hint",
                        "internal",
                        "capabilities",
                    ],
                },
            }
        },
        "required": ["links"],
    }


@dataclass(slots=True)
class SocialGraphExtractor:
    """LLM-извлекатель социального графа из биографии и интервью агента."""

    llm: "LLMCaller"
    temperature: float = 0.0

    async def extract(
        self,
        *,
        agent_id: str,
        agent_name: str,
        persona: PersonaArtifact,
        existing_agent_names: list[str],
        max_links: int,
        scenario_description: str,
        language: str,
    ) -> list[SocialLink]:
        """Выделить значимых людей из биографии и интервью агента."""

        if max_links <= 0:
            return []

        biography = (persona.biography or "").strip()
        interview_text = persona.interview_as_text()
        if not biography and not interview_text:
            return []

        interview_excerpt = "\n\n".join(chunk_text(interview_text, max_chars=3200)[:2])
        system = render_prompt("persona.social_graph.system", language_repr=repr(language))
        user = render_prompt(
            "persona.social_graph.user",
            scenario_description=scenario_description,
            agent_id=agent_id,
            agent_name=agent_name,
            existing_agent_names="\n- ".join(existing_agent_names or ["(нет)"]),
            biography=biography or "(пусто)",
            interview_excerpt=interview_excerpt or "(пусто)",
            max_links=max_links,
        )
        resp = await self.llm.generate_structured(
            role="social_graph",
            name=agent_id,
            tick=0,
            system=system,
            user=user,
            schema=_social_graph_schema(max_links=max_links),
            temperature=self.temperature,
        )
        raw_links = resp.data.get("links") if isinstance(resp.data, dict) else None
        if not isinstance(raw_links, list):
            return []

        links: list[SocialLink] = []
        for item in raw_links:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            relation = str(item.get("relation") or "").strip()
            persona_hint = str(item.get("persona_hint") or "").strip()
            if not name or not relation or not persona_hint:
                continue
            links.append(
                SocialLink(
                    name=name,
                    relation=relation,
                    relevance=str(item.get("relevance") or "").strip(),
                    persona_hint=persona_hint,
                    internal=bool(item.get("internal", False)),
                    capabilities=[str(x) for x in list(item.get("capabilities") or []) if str(x).strip()],
                )
            )
        return links


@dataclass(slots=True)
class PersonaGenerator:
    """LLM-генератор артефактов персоны."""

    llm: "LLMCaller"
    temperature: float = 0.0

    async def generate_core(
        self,
        *,
        agent_id: str,
        name: str,
        internal: bool,
        persona_hint: str,
        scenario_description: str,
        language: str,
    ) -> PersonaArtifact:
        """Сгенерировать только summary+biography с фолбэками."""
        artifact: PersonaArtifact | None = None
        try:
            artifact = await self._generate_core(
                agent_id=agent_id,
                name=name,
                internal=internal,
                persona_hint=persona_hint,
                scenario_description=scenario_description,
                language=language,
            )
        except Exception:
            artifact = None

        summary = (artifact.summary if artifact else "").strip()
        biography = (artifact.biography if artifact else "").strip()
        if not summary:
            summary = (persona_hint or name).strip()
        if not biography:
            biography = summary
        return PersonaArtifact(summary=summary, biography=biography, interview=[])

    async def _generate_core(
        self,
        *,
        agent_id: str,
        name: str,
        internal: bool,
        persona_hint: str,
        scenario_description: str,
        language: str,
    ) -> PersonaArtifact:
        system = render_prompt("persona.core.system", language_repr=repr(language))
        user = render_prompt(
            "persona.core.user",
            scenario_description=scenario_description,
            agent_id=agent_id,
            name=name,
            internal=internal,
            persona_hint=persona_hint,
        )
        resp = await self.llm.generate_structured(
            role="persona",
            name=agent_id,
            tick=0,
            system=system,
            user=user,
            schema=_persona_core_schema(),
            temperature=self.temperature,
        )
        return PersonaArtifact.model_validate(resp.data)

    async def _generate_interview_answers(
        self,
        *,
        agent_id: str,
        language: str,
        persona_summary: str,
        biography_excerpt: str,
        questions: list[str],
        max_chunk: int = 10,
        max_structured_calls: int = 12,
    ) -> list[str]:
        structured_calls = 0

        async def _answer_one(q: str) -> str:
            user = render_prompt(
                "persona.interview.single.user",
                language_repr=repr(language),
                persona_summary=persona_summary,
                biography_excerpt=biography_excerpt,
                question=q,
            )
            try:
                resp = await self.llm.generate(
                    role="persona_interview",
                    name=agent_id,
                    tick=0,
                    system=render_prompt("persona.interview.single.system"),
                    user=user,
                    temperature=self.temperature,
                )
                return (resp.text or "").strip()
            except Exception:
                return ""

        async def _try_batch(qs: list[str]) -> list[str]:
            nonlocal structured_calls
            q_lines = "\n".join(f"{i+1}. {q}" for i, q in enumerate(qs))
            system = render_prompt("persona.interview.batch.system", language_repr=repr(language))
            user = render_prompt(
                "persona.interview.batch.user",
                persona_summary=persona_summary,
                biography_excerpt=biography_excerpt,
                questions=q_lines,
            )
            structured_calls += 1
            resp = await self.llm.generate_structured(
                role="persona_interview",
                name=agent_id,
                tick=0,
                system=system,
                user=user,
                schema=_interview_answers_schema(n=len(qs)),
                temperature=self.temperature,
            )
            data = resp.data
            answers = data.get("answers") if isinstance(data, dict) else None
            if not isinstance(answers, list) or len(answers) != len(qs):
                raise ValueError("invalid interview answers")
            cleaned = [(a or "").strip() for a in answers]
            if any(not a for a in cleaned):
                raise ValueError("empty interview answer")
            return cleaned

        out: list[str] = []
        queue: list[list[str]] = []
        size = max(1, int(max_chunk))
        for i in range(0, len(questions), size):
            queue.append(questions[i : i + size])

        while queue:
            qs = queue.pop(0)
            if structured_calls >= max_structured_calls:
                out.extend([await _answer_one(q) for q in qs])
                continue
            try:
                out.extend(await _try_batch(qs))
                continue
            except Exception:
                pass

            if len(qs) <= 1:
                out.append(await _answer_one(qs[0] if qs else ""))
                continue

            mid = max(1, len(qs) // 2)
            queue.insert(0, qs[mid:])
            queue.insert(0, qs[:mid])

        return out

    async def _generate_reflections(
        self,
        *,
        agent_id: str,
        language: str,
        summary: str,
        biography_excerpt: str,
        interview: list[InterviewQA],
    ) -> list[ExpertReflection]:
        interview_excerpt = "\n\n".join(
            f"{idx + 1}. Q: {qa.question}\nA: {qa.answer}"
            for idx, qa in enumerate(interview[:8])
            if (qa.question or "").strip() and (qa.answer or "").strip()
        )
        system = render_prompt("persona.reflection.system", language_repr=repr(language))
        user = render_prompt(
            "persona.reflection.user",
            summary=summary,
            biography_excerpt=biography_excerpt,
            interview_excerpt=interview_excerpt or "(пусто)",
        )
        try:
            resp = await self.llm.generate_structured(
                role="persona_reflection",
                name=agent_id,
                tick=0,
                system=system,
                user=user,
                schema=_reflection_schema(),
                temperature=self.temperature,
            )
            raw = resp.data.get("reflections") if isinstance(resp.data, dict) else None
            if isinstance(raw, list):
                out: list[ExpertReflection] = []
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    expert = str(item.get("expert") or "").strip()
                    summary_text = str(item.get("summary") or "").strip()
                    if not expert or not summary_text:
                        continue
                    indices = [
                        int(idx)
                        for idx in list(item.get("evidence_indices") or [])
                        if isinstance(idx, int) and idx >= 0
                    ]
                    out.append(
                        ExpertReflection(
                            expert=expert,
                            summary=summary_text,
                            evidence_indices=indices,
                        )
                    )
                if len(out) >= 2:
                    return out[:4]
        except Exception:
            pass

        fallback_excerpt = (summary or biography_excerpt or "").strip()
        if not fallback_excerpt:
            return []
        return [
            ExpertReflection(
                expert="psychologist",
                summary=f"Стабильный поведенческий якорь: {fallback_excerpt[:220].strip()}",
                evidence_indices=[0],
            ),
            ExpertReflection(
                expert="economist",
                summary=f"Практический расчёт и стимулы: {fallback_excerpt[:220].strip()}",
                evidence_indices=[0],
            ),
        ]

    async def _generate_motivation(
        self,
        *,
        agent_id: str,
        language: str,
        summary: str,
        biography_excerpt: str,
        interview: list[InterviewQA],
        reflections: list[ExpertReflection],
    ) -> MotivationDigest:
        """Собрать interview-grounded мотивационный digest."""

        interview_excerpt = "\n\n".join(
            f"Q: {(qa.question or '').strip()}\nA: {(qa.answer or '').strip()}"
            for qa in interview[:6]
            if (qa.question or "").strip() and (qa.answer or "").strip()
        )
        reflections_excerpt = "\n".join(
            f"{(item.expert or '').strip()}: {(item.summary or '').strip()}"
            for item in reflections[:4]
            if (item.expert or "").strip() and (item.summary or "").strip()
        )
        system = render_prompt("persona.motivation.system", language_repr=repr(language))
        user = render_prompt(
            "persona.motivation.user",
            summary=summary or "(пусто)",
            biography_excerpt=biography_excerpt or "(пусто)",
            interview_excerpt=interview_excerpt or "(пусто)",
            reflections_excerpt=reflections_excerpt or "(пусто)",
        )
        try:
            resp = await self.llm.generate_structured(
                role="persona_motivation",
                name=agent_id,
                tick=0,
                system=system,
                user=user,
                schema=_motivation_schema(),
                temperature=self.temperature,
            )
            digest = MotivationDigest.model_validate(resp.data)
            if digest.has_content():
                return digest
        except Exception:
            pass

        fallback_excerpt = (summary or biography_excerpt or "").strip()
        head = fallback_excerpt[:220].strip() or "Сохранять контроль над ситуацией."
        return MotivationDigest(
            goal=head,
            fear="Потерять влияние, доверие или контроль над развитием ситуации.",
            obligation="Сохранять обязательства, вытекающие из текущей роли и личных связей.",
            gain=head,
            pressure=head,
            threat="Ошибка в выборе, потеря репутации, внешний шум или чужая инициатива.",
        )

    async def generate(
        self,
        *,
        agent_id: str,
        name: str,
        internal: bool,
        persona_hint: str,
        scenario_description: str,
        language: str,
    ) -> PersonaArtifact:
        questions = "\n".join(f"{i+1}. {q}" for i, q in enumerate(INTERVIEW_QUESTIONS_V2))
        system = render_prompt("persona.full.system", language_repr=repr(language))
        user = render_prompt(
            "persona.full.user",
            scenario_description=scenario_description,
            agent_id=agent_id,
            name=name,
            internal=internal,
            persona_hint=persona_hint,
            questions=questions,
        )
        artifact: PersonaArtifact | None = None
        try:
            resp = await self.llm.generate_structured(
                role="persona",
                name=agent_id,
                tick=0,
                system=system,
                user=user,
                schema=_persona_schema(),
                temperature=self.temperature,
            )
            artifact = PersonaArtifact.model_validate(resp.data)
        except Exception:
            artifact = None

        summary = (artifact.summary if artifact else "").strip()
        biography = (artifact.biography if artifact else "").strip()

        if not summary or not biography:
            try:
                core = await self._generate_core(
                    agent_id=agent_id,
                    name=name,
                    internal=internal,
                    persona_hint=persona_hint,
                    scenario_description=scenario_description,
                    language=language,
                )
                summary = (core.summary or summary).strip()
                biography = (core.biography or biography).strip()
            except Exception:
                pass

        if not summary:
            summary = (persona_hint or f"{name}").strip()
        if not biography:
            biography = (persona_hint or summary).strip()

        answers: list[str] = []
        reflections: list[ExpertReflection] = []
        motivation: MotivationDigest | None = None
        if artifact and len(artifact.interview) >= len(INTERVIEW_QUESTIONS_V2):
            answers = [
                (qa.answer or "").strip()
                for qa in artifact.interview[: len(INTERVIEW_QUESTIONS_V2)]
            ]
            if any(not a for a in answers):
                answers = []
            reflections = [
                item
                for item in list(artifact.reflections or [])
                if (item.expert or "").strip() and (item.summary or "").strip()
            ]
            if artifact.motivation is not None and artifact.motivation.has_content():
                motivation = artifact.motivation

        if not answers:
            excerpt = biography[:1600].strip()
            answers = await self._generate_interview_answers(
                agent_id=agent_id,
                language=language,
                persona_summary=summary,
                biography_excerpt=excerpt,
                questions=list(INTERVIEW_QUESTIONS_V2),
            )

        interview: list[InterviewQA] = []
        for q, a in zip(INTERVIEW_QUESTIONS_V2, answers, strict=False):
            a = (a or "").strip()
            if not a:
                continue
            interview.append(InterviewQA(question=q, answer=a))

        if len(reflections) < 2:
            reflections = await self._generate_reflections(
                agent_id=agent_id,
                language=language,
                summary=summary,
                biography_excerpt=biography[:1600].strip(),
                interview=interview,
            )
        if motivation is None or not motivation.has_content():
            motivation = await self._generate_motivation(
                agent_id=agent_id,
                language=language,
                summary=summary,
                biography_excerpt=biography[:1600].strip(),
                interview=interview,
                reflections=reflections,
            )

        return PersonaArtifact(
            summary=summary,
            biography=biography,
            interview=interview,
            reflections=reflections,
            motivation=motivation,
        )
