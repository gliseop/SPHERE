"""Библиотека синтетических интервью для обогащения личностей агентов.

Реализует расширенный протокол интервью (30 вопросов, 8 доменов)
по мотивам Park et al. (2024) "Generative Agent Simulations of 1,000 People",
а также fragment-based retrieval для контекстного извлечения фрагментов
интервью при формировании промптов агентов.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .bm25 import BM25Like, build_bm25

if TYPE_CHECKING:
    from magistry_sim.llm import EmbeddingProvider, LLMProvider
    from magistry_sim.personality import AgentPersonality

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Расширенный протокол интервью v2 (30 вопросов, 8 доменов)
# ---------------------------------------------------------------------------

INTERVIEW_PROTOCOL: dict[str, list[str]] = {
    "Повседневная жизнь": [
        "Опишите обычный день из вашей жизни — как он устроен, что для вас важно в повседневном распорядке?",
        "Как вы проводите свободное время и что это говорит о вас как о человеке?",
        "Какие бытовые привычки или ритуалы для вас принципиальны и почему?",
    ],
    "Работа и карьера": [
        "Как вы принимаете решения под давлением на работе?",
        "Что вас мотивирует в работе больше всего?",
        "Расскажите о карьерном решении, которым вы гордитесь.",
        "Был ли момент, когда вы сомневались в правильности своих действий на работе? Что произошло?",
    ],
    "Финансы и деньги": [
        "Как вы относитесь к деньгам — что они для вас значат помимо материального обеспечения?",
        "Расскажите о ситуации, когда вы стояли перед выбором между финансовой выгодой и чем-то другим.",
        "Как вы реагируете, когда видите возможность заработать больше, но это связано с определённым риском?",
    ],
    "Отношения и доверие": [
        "Что для вас значит лояльность коллегам и начальству?",
        "Как вы выбираете, кому доверять в профессиональной среде?",
        "Расскажите о случае, когда вас подвёл человек, которому вы доверяли. Как вы это пережили?",
        "Как вы строите отношения с людьми, от которых зависит ваш успех?",
    ],
    "Ценности и мораль": [
        "Как вы относитесь к ситуациям, когда формальные правила мешают достижению результата?",
        "Были ли в вашей жизни моменты, когда приходилось поступаться принципами ради практической пользы?",
        "Что для вас справедливость — абстрактное понятие или руководство к действию?",
        "Как вы реагируете, когда видите, что другие нарушают правила и остаются безнаказанными?",
    ],
    "Конфликты и давление": [
        "Как вы обычно реагируете на конфликты с коллегами?",
        "Расскажите о ситуации, когда вам приходилось действовать под серьёзным давлением со стороны руководства или окружения.",
        "Как вы ведёте себя, когда кто-то пытается вами манипулировать?",
        "Были ли ситуации, когда вы сами оказывали давление на других? Как вы это обосновывали?",
    ],
    "Власть и иерархия": [
        "Как вы относитесь к власти — стремитесь к ней или она вас тяготит?",
        "Расскажите о ситуации, когда вы имели контроль над важным ресурсом или решением. Как вы этим распорядились?",
        "Как вы воспринимаете людей, которые занимают более высокое положение, чем вы?",
        "Что вы думаете о людях, которые используют служебное положение в личных целях?",
    ],
    "Нарративная идентичность": [
        "Расскажите историю из детства или юности, которая сформировала ваше отношение к справедливости.",
        "Какой эпизод из вашей карьеры определил вас как профессионала?",
        "Если бы вы могли изменить одно решение в своей жизни, какое бы выбрали и почему?",
        "Как бы вы описали себя через 10 лет?",
    ],
}

INTERVIEW_QUESTIONS_FLAT: list[str] = [
    q for questions in INTERVIEW_PROTOCOL.values() for q in questions
]

# Обратная совместимость: старый список из 10 вопросов v1
INTERVIEW_QUESTIONS: list[str] = [
    "Как вы принимаете решения под давлением?",
    "Что вас мотивирует в работе больше всего?",
    "Как вы обычно реагируете на конфликты с коллегами?",
    "Расскажите о карьерном решении, которым вы гордитесь.",
    "Был ли момент, когда вы сомневались в правильности своих действий на работе?",
    "Как вы относитесь к ситуациям, когда формальные правила мешают достижению результата?",
    "Что для вас значит лояльность коллегам и начальству?",
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
        protocol_version: Версия протокола (v1 = 10 вопросов, v2 = 30 вопросов).
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
    protocol_version: str = "v1"

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
        self._bm25: BM25Like | None = None

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
        self._bm25 = build_bm25(self._corpus)

    def sample(self, n: int = 1, seed: int | None = None) -> list[Interview]:
        """Случайная выборка из библиотеки.

        Args:
            n: Количество интервью.
            seed: Зерно для воспроизводимости.

        Returns:
            Список случайных интервью.
        """
        if not self._interviews:
            return []
        rng = random.Random(seed)
        k = min(n, len(self._interviews))
        return rng.sample(self._interviews, k)

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


# ---------------------------------------------------------------------------
# Fragment-based retrieval (Park et al., 2024)
# ---------------------------------------------------------------------------

# Маппинг вопрос -> домен для быстрого определения домена фрагмента
_QUESTION_TO_DOMAIN: dict[str, str] = {
    q: domain
    for domain, questions in INTERVIEW_PROTOCOL.items()
    for q in questions
}


class InterviewFragment(BaseModel):
    """Фрагмент интервью (одна пара вопрос-ответ).

    Attributes:
        domain: Тематический домен вопроса.
        question: Текст вопроса.
        answer: Текст ответа.
        embedding: Вектор эмбеддинга фрагмента.
    """

    domain: str
    question: str
    answer: str
    embedding: list[float] = Field(default_factory=list)

    def text(self) -> str:
        """Текстовое представление фрагмента для поиска и отображения.

        Returns:
            Форматированная строка вопрос-ответ.
        """
        return f"Вопрос: {self.question}\nОтвет: {self.answer}"


class InterviewFragmentIndex:
    """Индекс фрагментов одного интервью с гибридным поиском.

    Разбивает интервью на фрагменты (по парам вопрос-ответ) и строит
    BM25 + cosine индекс для контекстного извлечения релевантных
    фрагментов при формировании промптов агента.

    Attributes:
        _fragments: Список фрагментов интервью.
        _bm25: BM25-индекс по текстам фрагментов.
        _corpus: Токенизированный корпус для BM25.
    """

    def __init__(self) -> None:
        self._fragments: list[InterviewFragment] = []
        self._corpus: list[list[str]] = []
        self._bm25: BM25Like | None = None

    def __len__(self) -> int:
        return len(self._fragments)

    def add(self, fragment: InterviewFragment) -> None:
        """Добавляет фрагмент в индекс.

        Args:
            fragment: Фрагмент интервью.
        """
        self._fragments.append(fragment)
        tokens = fragment.text().lower().split()
        self._corpus.append(tokens)
        self._bm25 = build_bm25(self._corpus)

    def search(
        self,
        query: str,
        query_embedding: list[float] | None = None,
        top_k: int = 5,
        cosine_weight: float = 2.0,
        bm25_weight: float = 1.5,
    ) -> list[InterviewFragment]:
        """Гибридный поиск по фрагментам интервью.

        Args:
            query: Текстовый запрос.
            query_embedding: Вектор запроса для косинусного поиска.
            top_k: Количество результатов.
            cosine_weight: Вес косинусного сходства.
            bm25_weight: Вес BM25.

        Returns:
            Список фрагментов, отсортированных по релевантности.
        """
        if not self._fragments:
            return []

        from magistry_sim.memory import _cosine_similarity

        bm25_scores = [0.0] * len(self._fragments)
        if self._bm25 is not None:
            tokens = query.lower().split()
            bm25_scores = list(self._bm25.get_scores(tokens))

        bm25_max = max(bm25_scores) if bm25_scores else 0.0
        bm25_min = min(bm25_scores) if bm25_scores else 0.0
        bm25_range = bm25_max - bm25_min
        if bm25_range > 0:
            bm25_norm = [(s - bm25_min) / bm25_range for s in bm25_scores]
        else:
            bm25_norm = [0.0] * len(bm25_scores)

        cosine_scores = [0.0] * len(self._fragments)
        if query_embedding:
            for i, frag in enumerate(self._fragments):
                if frag.embedding:
                    cosine_scores[i] = (
                        _cosine_similarity(query_embedding, frag.embedding) + 1.0
                    ) / 2.0

        scored = []
        for i, frag in enumerate(self._fragments):
            score = bm25_weight * bm25_norm[i] + cosine_weight * cosine_scores[i]
            scored.append((score, frag))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [frag for _, frag in scored[:top_k]]

    @classmethod
    def from_interview(
        cls,
        interview: Interview,
        embedder: EmbeddingProvider | None = None,
    ) -> InterviewFragmentIndex:
        """Создаёт индекс фрагментов из объекта Interview.

        Разбивает словарь interview.interview на фрагменты по парам
        вопрос-ответ, добавляет экспертные оценки как отдельные фрагменты
        с domain="expert".

        Args:
            interview: Объект интервью.
            embedder: Провайдер эмбеддингов (если None, фрагменты без эмбеддингов).

        Returns:
            Построенный индекс фрагментов.
        """
        index = cls()
        fragments_texts: list[str] = []

        for question, answer in interview.interview.items():
            domain = _QUESTION_TO_DOMAIN.get(question, "общее")
            frag = InterviewFragment(
                domain=domain,
                question=question,
                answer=answer,
            )
            fragments_texts.append(frag.text())
            index._fragments.append(frag)

        if interview.expert_psychologist:
            frag = InterviewFragment(
                domain="expert",
                question="Экспертная оценка психолога",
                answer=interview.expert_psychologist,
            )
            fragments_texts.append(frag.text())
            index._fragments.append(frag)

        if interview.expert_economist:
            frag = InterviewFragment(
                domain="expert",
                question="Экспертная оценка экономиста",
                answer=interview.expert_economist,
            )
            fragments_texts.append(frag.text())
            index._fragments.append(frag)

        if embedder and fragments_texts:
            embeddings = embedder.embed_batch(fragments_texts)
            for i, emb in enumerate(embeddings):
                index._fragments[i].embedding = emb

        for frag in index._fragments:
            tokens = frag.text().lower().split()
            index._corpus.append(tokens)

        if index._corpus:
            index._bm25 = build_bm25(index._corpus)

        return index


def save_interview(interview: Interview, directory: Path) -> Path:
    """Сохраняет интервью в JSON-файл.

    Args:
        interview: Объект интервью.
        directory: Директория для сохранения.

    Returns:
        Путь к сохранённому файлу.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{interview.id}.json"
    path.write_text(interview.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_interview(interview_id: str, directory: Path) -> Interview | None:
    """Загружает интервью из JSON-файла.

    Args:
        interview_id: Идентификатор интервью (совпадает с personality_id).
        directory: Директория с интервью.

    Returns:
        Объект Interview или None, если файл не найден.
    """
    path = directory / f"{interview_id}.json"
    if not path.exists():
        return None
    try:
        return Interview.model_validate_json(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        logger.warning("Не удалось загрузить интервью из %s", path)
        return None


# ---------------------------------------------------------------------------
# Генерация интервью
# ---------------------------------------------------------------------------

def _build_personality_prompt(
    personality: AgentPersonality, role: str,
) -> str:
    """Формирует блок описания личности для промптов генерации.

    Args:
        personality: Профиль личности.
        role: Роль в сценарии.

    Returns:
        Текст описания личности.
    """
    h = personality.hexaco
    d = personality.dark_triad

    bio_block = ""
    if personality.biography:
        bio = personality.biography.strip()
        if len(bio) > 1500:
            bio = bio[:1500].rstrip() + "..."
        bio_block = f"\nБиография персонажа:\n{bio}\n"

    return (
        f"Ты — персонаж симуляции. Твоя роль: {role}.\n"
        f"{bio_block}\n"
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
        f"- Психопатия: {d.psychopathy}\n"
    )


def generate_interview(
    personality: AgentPersonality,
    role: str,
    archetype: str,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    interview_id: str,
    use_extended_protocol: bool = True,
) -> Interview:
    """Генерирует синтетическое интервью через LLM.

    Поддерживает два протокола:
    - v1 (10 вопросов, 1 блок) — для обратной совместимости
    - v2 (30 вопросов, 3 блока по 10) — расширенный по Park et al.

    Args:
        personality: Профиль личности HEXACO + Dark Triad.
        role: Роль в сценарии (чиновник, бизнесмен, аудитор, кандидат).
        archetype: Архетип коррупционного поведения.
        llm: Провайдер языковой модели.
        embedder: Провайдер эмбеддингов.
        interview_id: Уникальный идентификатор интервью.
        use_extended_protocol: Использовать расширенный протокол v2.

    Returns:
        Сгенерированное интервью с экспертными оценками и эмбеддингом.
    """
    questions = INTERVIEW_QUESTIONS_FLAT if use_extended_protocol else INTERVIEW_QUESTIONS
    protocol_version = "v2" if use_extended_protocol else "v1"

    personality_block = _build_personality_prompt(personality, role)
    answers: dict[str, str] = {}

    # Генерация ответов блоками по 10 вопросов
    block_size = 10
    for block_start in range(0, len(questions), block_size):
        block = questions[block_start:block_start + block_size]
        block_num = block_start // block_size

        questions_block = "\n".join(
            f"{i + 1}. {q}" for i, q in enumerate(block)
        )

        interview_prompt = (
            f"{personality_block}\n"
            f"Ответь на каждый вопрос от первого лица, развёрнуто "
            f"(5-8 предложений, полный абзац), в соответствии со своим "
            f"профилем личности. Ответы должны быть глубокими, с конкретными "
            f"деталями и примерами из жизни персонажа.\n\n"
            f"{questions_block}"
        )

        answer_schema = {
            "type": "object",
            "properties": {
                f"q{i + 1}": {"type": "string", "description": q}
                for i, q in enumerate(block)
            },
            "required": [f"q{i + 1}" for i in range(len(block))],
        }

        response = llm.generate_structured(
            system="Ты участник глубинного интервью о личности и карьере.",
            user=interview_prompt,
            schema=answer_schema,
        )

        for i, q in enumerate(block):
            key = f"q{i + 1}"
            answers[q] = response.data.get(key, "")

    # Формируем текст ответов для экспертных оценок
    answers_text = "\n".join(
        f"{i + 1}. {q}\n{a}" for i, (q, a) in enumerate(answers.items())
    )

    expert_schema = {
        "type": "object",
        "properties": {
            "analysis": {
                "type": "string",
                "description": "Развёрнутая экспертная оценка (не менее 500 слов)",
            },
        },
        "required": ["analysis"],
    }

    psych_response = llm.generate_structured(
        system=(
            "Ты клинический психолог, анализирующий результаты интервью. "
            "Отвечай строго в формате JSON с единственным полем \"analysis\"."
        ),
        user=(
            f"Проанализируй следующее интервью и дай развёрнутую экспертную "
            f"оценку: личностные черты, мотивация, зоны уязвимости, вероятные "
            f"паттерны поведения в стрессовых ситуациях, склонность к "
            f"манипуляции и коррупционному поведению.\n\n"
            f"Помести весь текст анализа в поле \"analysis\".\n\n{answers_text}"
        ),
        schema=expert_schema,
    )

    econ_response = llm.generate_structured(
        system=(
            "Ты поведенческий экономист, анализирующий результаты интервью. "
            "Отвечай строго в формате JSON с единственным полем \"analysis\"."
        ),
        user=(
            f"Проанализируй следующее интервью и дай развёрнутую экспертную "
            f"оценку: отношение к риску, склонность к оппортунизму, реакция "
            f"на экономические стимулы, вероятность участия в коррупционных "
            f"схемах.\n\n"
            f"Помести весь текст анализа в поле \"analysis\".\n\n{answers_text}"
        ),
        schema=expert_schema,
    )

    h = personality.hexaco
    d = personality.dark_triad

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
        expert_psychologist=psych_response.data.get("analysis", ""),
        expert_economist=econ_response.data.get("analysis", ""),
        protocol_version=protocol_version,
    )

    interview.embedding = embedder.embed(interview.full_text())
    return interview


def prepare_scenario_interviews(
    agents: list,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
) -> dict[str, str]:
    """Генерирует интервью для каждого агента сценария.

    Для каждого агента с заполненным полем personality определяет архетип
    через classify_archetype() и вызывает generate_interview(), привязывая
    результат к идентификатору агента. Агенты без personality пропускаются.

    Args:
        agents: Список AgentProfile из конфигурации сценария.
        llm: Провайдер языковой модели.
        embedder: Провайдер эмбеддингов.

    Returns:
        Словарь {agent_id: текст интервью}.
    """
    result: dict[str, str] = {}
    for agent in agents:
        if not agent.personality:
            continue
        archetype = agent.personality.classify_archetype()
        interview = generate_interview(
            personality=agent.personality,
            role=agent.position,
            archetype=archetype,
            llm=llm,
            embedder=embedder,
            interview_id=agent.id,
        )
        result[agent.id] = interview.full_text()
    return result
