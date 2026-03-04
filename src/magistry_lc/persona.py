"""Персоны для MAGISTRY-LC (PersonaLibrary / PersonaGenerator).

Персона в greenfield-ветке — это не одна строка, а набор артефактов:
- краткая сводка (summary) для быстрого контекста;
- развернутая биография (biography) для долгосрочной памяти;
- интервью (30 вопросов) как устойчивые поведенческие якоря.
"""

from __future__ import annotations

import json
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


@dataclass(slots=True)
class PersonaGenerator:
    """LLM-генератор артефактов персоны."""

    llm: "LLMCaller"
    temperature: float = 0.0

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
        return artifact
