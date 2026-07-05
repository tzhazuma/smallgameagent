"""Shared agent state blackboard — AgentContext dataclass.

All agent components (probe, visual analyzer, LLM, rule engine, hybrid agent)
read from and write to this context object to share state without tight coupling.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentContext:
    """Blackboard dataclass for shared agent state across all components.

    Fields are deliberately typed as ``Any`` for ``extracted_rules`` and
    ``working_memory`` to avoid circular imports from ``src.engine``.

    ``current_mode`` controls which execution path the hybrid agent selects
    (e.g. ``"api"``, ``"rule"``, ``"vision"``, ``"hybrid"``).
    """

    # ── Probe / browser state ──────────────────────────────────────────
    probe_state: dict[str, Any] = field(default_factory=dict)
    screenshot: bytes | None = None

    # ── Visual analysis ────────────────────────────────────────────────
    visual_struct: dict[str, Any] | None = None

    # ── LLM / VLM decisions (per-step, cleared by clear_step) ──────────
    text_decision: dict[str, Any] | None = None
    vision_decision: dict[str, Any] | None = None
    final_action: dict[str, Any] | None = None

    # ── Rule engine (Any to avoid circular imports) ────────────────────
    extracted_rules: Any = None
    working_memory: Any = None

    # ── Execution control ──────────────────────────────────────────────
    current_mode: str = "api"
    step_number: int = 0

    # ── Diagnostics ────────────────────────────────────────────────────
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Serialisation ──────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation of the context.

        Binary fields (``screenshot``) are converted to their string
        representation; ``extracted_rules`` and ``working_memory`` are
        stored as their ``repr()`` when not ``None``.
        """
        return {
            "probe_state": dict(self.probe_state),
            "screenshot": repr(self.screenshot) if self.screenshot is not None else None,
            "visual_struct": copy.deepcopy(self.visual_struct),
            "text_decision": copy.deepcopy(self.text_decision),
            "vision_decision": copy.deepcopy(self.vision_decision),
            "final_action": copy.deepcopy(self.final_action),
            "extracted_rules": repr(self.extracted_rules) if self.extracted_rules is not None else None,
            "working_memory": repr(self.working_memory) if self.working_memory is not None else None,
            "current_mode": self.current_mode,
            "step_number": self.step_number,
            "errors": list(self.errors),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: dict) -> AgentContext:
        """Reconstruct an ``AgentContext`` from a dict returned by ``to_dict()``.

        .. note::

            Because ``to_dict`` converts binary and complex fields to string
            representations, ``from_dict`` will **not** restore the original
            ``screenshot``, ``extracted_rules``, or ``working_memory``
            objects — those fields are left as ``None`` after round-tripping.
        """
        ctx = AgentContext()
        ctx.probe_state = dict(data.get("probe_state", {}))
        ctx.visual_struct = copy.deepcopy(data.get("visual_struct"))
        ctx.text_decision = copy.deepcopy(data.get("text_decision"))
        ctx.vision_decision = copy.deepcopy(data.get("vision_decision"))
        ctx.final_action = copy.deepcopy(data.get("final_action"))
        ctx.current_mode = str(data.get("current_mode", "api"))
        ctx.step_number = int(data.get("step_number", 0))
        ctx.errors = list(data.get("errors", []))
        ctx.metadata = dict(data.get("metadata", {}))
        return ctx

    # ── Lifecycle helpers ──────────────────────────────────────────────

    def snapshot(self) -> AgentContext:
        """Return a deep-ish independent copy of this context.

        ``screenshot`` is shared by reference (bytes is immutable, so a
        true deep copy is unnecessary).  All other mutable containers are
        deep-copied.
        """
        return AgentContext(
            probe_state=copy.deepcopy(self.probe_state),
            screenshot=self.screenshot,
            visual_struct=copy.deepcopy(self.visual_struct),
            text_decision=copy.deepcopy(self.text_decision),
            vision_decision=copy.deepcopy(self.vision_decision),
            final_action=copy.deepcopy(self.final_action),
            extracted_rules=self.extracted_rules,
            working_memory=self.working_memory,
            current_mode=self.current_mode,
            step_number=self.step_number,
            errors=list(self.errors),
            metadata=copy.deepcopy(self.metadata),
        )

    def clear_step(self) -> None:
        """Reset per-step fields while preserving persistent state.

        The following fields are cleared:
        - ``text_decision``
        - ``vision_decision``
        - ``final_action``

        The following are **kept**:
        - ``probe_state``
        - ``screenshot``
        - ``visual_struct``
        - ``extracted_rules``
        - ``working_memory``
        - ``current_mode``
        - ``step_number``
        - ``errors``
        - ``metadata``
        """
        self.text_decision = None
        self.vision_decision = None
        self.final_action = None
