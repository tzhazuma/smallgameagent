#!/usr/bin/env python3
"""End-to-end verification of rule-update wiring using a mock L2 cloud client.

This script proves that:
1. HierarchicalPlanner triggers L2 rule updates when stall/composite thresholds are crossed;
2. L2 param updates are written to the shared RuleParameters;
3. RuleEngine reads those parameters and changes its behavior;
4. The rule update history is recorded in ctx.metadata.

Usage::

    PYTHONPATH=. python src/experiments/exp_rule_update_mock.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from src.agent.hybrid_agent import HybridAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class MockRuleUpdateClient:
    """Fake cloud client that always suggests coin_save_buffer=15."""

    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict], **kwargs):
        self.calls.append(messages)

        class _Choice:
            class _Message:
                content = json.dumps({
                    "update_type": "param",
                    "target": "rules.coin_save_buffer",
                    "reason": "Mock L2: agent should save more coins before upgrading",
                    "payload": {"coin_save_buffer": 15.0},
                    "confidence": 0.95,
                })
            message = _Message()

        class _Resp:
            choices = [_Choice()]

        return _Resp()

    def chat_with_vision(self, messages: list[dict], **kwargs):
        raise NotImplementedError


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", default="SSD_00461P01_EN_WNK_20260116_RBN_Applovin_塔防来着^有埋点.html")
    parser.add_argument("--max-steps", type=int, default=15)
    args = parser.parse_args()

    html_path = Path(args.html)
    if not html_path.exists():
        print(f"HTML not found: {html_path}", file=__import__("sys").stderr)
        return 1

    client = MockRuleUpdateClient()
    agent = HybridAgent(
        mode="hierarchical",
        game_id="SSD_00461P01",
        api_client=client,
        config={
            "l1_interval": 0,
            "l2_interval": 99999,
            "stuck_threshold": 3,
        },
    )
    result = await agent.run_game(str(html_path), max_steps=args.max_steps, headed=False)

    last_step = (result.get('step_log') or [{}])[-1]
    print(f"completed={result.get('completed')} win={result.get('win')} steps={result.get('steps')} "
          f"composite={last_step.get('composite', 'N/A')}")
    print(f"reason={result.get('reason')}")
    print(f"L2 mock calls: {len(client.calls)}")

    stats = (result.get("ctx_metadata") or {}).get("hierarchical_stats") or {}
    print("Rule params:", stats.get("rule_params"))
    print("Rule update history:", json.dumps(stats.get("rule_update_history", []), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
