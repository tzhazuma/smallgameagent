"""Multi-mode agent that supports all 6 game-playing modes.

Mode selection::

    agent = HybridAgent(mode="api", game_id="SSD_00848P01")
    result = await agent.run_game("path/to/game.html")

Available modes:

- ``"api"`` — Direct API (DeepSeek + Mimo) — uses ``LLMAgent`` internally
- ``"vlm"`` — Direct VLM (local Qwen/Gemma with LoRA)
- ``"vlm-struct"`` — VLM → structured state → API text → action (Mode 3)
- ``"vlm-rule"`` — VLM → rules → rule engine → action (Mode 4)
- ``"api-rule"`` — API → rules → rule engine → action (Mode 5)
- ``"rule"`` — Pure rule engine → action (Mode 6)
"""

from __future__ import annotations

import asyncio
import json
import logging
from io import BytesIO
from pathlib import Path
from typing import Any

from src.agent.harness import GameRunner
from src.agent.llm_agent import LLMAgent
from src.agent.probe_adapter import ProbeAdapter
from src.agent.visual_analyzer import VisualAnalyzer
from src.engine.rules import RuleEngine

logger = logging.getLogger(__name__)

_VALID_MODES = frozenset({
    "api", "vlm", "vlm-struct", "vlm-rule", "api-rule", "rule",
    "vlm-struct-api-rule",
})


class HybridAgent:
    """Agent that supports multiple game-playing modes.

    Parameters
    ----------
    mode:
        One of ``"api"``, ``"vlm"``, ``"vlm-struct"``, ``"vlm-rule"``,
        ``"api-rule"``, ``"rule"``.
    game_id:
        Game identifier (e.g. ``"SSD_00848P01"``) for profile lookup.
    api_client:
        ``OpenCodeGoClient`` instance (required for API-based modes).
    vlm_engine:
        ``GameAgentInference`` instance (required for VLM-based modes).
    config:
        Optional config dict passed through to ``LLMAgent``.
    """

    def __init__(
        self,
        mode: str = "api",
        game_id: str | None = None,
        api_client=None,
        vlm_engine=None,
        config: dict[str, Any] | None = None,
    ) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(f"Invalid mode: {mode}. Valid: {sorted(_VALID_MODES)}")
        self.mode = mode
        self.game_id = game_id
        self._api_client = api_client
        self._vlm_engine = vlm_engine
        self._config = config or {}

        # Sub-components
        self._llm_agent: LLMAgent | None = None
        self._rule_engine: RuleEngine | None = None
        self._visual_analyzer: VisualAnalyzer | None = None

        if mode == "api" and api_client:
            self._llm_agent = LLMAgent(api_client, config)

        if mode in ("rule", "vlm-rule", "api-rule") and game_id:
            self._rule_engine = RuleEngine(game_id)
            self._visual_analyzer = VisualAnalyzer(api_client) if api_client else None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run_game(
        self,
        game_path: str | Path,
        max_steps: int = 200,
        headed: bool = False,
    ) -> dict[str, Any]:
        """Open *game_path* and run the observe-think-act loop.

        Parameters
        ----------
        game_path:
            Path to a Cocos Creator HTML game file.
        max_steps:
            Maximum number of observation/action cycles.
        headed:
            Show the browser window (True) or run headless (False).

        Returns
        -------
        Summary dict with ``completed``, ``win``, ``steps``, ``reason``.
        """
        logger.info(
            "Starting game: %s  mode=%s  game_id=%s  headed=%s",
            game_path, self.mode, self.game_id, headed,
        )

        result_summary: dict[str, Any] = {
            "completed": False, "win": False, "steps": 0, "reason": "",
            "mode": self.mode,
        }

        runner = GameRunner(headed=headed)
        probe = ProbeAdapter()

        try:
            await runner.start()
            await runner.open_game(game_path)
            await asyncio.sleep(1.5)

            await probe.inject(runner._page)
            state = await probe.wait_for_ready(
                runner._page,
                timeout_ms=self._config.get("probe_timeout_ms", 18_000),
                poll_interval_ms=500,
            )
            if not state.get("ready"):
                result_summary["reason"] = "Probe never reported ready"
                return result_summary

            for step in range(max_steps):
                # ---- Observe ----
                state = await self._observe(probe, runner)
                if self._is_finished(state, result_summary):
                    break

                # ---- Screenshot ----
                screenshot_bytes = None
                try:
                    screenshot_bytes = await runner.screenshot()
                except Exception:
                    logger.warning("Screenshot failed at step %d", step)

                # ---- Decide ----
                decision = await self._decide(state, screenshot_bytes)

                # ---- Act ----
                await self._execute(decision, runner)

                # ---- Cooldown ----
                await asyncio.sleep(0.05)

            result_summary["steps"] = step + 1

        except asyncio.CancelledError:
            result_summary["reason"] = "Cancelled"
            raise
        except Exception:
            logger.exception("Agent loop crashed")
            result_summary["reason"] = "Exception in agent loop"
        finally:
            await runner.close()

        return result_summary

    # ------------------------------------------------------------------
    # Decision layer — dispatches to the selected mode
    # ------------------------------------------------------------------

    async def _decide(
        self,
        state: dict[str, Any],
        screenshot_bytes: bytes | None,
    ) -> dict[str, Any]:
        """Produce an action dict using the selected mode."""
        if self.mode == "api":
            return await self._decide_api(state, screenshot_bytes)
        elif self.mode == "vlm":
            return await self._decide_vlm(state, screenshot_bytes)
        elif self.mode == "vlm-struct":
            return await self._decide_vlm_struct(state, screenshot_bytes)
        elif self.mode == "vlm-rule":
            return await self._decide_vlm_rule(state, screenshot_bytes)
        elif self.mode == "api-rule":
            return await self._decide_api_rule(state, screenshot_bytes)
        elif self.mode == "rule":
            return await self._decide_rule(state, screenshot_bytes)
        elif self.mode == "vlm-struct-api-rule":
            return await self._decide_vlm_struct_api_rule(state, screenshot_bytes)
        return {"action": "wait", "params": {"duration_ms": 500}, "reason": "unknown_mode"}

    # --- Mode 1: Direct API ---
    async def _decide_api(
        self, state: dict[str, Any], screenshot_bytes: bytes | None,
    ) -> dict[str, Any]:
        if self._llm_agent is None:
            return {"action": "wait", "params": {"duration_ms": 500}, "reason": "no_api_client"}
        return await self._llm_agent._think_text(state, [])

    # --- Mode 2: Direct VLM ---
    async def _decide_vlm(
        self, state: dict[str, Any], screenshot_bytes: bytes | None,
    ) -> dict[str, Any]:
        if self._vlm_engine is None or screenshot_bytes is None:
            return {"action": "wait", "params": {"duration_ms": 500}, "reason": "no_vlm"}
        from PIL import Image
        loop = asyncio.get_running_loop()
        pil = Image.open(BytesIO(screenshot_bytes)).convert("RGB")
        try:
            result = await loop.run_in_executor(None, self._vlm_engine.predict, pil, state)
            return {
                "action": result.get("action", "wait"),
                "params": result.get("params", {"duration_ms": 500}),
                "reason": result.get("reason", "vlm"),
            }
        except Exception as exc:
            logger.warning("VLM predict failed: %s", exc)
            return {"action": "wait", "params": {"duration_ms": 500}, "reason": f"vlm_error:{exc}"}

    # --- Mode 3: VLM → Struct → API Text → Action ---
    async def _decide_vlm_struct(
        self, state: dict[str, Any], screenshot_bytes: bytes | None,
    ) -> dict[str, Any]:
        if self._vlm_engine is None or self._llm_agent is None or screenshot_bytes is None:
            return {"action": "wait", "params": {"duration_ms": 500}, "reason": "no_vlm_or_api"}
        from PIL import Image
        from src.inference.struct_extractor import extract_visual_structure

        pil = Image.open(BytesIO(screenshot_bytes)).convert("RGB")
        loop = asyncio.get_running_loop()
        visual_struct = await loop.run_in_executor(
            None, extract_visual_structure, self._vlm_engine.predict, pil, state,
        )
        # Enrich state with VLM visual structure
        enriched_state = dict(state)
        enriched_state["_visual_struct"] = visual_struct
        return await self._llm_agent._think_text(enriched_state, [])

    # --- Mode 4: VLM → Rules → Rule Engine ---
    async def _decide_vlm_rule(
        self, state: dict[str, Any], screenshot_bytes: bytes | None,
    ) -> dict[str, Any]:
        if self._vlm_engine is None or self._rule_engine is None or screenshot_bytes is None:
            return {"action": "wait", "params": {"duration_ms": 500}, "reason": "no_vlm_or_engine"}
        from PIL import Image
        from src.inference.rule_extractor import extract_rules_from_vlm

        pil = Image.open(BytesIO(screenshot_bytes)).convert("RGB")
        loop = asyncio.get_running_loop()
        ruleset = await loop.run_in_executor(
            None, extract_rules_from_vlm, self._vlm_engine.predict, pil, state,
        )
        if ruleset.rules:
            self._rule_engine.driver_type = ruleset.driver_type or self._rule_engine.driver_type
        return self._rule_engine.step(state)

    # --- Mode 5: API → Rules → Rule Engine ---
    async def _decide_api_rule(
        self, state: dict[str, Any], screenshot_bytes: bytes | None,
    ) -> dict[str, Any]:
        if self._llm_agent is None or self._rule_engine is None:
            return {"action": "wait", "params": {"duration_ms": 500}, "reason": "no_api_or_engine"}
        from src.inference.rule_extractor import extract_rules_from_api

        ruleset = extract_rules_from_api(
            text_api_fn=lambda s, h: self._llm_agent._think_text(s, h),
            vision_api_fn=lambda sp: self._llm_agent._think_vision(sp) if sp else None,
            screenshot=screenshot_bytes,
            probe_state=state,
        )
        if ruleset.rules:
            self._rule_engine.driver_type = ruleset.driver_type or self._rule_engine.driver_type
        return self._rule_engine.step(state)

    # --- Mode 7: VLM → Struct → API → Rules → Rule Engine ---
    _RULE_GEN_PROMPT = (
        "You are a game strategy analyst. Based on the game state and visual analysis below, "
        "output structured gameplay rules as a JSON list. Each rule must have: "
        "name, priority (0-10), condition, action_type (move/tap/wait), "
        "and action_params.\n\n"
        "## Visual Analysis\n{visual_struct}\n\n"
        "## Game State\n{state_json}\n\n"
        "## Output Format\n"
        '```json\n{"rules": [\n'
        '  {"name": "...", "priority": 8, "condition": "...", '
        '"action_type": "move", "action_params": {"dx": 0, "dy": 1, "duration_ms": 320}},\n'
        '  ...\n]}\n```\n'
        "Output ONLY the JSON."
    )

    async def _decide_vlm_struct_api_rule(
        self, state: dict[str, Any], screenshot_bytes: bytes | None,
    ) -> dict[str, Any]:
        if self._vlm_engine is None or self._llm_agent is None or self._rule_engine is None:
            return {"action": "wait", "params": {"duration_ms": 500}, "reason": "missing_component"}
        from PIL import Image
        import json

        pil = Image.open(BytesIO(screenshot_bytes)).convert("RGB")
        loop = asyncio.get_running_loop()

        # Step 1: VLM extracts visual structure
        from src.inference.struct_extractor import extract_visual_structure
        visual_struct = await loop.run_in_executor(
            None, extract_visual_structure, self._vlm_engine.predict, pil, state,
        )

        # Step 2: API (DeepSeek) generates rules from struct + state
        prompt = self._RULE_GEN_PROMPT.format(
            visual_struct=json.dumps(visual_struct, indent=2, ensure_ascii=False),
            state_json=json.dumps(state, indent=2, default=str, ensure_ascii=False)[:2000],
        )
        api_messages = [{"role": "user", "content": prompt}]
        try:
            resp = self._llm_agent._client.chat(
                api_messages, model="deepseek-v4-flash", max_tokens=1024, temperature=0.0
            )
            raw = resp.choices[0].message.content
            # Parse rules from API response
            import re
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                parsed = json.loads(match.group(0))
                rules_raw = parsed.get("rules", [])
                if rules_raw:
                    from src.engine.rules import GameRule, RuleSet
                    rules = []
                    for r in rules_raw:
                        rules.append(GameRule(
                            name=r.get("name", "api_rule"),
                            priority=r.get("priority", 0),
                            condition=r.get("condition", ""),
                            action_template={
                                "action": r.get("action_type", "wait"),
                                "params": r.get("action_params", {"duration_ms": 500}),
                            },
                        ))
                    ruleset = RuleSet(
                        game_id=self.game_id or "unknown",
                        driver_type=self._rule_engine.driver_type,
                        rules=rules,
                        source="vlm_struct_api_rule",
                    )
                    driver_type = ruleset.metadata.get("game_mechanics")
                    if driver_type:
                        self._rule_engine.driver_type = driver_type
        except Exception as exc:
            logger.warning("API rule generation failed: %s", exc)

        # Step 3: Rule Engine executes
        return self._rule_engine.step(state)

    # --- Mode 6: Pure Rule Engine ---
    async def _decide_rule(
        self, state: dict[str, Any], screenshot_bytes: bytes | None,
    ) -> dict[str, Any]:
        if self._rule_engine is None:
            return {"action": "wait", "params": {"duration_ms": 500}, "reason": "no_rule_engine"}
        visual = None
        if screenshot_bytes and self._visual_analyzer:
            try:
                from PIL import Image
                from io import BytesIO
                pil = Image.open(BytesIO(screenshot_bytes)).convert("RGB")
                visual = self._visual_analyzer.analyze(pil)
            except Exception:
                pass
        return self._rule_engine.step(state, visual)

    # ------------------------------------------------------------------
    # Observation / execution helpers
    # ------------------------------------------------------------------

    async def _observe(self, probe: ProbeAdapter, runner: GameRunner) -> dict[str, Any]:
        from src.agent.probe_adapter import ProbeAdapter  # type: ignore[no-redef]
        for attempt in range(4):
            state = await probe.observe_fast(runner._page)
            if isinstance(state, dict) and state.get("ready"):
                return state
            if attempt < 3:
                await probe.inject(runner._page)
                await asyncio.sleep(1.0)
        return {"ready": False}

    @staticmethod
    def _is_finished(state: dict[str, Any], summary: dict[str, Any]) -> bool:
        if state.get("win"):
            summary["completed"] = True
            summary["win"] = True
            summary["reason"] = "Win condition detected"
            return True
        if state.get("done"):
            summary["completed"] = True
            summary["win"] = False
            summary["reason"] = state.get("doneReason", "Game ended")
            return True
        return False

    @staticmethod
    async def _execute(decision: dict[str, Any], runner: GameRunner) -> None:
        action = decision.get("action", "wait")
        params = decision.get("params", {})
        try:
            if action == "move":
                await runner.joystick_pulse(
                    dx=params.get("dx", 0),
                    dy=params.get("dy", 0),
                    duration_ms=params.get("duration_ms", 320),
                )
            elif action == "tap":
                await runner.tap(
                    x=params.get("x", 0),
                    y=params.get("y", 0),
                    duration_ms=params.get("duration_ms", 100),
                )
            else:
                await runner.wait(params.get("duration_ms", 500))
        except Exception as exc:
            logger.warning("Action execution failed: %s", exc)
