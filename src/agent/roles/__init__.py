"""Role framework — BaseAgentRole ABC, RoleCard, RolePipeline, and all 6 agent roles.

Derived from the PPT's 6-agent design (Observer, StateMapper, DecisionAnalyst,
Verifier, MemoryCurator, SkillBuilder)."""

from __future__ import annotations

from .base import BaseAgentRole, RoleCard, run_pipeline
from .decision_analyst import DecisionAnalyst
from .memory_curator import MemoryCurator
from .observer import Observer
from .skill_builder import SkillBuilder
from .state_mapper import StateMapper
from .verifier import Verdict, Verifier

__all__ = [
    "BaseAgentRole",
    "DecisionAnalyst",
    "MemoryCurator",
    "Observer",
    "RoleCard",
    "SkillBuilder",
    "StateMapper",
    "Verdict",
    "Verifier",
    "run_pipeline",
]
