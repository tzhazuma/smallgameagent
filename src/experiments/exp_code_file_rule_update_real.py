#!/usr/bin/env python3
"""Real-cloud L2 code-file rule update experiment.

This script asks a real cloud API (qwen/kimi/xiaomi) to produce a structured
code-file update for ``configs/runtime_rules.json``.  It constructs a synthetic
"stuck" context from ``processed-runs/SSD_00461P01`` and verifies that the model
can correctly patch the runtime config file.

Usage::

    . .env && PYTHONPATH=. .venv/bin/python -B \
        src/experiments/exp_code_file_rule_update_real.py --provider qwen
"""

from __future__ import annotations

import argparse
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

from src.agent.api_client import MultiProviderClient  # noqa: E402
from src.agent.rule_update import (  # noqa: E402
    RuleUpdateApplier,
    RuleParameters,
    parse_update_response,
)
from src.engine.rules import RuleEngine  # noqa: E402
from src.experiments.offline_replay import (  # noqa: E402
    adapt_processed_state,
    load_state,
    load_steps,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIME_RULES = ROOT / "configs" / "runtime_rules.json"
BACKUP_DIR = ROOT / "configs" / ".rule_backups"


def _build_state_summary(game_id: str = "SSD_00461P01", step_idx: int = 10) -> dict[str, Any]:
    """Load a processed-run state and build a compact prompt context."""
    game_dir = ROOT / "processed-runs" / game_id
    steps = load_steps(game_dir)
    if not steps:
        raise ValueError(f"no steps for {game_id}")
    step_record = steps[min(step_idx, len(steps) - 1)]
    before_raw = load_state(game_dir, step_record.get("before", {}).get("state"))
    if before_raw is None:
        raise ValueError("no before state")
    state = adapt_processed_state(before_raw)
    player = state.get("player") or {}
    key_numbers = state.get("keyNumbers", {})
    return {
        "player_pos": (player.get("worldPosition") or {}).get("x", 0),
        "key_numbers": {k: v for k, v in key_numbers.items() if not k.startswith("_")} or key_numbers,
        "candidate_count": len(state.get("guide_or_target_candidates", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Real-cloud L2 code-file rule update experiment.")
    parser.add_argument("--provider", default="qwen", choices=["qwen", "kimi", "xiaomi", "opencodego"],
                        help="Cloud provider for L2.")
    parser.add_argument("--game", default="SSD_00461P01", help="Processed-run game ID.")
    parser.add_argument("--step-idx", type=int, default=10, help="Which step state to use as context.")
    parser.add_argument("--verbose", action="store_true", help="Debug logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    original_text = RUNTIME_RULES.read_text(encoding="utf-8")
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)

    try:
        state_summary = _build_state_summary(args.game, args.step_idx)
        params = RuleParameters({"stuck_escape_threshold": 5, "coin_save_buffer": 0})
        applier = RuleUpdateApplier(
            params,
            code_file_allowlist=[str(RUNTIME_RULES)],
        )

        # Build a clean prompt.  For Xiaomi/mimo we include the exact expected
        # JSON template as a few-shot example, because the model truncates or
        # returns empty with open-ended JSON instructions.
        if args.provider == "xiaomi":
            user_text = (
                "The agent has been stuck for 5 consecutive steps. "
                "Update configs/runtime_rules.json to lower stuck_escape_threshold from 5 to 3.\n\n"
                "Allowed file: /home/azuma/Downloads/smallgameagent/configs/runtime_rules.json\n"
                "Current stuck_escape_threshold: 5\n"
                "Proposed change: 3\n\n"
                "Output ONLY valid JSON (no markdown fences):\n"
                "{\n"
                '  "update_type": "code_file",\n'
                '  "target": "configs/runtime_rules.json",\n'
                '  "reason": "Lower stuck threshold to escape faster",\n'
                '  "payload": {\n'
                '    "file_path": "/home/azuma/Downloads/smallgameagent/configs/runtime_rules.json",\n'
                '    "search": "\\"stuck_escape_threshold\\": 5",\n'
                '    "replace": "\\"stuck_escape_threshold\\": 3"\n'
                "  },\n"
                '  "confidence": 0.95\n'
                "}"
            )
            messages = [
                {"role": "system", "content": "You are a strategy optimizer. Output only valid JSON."},
                {"role": "user", "content": user_text},
            ]
        else:
            user_content = {
                "trigger_reason": "stall_streak_5",
                "state_summary": state_summary,
                "current_params": params.to_dict(),
                "allowlisted_files": [str(RUNTIME_RULES)],
                "instruction": (
                    "The agent has been stuck for 5 consecutive steps. "
                    "Propose a code_file update to 'configs/runtime_rules.json' that lowers "
                    "'stuck_escape_threshold' from 5 to 3 so the agent escapes obstacles faster. "
                    "Use exact search/replace on the JSON file. Output only the JSON object."
                ),
            }
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a strategy optimizer for a small-game-playing agent. "
                        "Output a single JSON object (no markdown fences, no thinking) with this schema:\n"
                        '{"update_type": "code_file", '
                        '"target": "configs/runtime_rules.json", '
                        '"reason": "why this update helps", '
                        '"payload": {"file_path": "...", "search": "...", "replace": "..."}, '
                        '"confidence": 0.0-1.0}\n\n'
                        "Only propose code_file updates to the allowlisted file. "
                        "Make search/replace exact and minimal."
                    ),
                },
                {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
            ]

        client = MultiProviderClient(provider=args.provider)
        logger.info("Calling %s for code-file rule update...", args.provider)
        t0 = time.time()
        resp = client.chat(messages, max_tokens=1024, temperature=0.0)
        latency_s = time.time() - t0
        text = resp.choices[0].message.content or ""
        logger.info("L2 raw response (%d chars, %.2fs):\n%s", len(text), latency_s, text[:800])

        request = parse_update_response(text)
        result: dict[str, Any] = {
            "provider": args.provider,
            "latency_s": round(latency_s, 2),
            "raw": text,
            "parsed": request.to_dict() if request else None,
        }

        if request is None:
            logger.error("Failed to parse L2 response as RuleUpdateRequest")
            result["status"] = "parse_failed"
        else:
            applied = applier.apply(request)
            result["applied"] = applied
            result["pending_reason"] = None
            if not applied:
                pending = applier.pending_code_updates
                if pending:
                    result["pending_reason"] = pending[-1].get("pending_reason")
            result["status"] = "applied" if applied else "not_applied"

            # Verify the engine reads the new value (if changed).
            engine = RuleEngine(args.game)
            new_threshold = engine._param("stuck_escape_threshold", 999)
            result["threshold_after"] = new_threshold
            if new_threshold != 5:
                result["engine_reads_new_value"] = True
            else:
                result["engine_reads_new_value"] = False

        output_path = ROOT / f"experiment_code_file_rule_update_real_{args.provider}.json"
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Saved results to %s", output_path)

        ok = result.get("status") == "applied" and result.get("engine_reads_new_value")
        if ok:
            logger.info("Real-cloud code-file update experiment: PASSED")
        else:
            logger.warning("Real-cloud code-file update experiment: did not fully succeed")
        return 0

    finally:
        RUNTIME_RULES.write_text(original_text, encoding="utf-8")
        logger.info("Restored %s", RUNTIME_RULES)


if __name__ == "__main__":
    raise SystemExit(main())
