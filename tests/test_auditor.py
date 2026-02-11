"""Тесты слепого аудитора (Stage 4)."""
from __future__ import annotations

import pytest

from magistry_sim.auditor import Auditor
from magistry_sim.enums import GovernanceMode
from magistry_sim.llm import MockLLMProvider
from magistry_sim.models import (
    GovernanceConfig,
    NegotiationMessage,
    PublicTenderData,
)


def _make_auditor(seed: int = 42) -> Auditor:
    """Создать аудитора с mock LLM."""
    return Auditor(
        config=GovernanceConfig(mode=GovernanceMode.G3_FULL),
        llm=MockLLMProvider(seed=seed),
    )


def _make_clean_messages() -> list[NegotiationMessage]:
    """Создать чистые (не подозрительные) сообщения."""
    return [
        NegotiationMessage(
            sender_id="biz_0", receiver_id="off_0",
            text="Предлагаю обсудить техническое задание и условия контракта.",
            round=1, response_time_ms=300.0,
        ),
        NegotiationMessage(
            sender_id="off_0", receiver_id="biz_0",
            text="Решение будет приниматься строго по конкурсной документации.",
            round=1, response_time_ms=350.0,
        ),
    ]


def _make_corrupt_messages() -> list[NegotiationMessage]:
    """Создать подозрительные сообщения."""
    return [
        NegotiationMessage(
            sender_id="biz_0", receiver_id="off_0",
            text="Могу предложить откат 15%, это стандартная практика.",
            round=1, response_time_ms=200.0,
        ),
        NegotiationMessage(
            sender_id="off_0", receiver_id="biz_0",
            text="Процент устраивает, договоримся.",
            round=1, response_time_ms=150.0,
        ),
    ]


@pytest.mark.asyncio
async def test_blind_auditor_clean_deal() -> None:
    """Чистые messages → low risk."""
    auditor = _make_auditor()
    data = PublicTenderData(
        tick=0, lpr_id="off_0", contractor_id="biz_0",
        messages=_make_clean_messages(),
        bids={"biz_0": 900_000, "biz_1": 950_000},
        winner_id="biz_0",
        tender_budget=1_000_000.0,
        social_tie_strength=0.1,
        response_times_ms=[300.0, 350.0],
    )
    report = await auditor.assess(data=data)
    assert report.risk_score < 0.5
    assert report.critical is False


@pytest.mark.asyncio
async def test_blind_auditor_corrupt_messages() -> None:
    """Подозрительные messages → high risk."""
    auditor = _make_auditor()
    data = PublicTenderData(
        tick=0, lpr_id="off_0", contractor_id="biz_0",
        messages=_make_corrupt_messages(),
        bids={"biz_0": 950_000, "biz_1": 900_000},  # winner не лучший
        winner_id="biz_0",
        tender_budget=1_000_000.0,
        social_tie_strength=0.9,
        response_times_ms=[200.0, 150.0],
    )
    report = await auditor.assess(data=data)
    assert report.risk_score > 0.4
    assert len(report.reasons) > 0


@pytest.mark.asyncio
async def test_auditor_no_access_to_ground_truth() -> None:
    """PublicTenderData не содержит поле corruption."""
    fields = PublicTenderData.model_fields
    assert "corruption" not in fields
    assert "deal_reached" not in fields
    assert "explicit_bribe" not in fields


@pytest.mark.asyncio
async def test_auditor_with_noise() -> None:
    """Высокий noise_level увеличивает risk."""
    auditor = _make_auditor()

    base_data = PublicTenderData(
        tick=0, lpr_id="off_0", contractor_id="biz_0",
        messages=_make_clean_messages(),
        bids={"biz_0": 900_000, "biz_1": 950_000},
        winner_id="biz_0",
        tender_budget=1_000_000.0,
        social_tie_strength=0.1,
        response_times_ms=[300.0, 350.0],
        noise_level=0.0,
    )
    noisy_data = base_data.model_copy(update={"noise_level": 0.6})

    report_clean = await auditor.assess(data=base_data)
    report_noisy = await auditor.assess(data=noisy_data)

    assert report_noisy.risk_score >= report_clean.risk_score


@pytest.mark.asyncio
async def test_bid_deviation_detection() -> None:
    """Победитель с не лучшей ценой → повышенный risk."""
    auditor = _make_auditor()

    # Нормальный: лучшая ставка побеждает
    data_normal = PublicTenderData(
        tick=0, lpr_id="off_0", contractor_id="biz_0",
        messages=_make_clean_messages(),
        bids={"biz_0": 850_000, "biz_1": 950_000},
        winner_id="biz_0",  # лучшая ставка
        tender_budget=1_000_000.0,
        social_tie_strength=0.1,
        response_times_ms=[300.0, 350.0],
    )

    # Аномальный: не лучшая ставка побеждает
    data_anomaly = PublicTenderData(
        tick=0, lpr_id="off_0", contractor_id="biz_1",
        messages=_make_clean_messages(),
        bids={"biz_0": 850_000, "biz_1": 1_050_000},
        winner_id="biz_1",  # не лучшая ставка
        tender_budget=1_000_000.0,
        social_tie_strength=0.1,
        response_times_ms=[300.0, 350.0],
    )

    report_normal = await auditor.assess(data=data_normal)
    report_anomaly = await auditor.assess(data=data_anomaly)

    assert report_anomaly.risk_score > report_normal.risk_score
