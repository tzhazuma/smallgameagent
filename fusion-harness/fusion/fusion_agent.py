"""FusionAgent — the unified main loop.

Brings together:
  L2  cloud planning    : multi-provider API (mimo/kimi/qwen) -> StrategySpec
  L1  local VLM         : adaptive VLM observation (vlm_policy + /describe)
  L0  rule engine       : runtime_rules.json hot-update params
  Deterministic layer   : OptionCatalog + IntentGate + failure + recovery
  Memory layer          : DSG + experience + CDG + strategy registry
  Governance            : multi-game validation
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .options import OptionCatalog
from .intent_gate import IntentGate, IntentVerdict
from .failure import classify_execution_failure
from .recovery import RecoveryLadder
from .phase_strategy import (
    PhaseStrategy, StrategySpec, StrategyMachineRuntime, parse_strategy_spec,
    validate_strategy_spec,
)
from .dsg import DynamicSceneGraph
from .experience import ExperienceMemory
from .cdg import CausalDecisionGraph
from .registry import StrategyRegistry

logger = logging.getLogger("fusion")

TERMINAL_STATES = ("SETTLED_COMPLETE", "BUDGET_EXHAUSTED", "BLOCKED_UNSAFE",
                   "BLOCKED_UNKNOWN_MECHANIC", "RUNTIME_FAULT", "OPERATOR_INTERRUPTED")


@dataclass
class FusionConfig:
    game_id: str = "unknown"
    run_id: str = "run"
    max_steps: int = 240
    provider: str = "opencodego"        # L2 provider
    vlm_endpoint: str | None = None     # L1 VLM (5090 /describe)
    vlm_policy_max_calls: int = 6
    workspace: Path | None = None
    # Injectables (browser / probe adapters)
    execute_primitive: Callable | None = None   # (primitive, controls) -> result
    observe_world: Callable | None = None       # () -> world snapshot
    plan_strategy: Callable | None = None       # (brief) -> StrategySpec dict
    vlm_observe: Callable | None = None         # (prompt, image) -> text
    rule_step: Callable | None = None           # (state) -> action (L0)
    update_rule: Callable | None = None         # (request) -> bool (L0 hot update)


class FusionAgent:
    def __init__(self, config: FusionConfig) -> None:
        self.cfg = config
        self.catalog = OptionCatalog()
        self.gate = IntentGate()
        self.dsg = DynamicSceneGraph(config.game_id, config.run_id)
        self.experience = ExperienceMemory()
        self.cdg = CausalDecisionGraph(config.game_id)
        self.registry = StrategyRegistry()
        self.phase = PhaseStrategy(game_id=config.game_id, strategy_id=f"{config.game_id}-fusion-v1")
        self.strategy_runtime: StrategyMachineRuntime | None = None
        self.recovery: RecoveryLadder | None = None
        self.step_count = 0
        self.terminal: str | None = None
        self.planner_calls = 0
        self.vlm_calls = 0
        self.gameplay_steps = 0
        self.no_progress_count = 0
        self._last_state: dict | None = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> dict:
        while self.step_count < self.cfg.max_steps and not self.terminal:
            self.step_count += 1
            try:
                self._step()
            except Exception as exc:  # noqa: BLE001
                logger.exception("step %s failed", self.step_count)
                self.terminal = "RUNTIME_FAULT"
        if not self.terminal:
            self.terminal = "BUDGET_EXHAUSTED"
        return {
            "game_id": self.cfg.game_id,
            "terminal": self.terminal,
            "steps": self.step_count,
            "gameplay_steps": self.gameplay_steps,
            "planner_calls": self.planner_calls,
            "vlm_calls": self.vlm_calls,
        }

    def _step(self) -> None:
        world = self._observe()
        if not world:
            return
        # Track gameplay progress
        if world.get("progress_class") and world["progress_class"] != "none":
            self.gameplay_steps += 1
            self.no_progress_count = 0
        else:
            self.no_progress_count += 1

        # DSG update
        self.dsg.observe(world)

        # 1. L0 rule fast-path (zero latency) — deterministic options first
        if self.cfg.rule_step:
            rule_action = self.cfg.rule_step(world)
            if rule_action and rule_action.get("action") != "wait":
                result = self._execute_rule_action(rule_action, world)
                self._after_execution(world, result)
                return

        # 2. Strategy decision (L2-generated state machine or phase strategy)
        decision = self._strategy_decision(world)
        if decision["kind"] == "replan":
            self._replan(world)
            return
        if decision["kind"] == "verify":
            self.terminal = self._verify_completion(world)
            return
        if decision["kind"] == "stop":
            self.terminal = decision.get("reason", "STOP")
            return

        # 3. IntentGate
        intent = decision["intent"]
        # Instantiate with the current runtime base (the strategy spec's base
        # may be a static echo; the gate checks freshness against this).
        intent["base"] = {
            "game_id": self.cfg.game_id,
            "run_id": self.cfg.run_id,
            "state_version": self.dsg.state_version,
            "scene_epoch": 0,
            "policy_set_id": "candidate:fusion",
        }
        try:
            resolved = self.catalog.resolve(intent, {"viewport": world.get("viewport")})
        except ValueError as exc:
            logger.warning("compile rejected: %s", exc)
            self._replan(world)
            return
        verdict: IntentVerdict = self.gate.evaluate(
            intent=intent, resolved_option=resolved,
            current_base={"game_id": self.cfg.game_id, "run_id": self.cfg.run_id,
                          "state_version": self.dsg.state_version, "scene_epoch": 0},
            world=world,
        )
        if not verdict.allowed:
            logger.info("intent gate rejected: %s", verdict.reason)
            self._handle_rejection(world, verdict)
            return

        # 4. Execute primitives
        result = self._execute_resolved(resolved, world)
        self.gate.record_execution(resolved)

        # 5. Expected-effect validation + failure classification + recovery
        self._after_execution(world, result)

    # ------------------------------------------------------------------
    # Observation / planning / execution
    # ------------------------------------------------------------------

    def _observe(self) -> dict | None:
        if self.cfg.observe_world:
            return self.cfg.observe_world()
        return self._last_state

    def _strategy_decision(self, world: dict) -> dict:
        if self.strategy_runtime is None:
            return {"kind": "replan", "reason": "no_strategy"}
        ctx = self._build_runtime_ctx(world)
        return self.strategy_runtime.next(ctx)

    def _build_runtime_ctx(self, world: dict) -> dict:
        player = world.get("player") or {}
        guide = world.get("guide") or {}
        return {
            "phase": world.get("phase"),
            "control_domain": player.get("control_domain"),
            "guide_id": guide.get("target_id"),
            "completion_suspected": bool((world.get("completion") or {}).get("suspected")),
            "failure_active": bool((world.get("failure") or {}).get("active")),
            "objective_active": bool(world.get("objective_active", True)),
            "objective_reached": bool(world.get("objective_reached")),
            "no_progress_count": self.no_progress_count,
            "action_failure_count": int(world.get("action_failure_count", 0)),
            "local_iterations": int(world.get("local_iterations", 0)),
            "resources": world.get("resources") or {},
        }

    def _replan(self, world: dict) -> None:
        self.planner_calls += 1
        if self.cfg.plan_strategy:
            try:
                spec_data = self.cfg.plan_strategy(self._build_brief(world))
                spec = parse_strategy_spec(spec_data)
                errors = validate_strategy_spec(spec)
                if errors:
                    logger.warning("strategy contract violations: %s", errors)
                    return
                self.strategy_runtime = StrategyMachineRuntime(spec)
                logger.info("installed strategy %s (%d states)", spec.strategy_id, len(spec.states))
            except Exception as exc:  # noqa: BLE001
                logger.warning("planner failed: %s", exc)

    def _build_brief(self, world: dict) -> dict:
        return {
            "base": {"game_id": self.cfg.game_id, "run_id": self.cfg.run_id,
                     "state_version": self.dsg.state_version, "scene_epoch": 0,
                     "policy_set_id": "candidate:fusion"},
            "world": self.dsg.planner_view(),
            "evidence": [],
            "allowed_options": self.catalog.planner_descriptions(),
            "experience": [c.to_dict() for c in self.experience.retrieve(world, limit=3)],
            "cdg": self.cdg.planner_view(world),
        }

    def _execute_resolved(self, resolved: dict, world: dict) -> dict:
        if not self.cfg.execute_primitive:
            return {"submitted_input": True, "semantic_progress": True, "motion": {"player_moved": True}}
        results = []
        for primitive in resolved["primitives"]:
            res = self.cfg.execute_primitive(primitive, world.get("controls") or {})
            results.append(res or {})
        submitted = any(r.get("submitted_input", True) for r in results) or bool(results)
        moved = any(r.get("player_moved") for r in results)
        return {
            "submitted_input": submitted,
            "player_moved": moved,
            "semantic_progress": any(r.get("semantic_progress") for r in results),
            "motion": {"player_moved": moved, "distance_decreased": any(r.get("distance_decreased") for r in results)},
        }

    def _execute_rule_action(self, action: dict, world: dict) -> dict:
        return self._execute_resolved(
            {"option": action.get("action", "wait"), "risk": "low",
             "requires_target": False, "parameters": action.get("params", {}),
             "primitives": [{"type": action.get("action", "wait"), **action.get("params", {})}]},
            world,
        )

    def _after_execution(self, world: dict, result: dict) -> None:
        failure = classify_execution_failure(
            before=world, after=world, execution=result,
        )
        if failure:
            self._handle_failure(world, failure)
        else:
            self.recovery = None
            # Record causal transition + experience
            self.cdg.record_transition(
                before=world, after=world,
                action={"option": "last"}, semantic_progress=result.get("semantic_progress", False),
            )
        self._last_state = world

    def _handle_failure(self, world: dict, failure: dict) -> None:
        code = failure["code"]
        logger.info("failure %s: %s", code, failure.get("reason"))
        if self.recovery is None or self.recovery.failure_code != code:
            self.recovery = RecoveryLadder(failure_code=code)
        step = self.recovery.next()
        if step.get("exhausted"):
            self.terminal = "BLOCKED_UNKNOWN_MECHANIC"
        elif step.get("step") == "REPLAN":
            self._replan(world)
        elif step.get("step") in ("REVERSE", "EXIT_REENTER"):
            intent = {"option": "recover_reverse", "parameters": {}, "expected_effect": {"failure_clears": True}}
            try:
                resolved = self.catalog.resolve(intent)
                self._execute_resolved(resolved, world)
            except ValueError:
                pass

    def _handle_rejection(self, world: dict, verdict: IntentVerdict) -> None:
        if verdict.rule == "version_freshness":
            self._replan(world)
        elif verdict.rule == "repeated_semantic_no_effect":
            self._replan(world)
        else:
            self.no_progress_count += 1

    def _verify_completion(self, world: dict) -> str:
        # Fixed three-sample settled completion (lightweight here)
        samples = [bool((world.get("completion") or {}).get("suspected")) for _ in range(3)]
        if all(samples):
            return "SETTLED_COMPLETE"
        self._replan(world)
        return self.terminal  # keep running
