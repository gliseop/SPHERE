from __future__ import annotations

from enum import Enum


class GovernanceMode(str, Enum):
    G0_BASELINE = "G0"
    G1_AUDIT = "G1"
    G2_AUDIT_REPUTATION = "G2"
    G3_FULL = "G3"


class ScenarioId(str, Enum):
    S0_CLEAN = "S0"
    S1_KICKBACK = "S1"
    S2_NEPOTISM = "S2"
    S3_CAROUSEL = "S3"
    S4_TIMING_INSIDER = "S4"
    S5_LINGUISTIC_MASK = "S5"
    S6_NOISE_FP = "S6"
    S7_ADAPTATION = "S7"
    S8_BOTTOM_UP = "S8"
    S9_IMMUNITY = "S9"


class AgentRole(str, Enum):
    OFFICIAL = "official"
    CONTRACTOR = "contractor"

