"""Модуль рефлексии по модели Park et al. (2023).

Запускается при накоплении достаточного объёма новых впечатлений.
Генерирует фокусные точки, извлекает релевантные воспоминания
и синтезирует высокоуровневые выводы.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from magistry_sim.memory import MemoryRecord, MemoryStream

if TYPE_CHECKING:
    from magistry_sim.llm import EmbeddingProvider, LLMProvider

REFLECTION_THRESHOLD = 50.0
FOCAL_POINTS_COUNT = 3
MEMORIES_PER_FOCAL = 20


def should_reflect(stream: MemoryStream) -> bool:
    """Проверяет, достигнут ли порог для запуска рефлексии.

    Args:
        stream: Поток памяти агента.

    Returns:
        True, если накопленная важность достигла порога.
    """
    return stream.importance_since_reflection >= REFLECTION_THRESHOLD


def generate_focal_points(
    stream: MemoryStream,
    llm: LLMProvider,
    n: int = FOCAL_POINTS_COUNT,
) -> list[str]:
    """Генерирует фокусные точки для рефлексии.

    Языковая модель получает последние записи и формулирует вопросы
    высокого уровня по трём плоскостям: оценка рисков, оценка выгоды,
    моральная рационализация.

    Args:
        stream: Поток памяти агента.
        llm: Провайдер языковой модели.
        n: Количество фокусных точек.

    Returns:
        Список вопросов высокого уровня.
    """
    recent = stream.get_recent(n=100)
    memories_text = "\n".join(
        f"- [{r.kind}, раунд {r.created_at}] {r.content}" for r in recent
    )
    prompt = (
        f"На основе следующих наблюдений агента {stream.agent_id}, "
        f"сформулируй {n} вопроса высокого уровня, о которых стоит "
        f"задуматься. Учитывай три плоскости: оценка рисков (могут ли "
        f"меня разоблачить?), оценка выгоды (стоит ли игра свеч?), "
        f"моральная рационализация (почему это допустимо?).\n\n"
        f"Наблюдения:\n{memories_text}\n\n"
        f"Верни JSON-массив из {n} строк-вопросов."
    )
    response = llm.generate(system="", user=prompt)
    try:
        points = json.loads(response.text)
        if isinstance(points, list):
            return [str(p) for p in points[:n]]
    except (json.JSONDecodeError, TypeError):
        pass
    return ["Что важного произошло за последнее время?"] * n


def synthesize_insights(
    focal_point: str,
    memories: list[MemoryRecord],
    llm: LLMProvider,
    agent_id: str,
) -> str:
    """Синтезирует высокоуровневый вывод по фокусной точке.

    Args:
        focal_point: Вопрос для размышления.
        memories: Релевантные воспоминания.
        llm: Провайдер языковой модели.
        agent_id: Идентификатор агента.

    Returns:
        Краткий вывод (1-2 предложения).
    """
    evidence_text = "\n".join(
        f"- [{r.id}] {r.content}" for r in memories
    )
    prompt = (
        f"Ты — внутренний голос агента {agent_id}. "
        f"На основе следующих воспоминаний ответь на вопрос: "
        f"{focal_point}\n\n"
        f"Воспоминания:\n{evidence_text}\n\n"
        f"Сформулируй один краткий вывод (1-2 предложения)."
    )
    response = llm.generate(system="", user=prompt)
    return response.text.strip()


def run_reflection_cycle(
    stream: MemoryStream,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    current_round: int,
) -> list[MemoryRecord]:
    """Выполняет полный цикл рефлексии.

    Последовательность шагов:
    1. Генерация фокусных точек на основе последних наблюдений.
    2. Извлечение релевантных воспоминаний по каждой точке.
    3. Синтез инсайтов через языковую модель.
    4. Сохранение рефлексий в поток памяти.
    5. Сброс счётчика важности.

    Args:
        stream: Поток памяти агента.
        llm: Провайдер языковой модели.
        embedder: Провайдер эмбеддингов.
        current_round: Номер текущего раунда.

    Returns:
        Список созданных записей-рефлексий.
    """
    focal_points = generate_focal_points(stream, llm)

    reflections: list[MemoryRecord] = []
    for focal in focal_points:
        query_emb = embedder.embed(focal)
        relevant = stream.retrieve(
            query_embedding=query_emb,
            current_round=current_round,
            top_k=MEMORIES_PER_FOCAL,
        )
        insight = synthesize_insights(focal, relevant, llm, stream.agent_id)
        evidence_ids = [r.id for r in relevant[:5]]
        emb = embedder.embed(insight)

        record = stream.add(
            content=insight,
            importance=8.0,
            kind="reflection",
            round_num=current_round,
            embedding=emb,
            evidence=evidence_ids,
        )
        reflections.append(record)

    stream.reset_importance_accumulator()
    return reflections
