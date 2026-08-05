"""Adaptive VLM call policy for the three-layer architecture (L1 switch).

Bridges the finding from the 14-game VLM comparison (REPORT.md §38.43): VLM
visual grounding helps gameplay substantially when frame information is
critical to strategy, but hurts when the strategy already advances on text
probe data alone and the VLM latency (8-17s for kimi-k2.6) burns budget.

This module decides *when* to call the VLM and *which* perception tasks to
request, so L1 is engaged only when it pays off:

- If recent gameplay steps are progressing, skip the VLM (text probe suffices).
- If the agent is stalling or probe information is insufficient, enable the
  VLM for high-acceptance tasks first (completion_evidence / failure_observation
  / phase_observation), escalating to visual_grounding only on continued stall.
- Hard caps bound VLM cost per run.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Task types ordered by (acceptance reliability, cost) — high-acceptance first.
#: backend_grounding is the least reliable for kimi-k2.6, so it is requested
#: only after other tasks have been tried without resolving the stall.
HIGH_ACCEPTANCE_TASKS = (
    "completion_evidence",
    "failure_observation",
    "phase_observation",
    "temporal_change_observation",
)
ESCALATION_TASKS = (
    "visual_grounding",
    "backend_grounding",
)


@dataclass
class VlmCallPolicy:
    stall_threshold: int = 4
    progress_window: int = 6
    min_progress_steps: int = 3
    max_calls_per_run: int = 6
    cooldown_steps: int = 8
    info_sufficient_threshold: float = 0.5  # probe info sufficiency in [0,1]

    #: Internal state.
    _last_step: int = 0
    _calls_this_run: int = 0
    _last_call_step: int = -100
    _recent_gameplay: list[bool] = field(default_factory=list)
    _escalated: bool = False

    def observe_step(self, step_number: int, gameplay_advanced: bool) -> None:
        """Feed one executed step into the policy window."""
        self._last_step = step_number
        self._recent_gameplay.append(gameplay_advanced)
        if len(self._recent_gameplay) > self.progress_window:
            self._recent_gameplay.pop(0)

    def _recent_progress_count(self) -> int:
        return sum(1 for v in self._recent_gameplay if v)

    def _stall_count(self) -> int:
        # Consecutive non-advancing steps at the tail of the window.
        count = 0
        for v in reversed(self._recent_gameplay):
            if v:
                break
            count += 1
        return count

    def should_call_vlm(self, step_number: int, probe_info_sufficiency: float | None = None) -> bool:
        """Return True when the L1 VLM should be engaged at this step."""
        if self._calls_this_run >= self.max_calls_per_run:
            return False
        if step_number - self._last_call_step < self.cooldown_steps:
            return False
        # Progressing well -> text probe suffices, skip VLM.
        if self._recent_progress_count() >= self.min_progress_steps:
            return False
        # Stalling -> enable VLM.
        if self._stall_count() >= self.stall_threshold:
            return True
        # Probe information insufficient -> VLM may help.
        if probe_info_sufficiency is not None and probe_info_sufficiency < self.info_sufficient_threshold:
            return True
        return False

    def record_call(self, step_number: int) -> None:
        self._calls_this_run += 1
        self._last_call_step = step_number

    def tasks_for_call(self) -> list[str]:
        """Choose perception tasks for this VLM call.

        Starts with high-acceptance tasks; escalates to visual_grounding /
        backend_grounding only if the policy has already escalated (continued
        stall despite earlier calls).
        """
        if self._escalated or self._calls_this_run >= 3:
            self._escalated = True
            return [*HIGH_ACCEPTANCE_TASKS, *ESCALATION_TASKS[:1]]
        return list(HIGH_ACCEPTANCE_TASKS)

    def reset_run(self) -> None:
        self._last_step = 0
        self._calls_this_run = 0
        self._last_call_step = -100
        self._recent_gameplay = []
        self._escalated = False
