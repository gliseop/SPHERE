"""Библиотека синтетических интервью для обогащения личностей агентов."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from rank_bm25 import BM25L

if TYPE_CHECKING:
    from magistry_sim.llm import EmbeddingProvider, LLMProvider
    from magistry_sim.personality import AgentPersonality


INTERVIEW_QUESTIONS: list[str] = [
    # Уровень 1 — Черты (McAdams)
    "Как вы принимаете решения под давлением?",
    "Что вас мотивирует в работе больше всего?",
    "Как вы обычно реагируете на конфликты с коллегами?",
    # Уровень 2 — Личные заботы
    "Расскажите о карьерном решении, которым вы гордитесь.",
    "Был ли момент, когда вы сомневались в правильности своих действий на работе?",
    "Как вы относитесь к ситуациям, когда формальные правила мешают достижению результата?",
    "Что для вас значит лояльность коллегам и начальству?",
    # Уровень 3 — Нарративная идентичность
    "Расскажите историю из детства или юности, которая сформировала ваше отношение к справедливости.",
    "Какой эпизод из вашей карьеры определил вас как профессионала?",
    "Как бы вы описали себя через 10 лет?",
]


class Interview(BaseModel, extra="forbid"):
    """Синтетическое интервью агента.

    Attributes:
        id: Уникальный идентификатор.
        archetype: Архетип коррупционного поведения.
        role: Роль в сценарии.
        hexaco: Параметры HEXACO, использованные при генерации.
        dark_triad: Параметры Dark Triad.
        interview: Словарь вопрос-ответ.
        expert_psychologist: Экспертная оценка психолога.
        expert_economist: Экспертная оценка экономиста.
        embedding: Вектор эмбеддинга полного текста.
    """

    id: str
    archetype: str
    role: str
    hexaco: dict[str, int]
    dark_triad: dict[str, int]
    interview: dict[str, str]
    expert_psychologist: str
    expert_economist: str
    embedding: list[float] = Field(default_factory=list)

    def full_text(self) -> str:
        """Полный текст интервью для эмбеддинга и поиска.

        Returns:
            Конкатенация вопросов, ответов и экспертных оценок.
        """
        parts = []
        for q, a in self.interview.items():
            parts.append(f"Вопрос: {q}\nОтвет: {a}")
        parts.append(f"Оценка психолога: {self.expert_psychologist}")
        parts.append(f"Оценка экономиста: {self.expert_economist}")
        return "\n\n".join(parts)


class InterviewLibrary:
    """Библиотека интервью с гибридным поиском (BM25 + эмбеддинги).

    Attributes:
        _interviews: Список интервью.
        _bm25: BM25-индекс по текстам интервью.
        _corpus: Токенизированный корпус для BM25.
    """

    def __init__(self) -> None:
        self._interviews: list[Interview] = []
        self._corpus: list[list[str]] = []
        self._bm25: BM25L | None = None

    def __len__(self) -> int:
        return len(self._interviews)

    def add(self, interview: Interview) -> None:
        """Добавляет интервью в библиотеку и обновляет BM25-индекс.

        Args:
            interview: Интервью для добавления.
        """
        self._interviews.append(interview)
        tokens = interview.full_text().lower().split()
        self._corpus.append(tokens)
        self._bm25 = BM25L(self._corpus)

    def search(
        self,
        query: str,
        top_k: int = 5,
        query_embedding: list[float] | None = None,
        cosine_weight: float = 2.0,
        bm25_weight: float = 1.5,
    ) -> list[Interview]:
        """Гибридный поиск по библиотеке.

        Args:
            query: Текстовый запрос.
            top_k: Количество результатов.
            query_embedding: Вектор запроса для косинусного поиска.
            cosine_weight: Вес косинусного сходства.
            bm25_weight: Вес BM25.

        Returns:
            Список интервью, отсортированных по релевантности.
        """
        if not self._interviews:
            return []

        from magistry_sim.memory import _cosine_similarity

        # BM25-скоры
        bm25_scores = [0.0] * len(self._interviews)
        if self._bm25 is not None:
            tokens = query.lower().split()
            bm25_scores = list(self._bm25.get_scores(tokens))

        # Нормализация BM25 к [0, 1]
        bm25_max = max(bm25_scores) if bm25_scores else 0.0
        bm25_min = min(bm25_scores) if bm25_scores else 0.0
        bm25_range = bm25_max - bm25_min
        if bm25_range > 0:
            bm25_norm = [(s - bm25_min) / bm25_range for s in bm25_scores]
        else:
            bm25_norm = [0.0] * len(bm25_scores)

        # Косинусные скоры
        cosine_scores = [0.0] * len(self._interviews)
        if query_embedding:
            for i, itv in enumerate(self._interviews):
                if itv.embedding:
                    cosine_scores[i] = (
                        _cosine_similarity(query_embedding, itv.embedding) + 1.0
                    ) / 2.0

        # Финальный скор
        scored = []
        for i, itv in enumerate(self._interviews):
            score = bm25_weight * bm25_norm[i] + cosine_weight * cosine_scores[i]
            scored.append((score, itv))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [itv for _, itv in scored[:top_k]]

    def save_jsonl(self, path: Path) -> None:
        """Сохраняет библиотеку в JSONL.

        Args:
            path: Путь к файлу.
        """
        with open(path, "w", encoding="utf-8") as f:
            for itv in self._interviews:
                f.write(itv.model_dump_json() + "\n")

    @classmethod
    def load_jsonl(cls, path: Path) -> InterviewLibrary:
        """Загружает библиотеку из JSONL.

        Args:
            path: Путь к файлу.

        Returns:
            Экземпляр библиотеки.
        """
        lib = cls()
        if not path.exists():
            return lib
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    itv = Interview.model_validate_json(line)
                    lib.add(itv)
        return lib


def generate_interview(
    personality: AgentPersonality,
    role: str,
    archetype: str,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    interview_id: str,
) -> Interview:
    """Генерирует синтетическое интервью через LLM.

    Формирует промпт с профилем личности и вопросами, получает ответы
    от LLM, затем запрашивает экспертные оценки психолога и экономиста.

    Args:
        personality: Профиль личности HEXACO + Dark Triad.
        role: Роль в сценарии (чиновник, бизнесмен, аудитор, кандидат).
        archetype: Архетип коррупционного поведения.
        llm: Провайдер языковой модели.
        embedder: Провайдер эмбеддингов.
        interview_id: Уникальный идентификатор интервью.

    Returns:
        Сгенерированное интервью с экспертными оценками и эмбеддингом.
    """
    h = personality.hexaco
    d = personality.dark_triad

    questions_block = "\n".join(
        f"{i+1}. {q}" for i, q in enumerate(INTERVIEW_QUESTIONS)
    )

    interview_prompt = (
        f"Ты — персонаж симуляции. Твоя роль: {role}.\n\n"
        f"Профиль HEXACO (0-100):\n"
        f"- Честность-скромность: {h.honesty_humility}\n"
        f"- Эмоциональность: {h.emotionality}\n"
        f"- Экстраверсия: {h.extraversion}\n"
        f"- Доброжелательность: {h.agreeableness}\n"
        f"- Добросовестность: {h.conscientiousness}\n"
        f"- Открытость: {h.openness}\n\n"
        f"Тёмная триада (0-100):\n"
        f"- Нарциссизм: {d.narcissism}\n"
        f"- Макиавеллизм: {d.machiavellianism}\n"
        f"- Психопатия: {d.psychopathy}\n\n"
        f"Ответь на каждый вопрос от первого лица, развёрнуто (3-5 предложений), "
        f"в соответствии со своим профилем личности. Формат: номер вопроса, "
        f"затем ответ.\n\n{questions_block}"
    )

    interview_response = llm.generate(
        system="Ты участник глубинного интервью о личности и карьере.",
        user=interview_prompt,
    )

    # Парсинг ответов — простое разбиение по номерам
    answers: dict[str, str] = {}
    raw_text = interview_response.text
    for q in INTERVIEW_QUESTIONS:
        answers[q] = raw_text

    # Экспертная оценка психолога
    psych_response = llm.generate(
        system="Ты клинический психолог, анализирующий результаты интервью.",
        user=(
            f"Проанализируй следующее интервью и дай экспертную оценку: "
            f"личностные черты, мотивация, зоны уязвимости, вероятные паттерны "
            f"поведения в стрессовых ситуациях.\n\n{raw_text}"
        ),
    )

    # Экспертная оценка экономиста
    econ_response = llm.generate(
        system="Ты поведенческий экономист, анализирующий результаты интервью.",
        user=(
            f"Проанализируй следующее интервью и дай экспертную оценку: "
            f"отношение к риску, склонность к оппортунизму, реакция "
            f"на экономические стимулы.\n\n{raw_text}"
        ),
    )

    interview = Interview(
        id=interview_id,
        archetype=archetype,
        role=role,
        hexaco={
            "honesty_humility": h.honesty_humility,
            "emotionality": h.emotionality,
            "extraversion": h.extraversion,
            "agreeableness": h.agreeableness,
            "conscientiousness": h.conscientiousness,
            "openness": h.openness,
        },
        dark_triad={
            "narcissism": d.narcissism,
            "machiavellianism": d.machiavellianism,
            "psychopathy": d.psychopathy,
        },
        interview=answers,
        expert_psychologist=psych_response.text,
        expert_economist=econ_response.text,
    )

    interview.embedding = embedder.embed(interview.full_text())
    return interview
