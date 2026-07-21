#!/usr/bin/env python3
"""Debug L2 rule update response for a given provider."""
from __future__ import annotations

import os
from src.agent.api_client import MultiProviderClient
from src.agent.rule_update import RuleParameters, update_prompt, parse_update_response

provider = os.environ.get("PROVIDER", "xiaomi")
client = MultiProviderClient(provider=provider)

params = RuleParameters({"coin_save_buffer": 0.0, "stuck_escape_threshold": 5})
state = {
    "player": {"worldPosition": {"x": 1.0, "z": 2.0}},
    "keyNumbers": {"money": 10, "_failCount": 0},
    "keyFlags": {},
    "guide_or_target_candidates": [{"name": "UnlockItem_1", "path": "Canvas/UnlockItem_1"}],
}

messages = update_prompt(
    trigger_reason="stall_streak_5",
    state=state,
    params=params.to_dict(),
    visual_context={},
)

print(f"Provider: {provider}")
print(f"Model: {client._text_model}")
print("Messages:")
for m in messages:
    print(f"  {m['role']}: {m['content'][:200]}...")

resp = client.chat(messages, max_tokens=512, temperature=0.0)
text = resp.choices[0].message.content or ""
print(f"\nRaw response ({len(text)} chars):")
print(text)
print("\nParsed:", parse_update_response(text))
