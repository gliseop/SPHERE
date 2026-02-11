from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from magistry_sim.enums import AgentRole, GovernanceMode, ScenarioId


class GovernanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: GovernanceMode
    risk_threshold: float = 0.70
    critical_threshold: float = 0.85
    jury_size: int = 5


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ScenarioId
    title: str
    description: str

    ticks: int = 1
    seed: int = 0

    num_officials: int = 8
    num_contractors: int = 4

    tender_budget: float = 1_000_000.0

    relationship_strength: float = 0.0
    explicit_bribe: bool = False
    mask_language: bool = False
    enable_carousel: bool = False
    enable_timing_anomaly: bool = False
    noise_level: float = 0.0
    enable_adaptation: bool = False
    enable_bottom_up_signal: bool = False
    include_immune_influencer: bool = False


class SocialCapital(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work: float = 0.0
    research: float = 0.0
    social: float = 0.0

    @property
    def total(self) -> float:
        return self.work + self.research + self.social


class Agent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: AgentRole

    greed: float
    fear: float
    honesty: float
    competence: float

    immune: bool = False
    frozen: bool = False

    eligible_for_lpr: bool = True
    eligible_for_contracts: bool = True

    social_capital: SocialCapital = Field(default_factory=SocialCapital)


class NegotiationMessage(BaseModel):
    """Одно сообщение в переговорах."""

    model_config = ConfigDict(extra="forbid")

    sender_id: str
    receiver_id: str
    text: str
    round: int
    response_time_ms: float


class NegotiationResult(BaseModel):
    """Результат переговоров между ЛПР и подрядчиком."""

    model_config = ConfigDict(extra="forbid")

    lpr_id: str
    contractor_id: str
    messages: list[NegotiationMessage]
    deal_reached: bool
    kickback_percent: float | None = None
    total_rounds: int


class PublicTenderData(BaseModel):
    """Данные, доступные аудитору (НЕ ground truth).

    Аудитор видит только эти поля. Поле corruption
    отсутствует — аудитор не знает правду.
    """

    model_config = ConfigDict(extra="forbid")

    tick: int
    lpr_id: str
    contractor_id: str

    # Публичные коммуникации (shadow layer chat)
    messages: list[NegotiationMessage]

    # Тендерные данные
    bids: dict[str, float]
    winner_id: str
    tender_budget: float

    # Граф (публичные связи)
    social_tie_strength: float
    social_tie_kind: str | None = None

    # Поведенческие метрики
    response_times_ms: list[float]

    # Контекст
    noise_level: float = 0.0

    # История для контекстного анализа
    lpr_previous_winners: list[str] | None = None
    contractor_win_streak: int = 0


class Event(BaseModel):
    model_config = ConfigDict(extra="allow")

    tick: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RiskReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick: int
    lpr_id: str
    contractor_id: str
    risk_score: float
    reasons: list[str]
    critical: bool


class TribunalResult(BaseModel):
    """Результат трибунала с полной информацией о голосовании."""

    model_config = ConfigDict(extra="forbid")

    guilty: bool
    juror_ids: list[str]
    votes_guilty: int
    votes_total: int


class TickOutcome(BaseModel):
    """Результат одного тика симуляции."""

    model_config = ConfigDict(extra="forbid")

    tick: int
    tender_budget: float
    fair_winner_id: str
    actual_winner_id: str
    corruption: bool

    negotiation: NegotiationResult | None = None

    audit_risk: float | None = None
    audit_flagged: bool = False
    tribunal_triggered: bool = False
    tribunal_guilty: bool | None = None


class ConfusionMatrix(BaseModel):
    """Матрица ошибок аудитора."""

    model_config = ConfigDict(extra="forbid")

    tp: int = 0  # True Positive: corruption=True, audit_flagged=True
    fp: int = 0  # False Positive: corruption=False, audit_flagged=True
    tn: int = 0  # True Negative: corruption=False, audit_flagged=False
    fn: int = 0  # False Negative: corruption=True, audit_flagged=False

    @property
    def precision(self) -> float:
        """Точность: TP / (TP + FP)."""
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0.0

    @property
    def recall(self) -> float:
        """Полнота: TP / (TP + FN)."""
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0.0

    @property
    def f1(self) -> float:
        """F1-мера: гармоническое среднее precision и recall."""
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @property
    def accuracy(self) -> float:
        """Точность: (TP + TN) / total."""
        total = self.tp + self.fp + self.tn + self.fn
        return (self.tp + self.tn) / total if total > 0 else 0.0


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: ScenarioId
    governance: GovernanceMode
    seed: int
    ticks: int

    outcomes: list[TickOutcome]
    final_agents: dict[str, Agent]
    events: list[Event]

