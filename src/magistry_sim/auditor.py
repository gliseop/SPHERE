"""ИИ-аудитор: оценка рисков коррупции на основе ТОЛЬКО публичных данных."""

from __future__ import annotations

import re
from dataclasses import dataclass

from magistry_sim.llm import LLMProvider
from magistry_sim.models import (
    GovernanceConfig,
    NegotiationMessage,
    PublicTenderData,
    RiskReport,
)

# ─────────────────────────────────────────────────────────────────────────────
# System prompts
# ─────────────────────────────────────────────────────────────────────────────

AUDITOR_TEXT_ANALYSIS_PROMPT = """Ты ИИ-аудитор, анализирующий переписку участников тендера.
Оцени вероятность коррупционного сговора на основе:
- Лексики и семантики
- Нетипичная осторожность или уклончивость
- Признаки заранее согласованного исхода
- Нормальная деловая коммуникация (низкий риск)

Ответь ОДНИМ числом от 0.0 до 1.0, где:
0.0 = чистая деловая коммуникация
1.0 = явный сговор
Только число, без объяснений."""


@dataclass(slots=True)
class Auditor:
    """ИИ-аудитор для оценки рисков коррупции.

    Видит ТОЛЬКО публичные данные (PublicTenderData).
    Не имеет доступа к ground truth (corruption flag).

    Args:
        config: Конфигурация режима управления.
        llm: LLM-провайдер для текстового анализа.
    """

    config: GovernanceConfig
    llm: LLMProvider

    async def assess(self, *, data: PublicTenderData) -> RiskReport:
        """Оценить риск коррупции на основе ТОЛЬКО публичных данных.

        Args:
            data: Публичные данные тендера (аудитор НЕ знает ground truth).

        Returns:
            RiskReport с risk_score и reasons.
        """
        reasons: list[str] = []
        risk = 0.05  # baseline

        # 1. Graph analysis (публичные связи)
        if data.social_tie_strength >= 0.8:
            reasons.append("Strong social tie (graph)")
            risk += data.social_tie_strength * 0.35
        elif data.social_tie_strength >= 0.4:
            reasons.append("Moderate social tie (graph)")
            risk += data.social_tie_strength * 0.15

        # 2. Bid analysis (отклонение от fair price)
        bid_deviation = self._analyze_bids(data.bids, data.winner_id)
        if bid_deviation > 0.15:
            reasons.append(f"Bid deviation anomaly: {bid_deviation:.0%}")
            risk += bid_deviation * 0.25

        # 3. LLM text analysis (ключевое изменение!)
        text_risk = await self._analyze_text(data.messages)
        if text_risk > 0.3:
            reasons.append(f"Suspicious communication (LLM): {text_risk:.2f}")
            risk += text_risk * 0.30

        # 4. Behavioral analysis (timing)
        timing_risk = self._analyze_timing(data.response_times_ms)
        if timing_risk > 0.2:
            reasons.append(f"Timing anomaly: {timing_risk:.2f}")
            risk += timing_risk * 0.15

        # 5. Noise penalty
        if data.noise_level >= 0.4:
            reasons.append("Log noise / missing data (uncertainty)")
            risk += data.noise_level * 0.10

        risk = max(0.0, min(1.0, risk))

        return RiskReport(
            tick=data.tick,
            lpr_id=data.lpr_id,
            contractor_id=data.contractor_id,
            risk_score=risk,
            reasons=reasons,
            critical=risk >= self.config.critical_threshold,
        )

    async def _analyze_text(self, messages: list[NegotiationMessage]) -> float:
        """LLM-анализ текстов переговоров.

        Args:
            messages: Список сообщений переговоров.

        Returns:
            Скор риска 0.0-1.0.
        """
        if not messages:
            return 0.0

        chat_text = "\n".join(f"{m.sender_id}: {m.text}" for m in messages)

        response = await self.llm.generate(
            AUDITOR_TEXT_ANALYSIS_PROMPT,
            chat_text,
            temperature=0.2,
        )
        return self._parse_risk_score(response.text)

    @staticmethod
    def _parse_risk_score(text: str) -> float:
        """Извлечь числовой скор из ответа LLM.

        Args:
            text: Ответ LLM (ожидается число 0.0-1.0).

        Returns:
            Числовой скор, ограниченный [0.0, 1.0].
        """
        m = re.search(r'(\d+\.?\d*)', text.strip())
        if m:
            return max(0.0, min(1.0, float(m.group(1))))
        return 0.3  # fallback при непарсимом ответе

    @staticmethod
    def _analyze_timing(response_times: list[float]) -> float:
        """Анализ аномалий в латентности ответов.

        Аномально быстрые ответы (< 150ms) подозрительны — как будто
        агент знал ответ заранее. Аномально медленные — тоже
        (долгое обдумывание сложной лжи).

        Args:
            response_times: Список времён ответов в ms.

        Returns:
            Скор аномальности 0.0-1.0.
        """
        if not response_times:
            return 0.0
        avg = sum(response_times) / len(response_times)
        if avg < 150:
            return min(1.0, (150 - avg) / 150)
        if avg > 800:
            return min(1.0, (avg - 800) / 1000)
        return 0.0

    @staticmethod
    def _analyze_bids(bids: dict[str, float], winner_id: str) -> float:
        """Анализ аномалий в ставках.

        Если победитель не дал лучшую цену — подозрительно.
        Если все ставки подозрительно близки — потенциальный сговор.

        Args:
            bids: Ставки участников {agent_id: price}.
            winner_id: ID победителя.

        Returns:
            Скор отклонения 0.0-1.0.
        """
        if not bids or winner_id not in bids:
            return 0.0
        winner_bid = bids[winner_id]
        best_bid = min(bids.values())
        if winner_bid > best_bid:
            deviation = (winner_bid - best_bid) / best_bid
            return min(1.0, deviation)
        return 0.0
