"""Многораундовые переговоры Business ↔ Official через LLM."""

from __future__ import annotations

import json
import re
from random import Random

from magistry_sim.llm import LLMProvider
from magistry_sim.memory import AgentMemory, MemoryItemType
from magistry_sim.models import Agent, NegotiationMessage, NegotiationResult, Scenario

# ─────────────────────────────────────────────────────────────────────────────
# System prompts
# ─────────────────────────────────────────────────────────────────────────────

CONTRACTOR_SYSTEM = """Ты — {name}, подрядчик в тендере на {budget:,.0f} руб.
Твои характеристики: жадность={greed:.1f}, страх={fear:.1f}, честность={honesty:.1f}.
Контекст твоей истории:
{memory_context}

Ты общаешься с чиновником {lpr_name}.
Если жадность > страха — предложи откат. Если честность высока — действуй по правилам.
Отвечай ОДНИМ предложением. Не раскрывай свои числовые параметры."""

OFFICIAL_SYSTEM = """Ты — {name}, чиновник (ЛПР) в тендере на {budget:,.0f} руб.
Твои характеристики: жадность={greed:.1f}, страх={fear:.1f}, честность={honesty:.1f}.
Контекст твоей истории:
{memory_context}

К тебе обращается подрядчик {contractor_name}.
Если тебе предлагают откат и твоя жадность > честности — соглашайся.
Если страх высок или ты честен — отказывай.
Отвечай ОДНИМ предложением."""

DECISION_SYSTEM = """На основе переговоров определи:
1. deal_reached: true/false
2. kickback_percent: число 0-30 (если deal_reached=true)
Ответь строго в JSON: {{"deal_reached": bool, "kickback_percent": float | null}}"""


class NegotiationProtocol:
    """Многораундовые переговоры Business ↔ Official.

    Args:
        llm: LLM-провайдер для генерации диалогов.
        max_rounds: Максимальное число раундов переговоров.
    """

    MAX_ROUNDS = 3

    def __init__(self, llm: LLMProvider, *, max_rounds: int = 3):
        self.llm = llm
        self.max_rounds = max_rounds

    async def negotiate(
        self,
        *,
        lpr: Agent,
        contractor: Agent,
        scenario: Scenario,
        tick: int,
        lpr_memory: AgentMemory,
        contractor_memory: AgentMemory,
        force_deal_reached: bool | None = None,
        rng: Random,
    ) -> NegotiationResult:
        """Провести переговоры.

        Args:
            lpr: Агент-чиновник (ЛПР).
            contractor: Агент-подрядчик.
            scenario: Текущий сценарий.
            tick: Тик симуляции для записи в AgentMemory.
            lpr_memory: Память чиновника.
            contractor_memory: Память подрядчика.
            force_deal_reached: Принудительно задать результат сделки (например, для режима "карусель").
            rng: Генератор случайных чисел.

        Returns:
            NegotiationResult с сообщениями и решением.
        """
        messages: list[NegotiationMessage] = []

        # Записать начало переговоров в память
        contractor_memory.add_event(
            tick, MemoryItemType.NEGOTIATION_START,
            f"Начаты переговоры с {lpr.name} по тендеру на {scenario.tender_budget:,.0f}",
        )
        lpr_memory.add_event(
            tick, MemoryItemType.NEGOTIATION_START,
            f"Начаты переговоры с {contractor.name} по тендеру на {scenario.tender_budget:,.0f}",
        )

        # Формируем системные промпты
        contractor_sys = CONTRACTOR_SYSTEM.format(
            name=contractor.name,
            budget=scenario.tender_budget,
            greed=contractor.greed,
            fear=contractor.fear,
            honesty=contractor.honesty,
            memory_context=contractor_memory.get_context(max_items=10),
            lpr_name=lpr.name,
        )

        official_sys = OFFICIAL_SYSTEM.format(
            name=lpr.name,
            budget=scenario.tender_budget,
            greed=lpr.greed,
            fear=lpr.fear,
            honesty=lpr.honesty,
            memory_context=lpr_memory.get_context(max_items=10),
            contractor_name=contractor.name,
        )

        # Многораундовые переговоры
        conversation_context = ""
        for round_num in range(1, self.max_rounds + 1):
            # Contractor говорит первым
            contractor_prompt = f"Раунд {round_num}. {conversation_context}Начни/продолжи переговоры."
            contractor_response = await self.llm.generate(
                contractor_sys, contractor_prompt, temperature=0.7,
            )
            contractor_msg = NegotiationMessage(
                sender_id=contractor.id,
                receiver_id=lpr.id,
                text=contractor_response.text.strip(),
                round=round_num,
                response_time_ms=contractor_response.response_time_ms,
            )
            messages.append(contractor_msg)
            conversation_context += f"{contractor.name}: {contractor_msg.text}\n"

            # LPR отвечает
            official_prompt = f"Раунд {round_num}. {conversation_context}Ответь подрядчику."
            official_response = await self.llm.generate(
                official_sys, official_prompt, temperature=0.7,
            )
            official_msg = NegotiationMessage(
                sender_id=lpr.id,
                receiver_id=contractor.id,
                text=official_response.text.strip(),
                round=round_num,
                response_time_ms=official_response.response_time_ms,
            )
            messages.append(official_msg)
            conversation_context += f"{lpr.name}: {official_msg.text}\n"

        # Финальное решение через LLM (или принудительно)
        if force_deal_reached is None:
            decision_prompt = f"Переговоры:\n{conversation_context}\nОпредели результат."
            decision_response = await self.llm.generate(
                DECISION_SYSTEM, decision_prompt, temperature=0.2,
            )
            deal_reached, kickback_percent = self._parse_decision(
                decision_response.text, contractor=contractor, lpr=lpr, rng=rng,
            )
        else:
            deal_reached = bool(force_deal_reached)
            kickback_percent = None
            if deal_reached:
                kickback_percent = rng.uniform(5, 20)
                kickback_percent = max(0.0, min(30.0, float(kickback_percent)))

        # Записать результат в память обоих агентов
        if deal_reached:
            if kickback_percent is None:
                kickback_percent = rng.uniform(5, 20)
                kickback_percent = max(0.0, min(30.0, float(kickback_percent)))
            result_text = f"Достигнута договорённость с откатом {kickback_percent:.0f}%"
            mem_type = MemoryItemType.DEAL_REACHED
        else:
            result_text = "Переговоры завершены без договорённости"
            mem_type = MemoryItemType.DEAL_REJECTED

        contractor_memory.add_event(tick, mem_type, result_text)
        lpr_memory.add_event(tick, mem_type, result_text)

        return NegotiationResult(
            lpr_id=lpr.id,
            contractor_id=contractor.id,
            messages=messages,
            deal_reached=deal_reached,
            kickback_percent=kickback_percent,
            total_rounds=self.max_rounds,
        )

    def _parse_decision(
        self,
        text: str,
        *,
        contractor: Agent,
        lpr: Agent,
        rng: Random,
    ) -> tuple[bool, float | None]:
        """Парсинг решения из LLM ответа.

        Args:
            text: Ответ LLM (ожидается JSON).
            contractor: Подрядчик.
            lpr: Чиновник.
            rng: RNG для fallback.

        Returns:
            Tuple (deal_reached, kickback_percent).
        """
        # Пытаемся распарсить JSON
        try:
            # Извлечь JSON из текста (может быть обёрнут в текст)
            json_match = re.search(r'\{[^}]+\}', text)
            if json_match:
                data = json.loads(json_match.group())
                deal = bool(data.get("deal_reached", False))
                if not deal:
                    return False, None

                kickback_raw = data.get("kickback_percent")
                try:
                    kickback = float(kickback_raw) if kickback_raw is not None else rng.uniform(5, 20)
                except (ValueError, TypeError):
                    kickback = rng.uniform(5, 20)
                kickback = max(0.0, min(30.0, kickback))
                return True, kickback
        except json.JSONDecodeError:
            pass

        # Fallback: на основе traits агентов
        avg_greed = (contractor.greed + lpr.greed) / 2
        avg_honesty = (contractor.honesty + lpr.honesty) / 2
        deal = avg_greed > avg_honesty
        kickback = rng.uniform(5, 20) if deal else None
        return deal, kickback
