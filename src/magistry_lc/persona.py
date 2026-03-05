"""Персоны для MAGISTRY-LC (PersonaLibrary / PersonaGenerator).

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


class PersonaArtifact(BaseModel):
    """Полный артефакт персоны."""

    model_config = ConfigDict(extra="forbid")

    persona_id: str | None = None
    summary: str = ""
    biography: str = ""
    interview: list[InterviewQA] = Field(default_factory=list)

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

    key = _NAME_KEY_RE.sub("", (name or "").casefold()).strip("_")
    return key


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
                    "Для PersonaLibrary YAML установите зависимости: pip install magistry-sim[lc]"
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
        system = (
            "Ты — модуль извлечения социального графа для симуляции организационных процессов.\n"
            "Выдели только людей, которые реально важны для сюжета и решений агента.\n"
            "Не придумывай новых организаций, должностей или ID. Возвращай строго JSON по схеме.\n"
            f"Пиши на языке: {language!r}.\n"
        )
        user = (
            f"Сценарий:\n{scenario_description}\n\n"
            f"Агент:\n- id: {agent_id}\n- name: {agent_name}\n\n"
            f"Уже существующие агенты:\n- " + "\n- ".join(existing_agent_names or ["(нет)"]) + "\n\n"
            f"Биография:\n{biography or '(пусто)'}\n\n"
            f"Интервью (фрагмент):\n{interview_excerpt or '(пусто)'}\n\n"
            f"Выдели до {max_links} людей. Для каждого укажи имя, связь, почему важен, краткий persona_hint, "
            "является ли он внутренним участником процесса, и рекомендуемые capabilities."
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
        system = (
            "Ты — генератор персоны агента для симуляции организационных процессов (MAGISTRY-LC).\n"
            "Сгенерируй:\n"
            "- краткую сводку (summary) 3–6 предложений;\n"
            "- биографию (biography) 1000–2000 слов.\n"
            "Важно:\n"
            "- Не используй числовые параметры личности (greed/fear/honesty/etc). Только текст.\n"
            "- Не выдумывай новых ID/сущностей мира; описывай человека и мотивации.\n"
            f"- Пиши на языке: {language!r}.\n"
            "Ответ: строго JSON по схеме.\n"
        )
        user = (
            f"Сценарий:\n{scenario_description}\n\n"
            f"Агент:\n- agent_id: {agent_id}\n- name: {name}\n- internal: {internal}\n\n"
            f"Подсказка/черновик персоны:\n{persona_hint}\n"
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
            user = (
                "Ответь на вопрос интервью персоны. 2–6 предложений.\n"
                f"Язык: {language!r}\n\n"
                f"Persona summary:\n{persona_summary}\n\n"
                f"Biography excerpt:\n{biography_excerpt}\n\n"
                f"Вопрос:\n{q}\n"
            )
            try:
                resp = await self.llm.generate(
                    role="persona_interview",
                    name=agent_id,
                    tick=0,
                    system="Ты — генератор интервью персоны.",
                    user=user,
                    temperature=self.temperature,
                )
                return (resp.text or "").strip()
            except Exception:
                return ""

        async def _try_batch(qs: list[str]) -> list[str]:
            nonlocal structured_calls
            q_lines = "\n".join(f"{i+1}. {q}" for i, q in enumerate(qs))
            system = (
                "Ты — генератор интервью персоны (MAGISTRY-LC).\n"
                "Дай ответы на вопросы, каждый ответ 2–6 предложений.\n"
                f"Пиши на языке: {language!r}.\n"
                "Ответ: строго JSON по схеме.\n"
            )
            user = (
                f"Persona summary:\n{persona_summary}\n\n"
                f"Biography excerpt:\n{biography_excerpt}\n\n"
                f"Вопросы:\n{q_lines}\n"
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
        system = (
            "Ты — генератор персоны агента для симуляции организационных процессов (MAGISTRY-LC).\n"
            "Сгенерируй:\n"
            "- краткую сводку (summary) 3–6 предложений;\n"
            "- биографию (biography) 1000–2000 слов;\n"
            "- интервью: ответы на вопросы (30), каждый ответ 2–6 предложений.\n"
            "Важно:\n"
            "- Не используй числовые параметры личности (greed/fear/honesty/etc). Только текст.\n"
            "- Не выдумывай новых ID/сущностей мира; описывай человека и мотивации.\n"
            f"- Пиши на языке: {language!r}.\n"
            "Ответ: строго JSON по схеме.\n"
        )
        user = (
            f"Сценарий:\n{scenario_description}\n\n"
            f"Агент:\n- agent_id: {agent_id}\n- name: {name}\n- internal: {internal}\n\n"
            f"Подсказка/черновик персоны:\n{persona_hint}\n\n"
            f"Вопросы интервью:\n{questions}\n"
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
        if artifact and len(artifact.interview) >= len(INTERVIEW_QUESTIONS_V2):
            answers = [
                (qa.answer or "").strip()
                for qa in artifact.interview[: len(INTERVIEW_QUESTIONS_V2)]
            ]
            if any(not a for a in answers):
                answers = []

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

        return PersonaArtifact(summary=summary, biography=biography, interview=interview)
