#!/usr/bin/env python3
"""End-to-end test of L2 code-file rule updates.

This experiment replays the first N steps of a processed-run trajectory through
``HierarchicalPlanner`` with a mock cloud client.  The mock L2 returns a single
``code_file`` update targeted at ``configs/runtime_rules.json``.  We verify that:

1. ``RuleUpdateApplier`` accepts and applies the patch (confidence >= 0.9,
   file is on the allowlist, patch is small and unambiguous).
2. ``RuleEngine._param()`` reads the new value on subsequent steps without
   restart.
3. The JSON file is automatically backed up in
   ``configs/.rule_backups/runtime_rules.json.0.bak``.
4. After the experiment the original file is restored.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.agent.hierarchical_planner import HierarchicalPlanner  # noqa: E402
from src.agent.rule_update import RuleParameters  # noqa: E402
from src.engine.rules import RuleEngine  # noqa: E402
from src.experiments.offline_replay import (  # noqa: E402
    actions_match,
    adapt_ground_truth,
    adapt_processed_state,
    build_fake_context,
    load_state,
    load_steps,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
PROCESSED_ROOT = ROOT / "processed-runs"
RUNTIME_RULES = ROOT / "configs" / "runtime_rules.json"
BACKUP_DIR = ROOT / "configs" / ".rule_backups"


class MockCodeFileUpdateClient:
    """Mock cloud client that returns one code-file update then no-ops."""

    def __init__(self, *, trigger_step: int = 5) -> None:
        self.plan_calls = 0
        self.update_calls = 0
        self.trigger_step = trigger_step
        self.last_update_step: int | None = None

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        system = (messages[0].get("content", "") if messages else "").lower()
        is_plan = "game strategy planner" in system
        is_update = "strategy optimizer" in system

        if is_plan:
            self.plan_calls += 1
            content = json.dumps({
                "plan": [{"action_hint": "wait", "duration_ms": 500, "reason": "mock plan"}],
                "reason": "mock offline replay",
            })
        elif is_update:
            self.update_calls += 1
            if self.last_update_step is None:
                self.last_update_step = self.trigger_step
                content = json.dumps({
                    "update_type": "code_file",
                    "target": "configs/runtime_rules.json",
                    "reason": "Reduce stuck threshold so the agent escapes obstacles faster in this level",
                    "confidence": 0.95,
                    "payload": {
                        "file_path": str(RUNTIME_RULES),
                        "search": '"stuck_escape_threshold": 5',
                        "replace": '"stuck_escape_threshold": 3',
                    },
                })
            else:
                content = json.dumps({"update_type": "none", "confidence": 0.0})
        else:
            content = json.dumps({"update_type": "none", "confidence": 0.0})

        class _Message:
            content: str = ""

        msg = _Message()
        msg.content = content

        class _Choice:
            message = msg

        class _RespLocal:
            choices = [_Choice()]

        return _RespLocal()

    def chat_with_vision(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        raise NotImplementedError


def _restore_runtime_rules(original_text: str) -> None:
    RUNTIME_RULES.write_text(original_text, encoding="utf-8")
    logger.info("Restored %s", RUNTIME_RULES)


def run_experiment(
    game_id: str = "SSD_00461P01",
    max_steps: int = 30,
    trigger_step: int = 5,
) -> dict[str, Any]:
    """Run the code-file rule-update experiment and return metrics."""
    original_text = RUNTIME_RULES.read_text(encoding="utf-8")
    # Clean any stale backup dir so we can verify the applier created it.
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)

    game_dir = PROCESSED_ROOT / game_id
    if not game_dir.exists():
        return {"error": f"processed run not found: {game_dir}"}

    steps = load_steps(game_dir)[:max_steps]
    rule_params = RuleParameters()
    rule_engine = RuleEngine(game_id, rule_params=rule_params)
    client = MockCodeFileUpdateClient(trigger_step=trigger_step)

    planner = HierarchicalPlanner(
        rule_engine=rule_engine,
        api_client=client,
        l1_interval=0,  # L1 disabled for this test
        l2_interval=99,  # only trigger via rule-update path
        stuck_threshold=3,
        rule_params=rule_params,
        rule_update_allowlist=[str(RUNTIME_RULES)],
    )

    prev_after_state: dict[str, Any] | None = None
    last_composite = 0.0
    stall_streak = 0
    conflict_streak = 0

    records: list[dict[str, Any]] = []
    applied_step: int | None = None
    threshold_before = rule_engine._param("stuck_escape_threshold", 999)

    try:
        for step_record in steps:
            step_num = int(step_record.get("step", 0))

            before_raw = load_state(game_dir, step_record.get("before", {}).get("state"))
            if before_raw is None and prev_after_state is not None:
                before_raw = prev_after_state
            if before_raw is None:
                continue

            state = adapt_processed_state(before_raw)
            action_record = load_state(game_dir, step_record.get("action"))
            true_action = adapt_ground_truth(action_record or {"type": "wait"})
            if true_action is None:
                true_action = {"action": "wait", "params": {"duration_ms": 500}, "reason": "unparseable-gt"}

            ctx = build_fake_context(
                step_number=step_num,
                state=state,
                last_composite=last_composite,
                stall_streak=stall_streak,
                conflict_streak=conflict_streak,
                visual_struct=None,
                metadata={"game_id": game_id},
            )

            t0 = time.perf_counter()
            pred_action = planner.step(ctx)
            latency_ms = (time.perf_counter() - t0) * 1000

            type_match, action_match, cos = actions_match(pred_action, true_action)

            if action_match:
                stall_streak = 0
                conflict_streak = 0
            else:
                if pred_action.get("action") == "wait" and true_action.get("action") != "wait":
                    stall_streak += 1
                if pred_action.get("action") != true_action.get("action"):
                    conflict_streak += 1

            composite = 0.0
            if action_match:
                composite = 1.0
            elif type_match and true_action.get("action") == "move":
                composite = 0.6 + 0.4 * max(0.0, cos)
            elif type_match:
                composite = 0.6
            last_composite = composite

            # Detect application by reading the file directly.
            threshold_now = rule_engine._param("stuck_escape_threshold", 999)
            if applied_step is None and threshold_now == 3:
                applied_step = step_num

            records.append({
                "step": step_num,
                "threshold_read": threshold_now,
                "type_match": type_match,
                "action_match": action_match,
                "latency_ms": round(latency_ms, 2),
            })

            after_raw = load_state(game_dir, step_record.get("after", {}).get("state"))
            if after_raw is not None:
                prev_after_state = after_raw
    finally:
        _restore_runtime_rules(original_text)

    stats = planner.stats()
    backup_created = (BACKUP_DIR / "runtime_rules.json.0.bak").is_file()

    # Reload engine to make sure post-restore value is back to 5.
    restored_engine = RuleEngine(game_id)
    threshold_after_restore = restored_engine._param("stuck_escape_threshold", 999)

    return {
        "game_id": game_id,
        "max_steps": max_steps,
        "trigger_step": trigger_step,
        "threshold_before": threshold_before,
        "threshold_applied_step": applied_step,
        "threshold_after_restore": threshold_after_restore,
        "rule_update_history": stats.get("rule_update_history", []),
        "l2_update_calls": stats.get("l2_update_calls", 0),
        "backup_created": backup_created,
        "backup_path": str(BACKUP_DIR / "runtime_rules.json.0.bak"),
        "records": records,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    result = run_experiment()
    output_path = ROOT / "experiment_code_file_rule_update.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved results to %s", output_path)

    ok = (
        result.get("threshold_before") == 5
        and result.get("threshold_applied_step") is not None
        and result.get("threshold_after_restore") == 5
        and result.get("backup_created")
        and result.get("l2_update_calls", 0) >= 1
    )
    if ok:
        logger.info("Code-file rule update experiment: PASSED")
    else:
        logger.error("Code-file rule update experiment: FAILED")
        logger.error(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
