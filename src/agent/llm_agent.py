"""LLM-driven agent loop — observe, think (text + vision), then act.

Replaces hardcoded strategy profiles with a central LLM-based decision loop
that inspects structured game state (via the Cocos probe) and screenshot
frames (via the browser harness), delegates reasoning to DeepSeek-v4-flash
(text) and Mimo-v2.5 (vision), and dispatches actions through CDP touch
control.

Typical usage::

    client = OpenCodeGoClient()
    agent = LLMAgent(client)
    result = await agent.run_game("/path/to/game.html", max_steps=200)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .api_client import OpenCodeGoClient
from .dataset_writer import DatasetWriter
from .harness import GameRunner
from .probe_adapter import ProbeAdapter

if TYPE_CHECKING:
    from .context import AgentContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action format constants
# ---------------------------------------------------------------------------

_VALID_ACTIONS = frozenset({"move", "tap", "wait"})

# Each action dict must contain "action", optional "params", and "reason".
# Move params:  {"dx": -1..1, "dy": -1..1, "duration_ms": 320}
# Tap params:   {"x": float, "y": float, "duration_ms": 100}
# Wait params:  {"duration_ms": int}


class LLMAgent:
    """Central agent that observes a Cocos2D game and decides actions via LLMs.

    Parameters
    ----------
    api_client:
        An :class:`OpenCodeGoClient` for text (DeepSeek) and vision (Mimo) calls.
    config:
        Optional configuration dict with overrides for timeouts, model names,
        game profiles, etc.  When ``None`` sensible defaults are used.
    """

    # ------------------------------------------------------------------
    # Prompt templates
    # ------------------------------------------------------------------

    TEXT_PROMPT = (
        "You are an AI agent playing a mobile game.  You receive the current "
        "game state as JSON and must output a single JSON action to progress "
        "toward winning.\n\n"
        "## State Format\n"
        'The state dict contains: "ready", "done", "win", "player", '
        '"keyNumbers", "keyFlags", "guideSummary", and optional chain data.\n\n'
        "## Actions\n"
        "- move:  Joystick drag.  params: "
        '{{"dx": float(-1..1), "dy": float(-1..1), "duration_ms": 320}}\n'
        "- tap:   Screen tap.   params: "
        '{{"x": float, "y": float, "duration_ms": 100}}\n'
        '- wait:  Do nothing.   params: {{"duration_ms": int}}\n\n'
        "## Output Format\n"
        "Respond with **only** valid JSON, no markdown fences, no commentary:\n"
        '{{"action": "<action>", "params": {{...}}, "reason": "..."}}\n\n'
        "## History (most recent first)\n"
        "{history}\n\n"
        "## Current State\n"
        "{state}\n\n"
        "What is the best next action?"
    )

    VISION_PROMPT = (
        "You are a visual assistant for an AI playing a mobile game.  Analyze "
        "this screenshot and return JSON describing what you see.\n\n"
        "Look for:\n"
        "- Arrows / guide indicators pointing in a direction\n"
        "- Targets / highlighted objects / sparkling items to collect\n"
        "- Obstacles or walls blocking the player\n"
        "- End-screen / win / lose UI elements\n"
        "- UI buttons (continue, retry, next-level)\n\n"
        "## Output Format\n"
        "Respond with **only** valid JSON:\n"
        '{"has_arrow": true|false, "arrow_direction": '
        '"up"|"down"|"left"|"right"|"none", '
        '"has_target": true|false, "target_visible": true|false, '
        '"has_obstacle": true|false, "is_end_screen": true|false, '
        '"ui_buttons": ["..."], "extra_notes": "..."}'
    )

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        api_client: OpenCodeGoClient,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._client = api_client
        self._config = config or {}

        # Resolve model names with config overrides.
        self._text_model = self._config.get("text_model", "deepseek-v4-flash")
        self._vision_model = self._config.get("vision_model", "mimo-v2.5")
        self._max_steps_default = self._config.get("max_steps", 200)
        self._probe_timeout_ms = self._config.get("probe_timeout_ms", 18_000)
        self._probe_retry_delay_ms = self._config.get("probe_retry_delay_ms", 1_000)
        self._max_json_retries = self._config.get("max_json_retries", 2)
        self._step_cooldown_ms = self._config.get("step_cooldown_ms", 50)

        # Optional dataset collection.
        self._collect_dataset = self._config.get("collect_dataset", False)
        self._dataset: list[dict[str, Any]] = []
        self._dataset_writer: DatasetWriter | None = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run_game(
        self,
        game_path: str | Path,
        max_steps: int | None = None,
        headed: bool = True,
    ) -> dict[str, Any]:
        """Open *game_path* and run the observe-think-act loop until done.

        Parameters
        ----------
        game_path:
            Path to a Cocos Creator HTML game file.
        max_steps:
            Maximum number of observation/action cycles (default from config or 200).
        headed:
            Show the browser window (``True``) or run headless (``False``).

        Returns
        -------
        dict
            Summary dict with keys: ``completed``, ``win``, ``steps``,
            ``reason``, ``dataset`` (if collection enabled).
        """
        steps = max_steps if max_steps is not None else self._max_steps_default
        history: list[dict[str, Any]] = []
        result_summary: dict[str, Any] = {
            "completed": False,
            "win": False,
            "steps": 0,
            "reason": "",
        }

        runner = GameRunner(headed=headed)
        probe = ProbeAdapter()

        try:
            await runner.start()
            await runner.open_game(game_path)

            # Start dataset writer when collection is enabled.
            if self._collect_dataset and self._dataset_writer is None:
                game_id = Path(game_path).stem
                dataset_output = self._config.get("dataset_output_dir", "./collected_datasets")
                self._dataset_writer = DatasetWriter(output_dir=dataset_output)
                self._dataset_writer.start_session(game_id)

            await asyncio.sleep(1.5)  # let the game boot

            # Inject probe and wait for readiness.
            await probe.inject(runner._page)
            state = await probe.wait_for_ready(
                runner._page,
                timeout_ms=self._probe_timeout_ms,
                poll_interval_ms=500,
            )
            if not state.get("ready"):
                result_summary["reason"] = "Probe never reported ready"
                return result_summary

            for step in range(steps):
                # ---- 1. OBSERVE ----
                state = await self._observe(probe, runner)
                if self._is_finished(state, result_summary):
                    break

                # ---- 2. THINK-TEXT ----
                screenshot_path = None
                try:
                    screenshot_bytes = await runner.screenshot()
                    screenshot_path = self._temp_screenshot_path(step)
                    Path(screenshot_path).write_bytes(screenshot_bytes)
                except Exception:
                    logger.warning("Screenshot capture failed at step %d", step, exc_info=True)

                text_response = await self._think_text(state, history)

                # ---- 3. THINK-VISION ----
                vision_response = None
                if screenshot_path is not None:
                    try:
                        vision_response = await self._think_vision(screenshot_path)
                    except Exception:
                        logger.warning(
                            "Vision call failed at step %d, continuing with text only",
                            step,
                            exc_info=True,
                        )

                # ---- 4. FUSE ----
                decision = self._fuse_decisions(text_response, vision_response)

                # ---- 5. ACT ----
                await self._execute(decision, runner, state)

                # ---- 6. Record & cooldown ----
                history.append(
                    {
                        "step": step,
                        "state_summary": self._summarize_state(state),
                        "decision": decision,
                    }
                )
                if len(history) > 20:
                    history = history[-20:]

                if self._collect_dataset:
                    self._collect_dataset_step(state, screenshot_path, decision)

                await asyncio.sleep(self._step_cooldown_ms / 1000.0)

            result_summary["steps"] = step + 1  # type: ignore[assignment]

        except asyncio.CancelledError:
            result_summary["reason"] = "Cancelled"
            raise
        except Exception:
            logger.exception("Agent loop crashed")
            result_summary["reason"] = "Exception in agent loop"
        finally:
            await self._cleanup_runner(runner)
            if self._collect_dataset:
                result_summary["dataset"] = self._dataset
            if self._dataset_writer is not None:
                dataset_path = self._dataset_writer.end_session()
                if dataset_path is not None:
                    result_summary["dataset_path"] = str(dataset_path)

        return result_summary

    # ------------------------------------------------------------------
    # Observe / completion check
    # ------------------------------------------------------------------

    async def _observe(
        self,
        probe: ProbeAdapter,
        runner: GameRunner,
        retries: int = 3,
    ) -> dict[str, Any]:
        """Poll ``observe_fast`` with retry on probe injection failure."""
        for attempt in range(1 + retries):
            state = await probe.observe_fast(runner._page)
            if isinstance(state, dict) and state.get("ready"):
                return state
            if attempt < retries:
                logger.debug("Probe not ready (attempt %d/%d), re-injecting", attempt + 1, retries)
                await probe.inject(runner._page)
                await asyncio.sleep(self._probe_retry_delay_ms / 1000.0)
        return state if isinstance(state, dict) else {"ready": False}

    @staticmethod
    def _is_finished(
        state: dict[str, Any],
        summary: dict[str, Any],
    ) -> bool:
        """Check terminal conditions and update *summary* in place."""
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

    # ------------------------------------------------------------------
    # Think — text
    # ------------------------------------------------------------------

    async def _think_text(
        self,
        state: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        *,
        ctx: AgentContext | None = None,
    ) -> dict[str, Any]:
        """Call DeepSeek for a structured text decision.

        Parameters
        ----------
        state:
            Current game state dict from the probe.
        history:
            Optional list of history entries (ignored when *ctx* is provided).
        ctx:
            Optional :class:`AgentContext` — when provided, working memory is
            used instead of *history*.

        Returns a parsed action dict.  Retries with correction hints on
        JSON parse failure; falls back to a ``wait`` action after all
        retries are exhausted.
        """
        prompt = self._build_text_prompt(state, history, ctx=ctx)
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        last_raw = ""

        for attempt in range(1 + self._max_json_retries):
            resp = self._client.chat(
                messages, model=self._text_model, max_tokens=512, temperature=0.0
            )
            last_raw = resp.choices[0].message.content
            try:
                return self._parse_llm_response(last_raw)
            except (json.JSONDecodeError, ValueError) as exc:
                if attempt >= self._max_json_retries:
                    logger.debug(
                        "Text LLM JSON parse exhausted after %d attempts: %s",
                        attempt + 1,
                        exc,
                    )
                    break
                logger.debug(
                    "Text LLM JSON parse failure (attempt %d/%d): %s",
                    attempt + 1,
                    self._max_json_retries,
                    exc,
                )
                messages.append({"role": "assistant", "content": last_raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was not valid JSON. "
                            "Please respond ONLY with a JSON object: "
                            '{"action": "<action>", "params": {...}, "reason": "..."}'
                        ),
                    }
                )

        return {"action": "wait", "params": {"duration_ms": 500}, "reason": "fallback"}

    def _build_system_prompt(self, ctx: AgentContext | None = None) -> str:
        """Build a system-context string from cross-session memory in *ctx*.

        Reads ``ctx.metadata["previous_sessions"]`` (from
        :meth:`EpisodicMemory.find_similar`) and
        ``ctx.metadata["relevant_knowledge"]`` (from
        :meth:`SemanticMemory.query`) and formats them as bullet-pointed
        sections.

        Parameters
        ----------
        ctx:
            Optional agent context.  When ``None`` or when neither memory
            key is present, an empty string is returned.

        Returns
        -------
        str
            Formatted system context, capped at ~1000 characters.
        """
        if ctx is None:
            return ""

        metadata = ctx.metadata
        previous_sessions = metadata.get("previous_sessions")
        relevant_knowledge = metadata.get("relevant_knowledge")

        if not previous_sessions and not relevant_knowledge:
            return ""

        parts: list[str] = []

        if previous_sessions:
            parts.append("## Previous Game Experience")
            for s in previous_sessions:
                sid = s.get("id", "unknown")
                result = s.get("result", "?")
                summary = s.get("summary", "")[:200]
                score = s.get("score", 0)
                parts.append(f"- [{result}] Session {sid[:8]}: {summary} (score: {score})")

        if relevant_knowledge:
            parts.append("## Relevant Game Knowledge")
            for e in relevant_knowledge:
                content = e.get("content", "")
                confidence = e.get("confidence", 0.0)
                parts.append(f"- [{confidence:.0%}] {content}")

        system_text = "\n".join(parts)
        if len(system_text) > 1000:
            system_text = system_text[:997] + "..."
        return system_text

    def _build_text_prompt(
        self,
        state: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        *,
        ctx: AgentContext | None = None,
    ) -> str:
        """Format the text prompt with current state and recent history.

        When *ctx* with working memory is provided, ``to_prompt_context()``
        is used to build the history section instead of the raw *history*
        list.

        When *ctx* carries cross-session memory
        (``previous_sessions`` / ``relevant_knowledge`` in metadata) the
        system context is prepended before the main prompt.
        """
        state_json = json.dumps(state, indent=2, default=str)

        if ctx is not None and ctx.working_memory is not None:
            history_section = ctx.working_memory.to_prompt_context(5)
        elif history:
            history_section = json.dumps(history[-5:], indent=2, default=str)
        else:
            history_section = "[]"

        system_context = self._build_system_prompt(ctx=ctx) if ctx else ""

        if system_context:
            return system_context + "\n\n" + self.TEXT_PROMPT.format(
                history=history_section, state=state_json,
            )

        return self.TEXT_PROMPT.format(
            history=history_section,
            state=state_json,
        )

    # ------------------------------------------------------------------
    # Think — vision
    # ------------------------------------------------------------------

    async def _think_vision(self, screenshot_path: str) -> dict[str, Any]:
        """Call Mimo-v2.5 for vision-based analysis of the screenshot.

        Returns a parsed dict with visual observations.  Falls back to
        an empty vision dict after all retries are exhausted.
        """
        prompt = self._build_vision_prompt()
        image_uri = OpenCodeGoClient.encode_image_base64(screenshot_path)
        vision_content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_uri}},
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": vision_content}]

        for attempt in range(1 + self._max_json_retries):
            resp = self._client.chat_with_vision(messages, model=self._vision_model, max_tokens=512)
            raw = resp.choices[0].message.content
            try:
                return self._parse_llm_response(raw)
            except json.JSONDecodeError, ValueError:
                if attempt >= self._max_json_retries:
                    logger.debug("Vision LLM JSON parse exhausted after %d attempts", attempt + 1)
                    break
                logger.debug(
                    "Vision LLM JSON parse failure (attempt %d/%d)",
                    attempt + 1,
                    self._max_json_retries,
                )
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": "Respond ONLY with valid JSON (no markdown).",
                    }
                )

        return {"has_arrow": False, "arrow_direction": "none"}

    @staticmethod
    def _build_vision_prompt() -> str:
        """Return the standard vision analysis prompt."""
        return LLMAgent.VISION_PROMPT

    # ------------------------------------------------------------------
    # JSON parsing
    # ------------------------------------------------------------------

    _JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
    _JSON_BARE_RE = re.compile(r"\{[\s\S]*\}")

    @classmethod
    def _parse_llm_response(cls, text: str) -> dict[str, Any]:
        """Parse LLM output into a dict, handling markdown fences.

        Raises
        ------
        ValueError
            If *text* cannot be parsed as JSON.
        """
        if not text or not text.strip():
            raise ValueError("Empty LLM response")

        # Strip markdown code fences.
        cleaned = cls._JSON_FENCE_RE.sub("", text).strip()

        # If we *still* don't have a JSON object, try to find one.
        if not cleaned.startswith("{"):
            match = cls._JSON_BARE_RE.search(cleaned)
            if match:
                cleaned = match.group(0)
            else:
                raise ValueError(f"No JSON object found in response: {text[:200]!r}")

        return json.loads(cleaned)

    # ------------------------------------------------------------------
    # Decision fusion
    # ------------------------------------------------------------------

    @staticmethod
    def _fuse_decisions(
        text_response: dict[str, Any] | None,
        vision_response: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Combine text and vision outputs into a single action dict.

        Priority rules:
        - If vision detects an end screen, prefer a wait or tap action.
        - If vision detects a clear arrow, use that direction for a move.
        - Otherwise, trust the text model's action.

        Returns an action dict with ``action``, ``params``, and ``reason`` keys.
        """
        # If text response is missing or malformed, build from vision.
        if not isinstance(text_response, dict) or "action" not in text_response:
            text_response = {}

        # Default fallback action.
        fallback: dict[str, Any] = {
            "action": "wait",
            "params": {"duration_ms": 500},
            "reason": "No valid decision available",
        }

        # Start with text as the primary decision.
        decision = fallback.copy()
        if text_response:
            decision["action"] = text_response.get("action", "wait")
            decision["params"] = text_response.get("params", {})
            decision["reason"] = text_response.get("reason", decision["reason"])

        # Apply vision overrides when available.
        if isinstance(vision_response, dict):
            # End-screen takes highest priority — tap or wait.
            if vision_response.get("is_end_screen"):
                decision["action"] = "wait"
                decision["params"] = {"duration_ms": 2000}
                decision["reason"] = "Vision: end screen detected, waiting"

            # Arrow direction overrides joystick move.
            elif vision_response.get("has_arrow") and vision_response.get(
                "arrow_direction"
            ) not in (None, "none"):
                direction = vision_response["arrow_direction"]
                dx, dy = _arrow_to_vector(direction)
                decision["action"] = "move"
                decision["params"] = {"dx": dx, "dy": dy, "duration_ms": 320}
                decision["reason"] = f"Vision: following {direction} arrow"

            # Merge any extra visual insights into reason.
            if vision_response.get("extra_notes"):
                decision["reason"] = (
                    f"{decision['reason']} | Vision: {vision_response['extra_notes']}"
                )

        # Normalise to ensure action validity.
        if decision.get("action") not in _VALID_ACTIONS:
            decision["action"] = "wait"
            decision["params"] = {"duration_ms": 500}
            decision["reason"] = "Fallback: invalid action type"

        return decision

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    async def _execute(
        self,
        decision: dict[str, Any],
        runner: GameRunner,
        state: dict[str, Any],
    ) -> None:
        """Dispatch a decision to the appropriate CDP touch method.

        Parameters
        ----------
        decision:
            Action dict with ``action``, ``params``, ``reason``.
        runner:
            Active :class:`GameRunner`.
        state:
            Current game state (used for anchor resolution).
        """
        action = decision.get("action", "wait")
        params = decision.get("params", {})

        if action == "move":
            dx = float(params.get("dx", 0))
            dy = float(params.get("dy", 0))
            duration = int(params.get("duration_ms", 320))

            # Resolve joystick anchor from game profile if available.
            game_profile = self._config.get("game_profile", {})
            joystick = game_profile.get("joystick", {})
            anchor_x = int(params.get("anchor_x", joystick.get("anchor", [91, 699])[0]))
            anchor_y = int(params.get("anchor_y", joystick.get("anchor", [91, 699])[1]))
            radius = int(params.get("radius", joystick.get("radius", 50)))

            await runner.joystick_pulse(
                dx, dy, duration, anchor=(anchor_x, anchor_y), radius=radius
            )

        elif action == "tap":
            x = float(params.get("x", 187))
            y = float(params.get("y", 400))
            duration = int(params.get("duration_ms", 100))
            await runner.tap(x, y, duration)

        elif action == "wait":
            duration = int(params.get("duration_ms", 500))
            await runner.wait(duration)

        else:
            logger.warning("Unknown action %r, falling back to wait", action)
            await runner.wait(500)

    # ------------------------------------------------------------------
    # Dataset collection
    # ------------------------------------------------------------------

    def _collect_dataset_step(
        self,
        state: dict[str, Any],
        screenshot_path: str | None,
        decision: dict[str, Any],
    ) -> None:
        """Record a single observation/action pair for dataset collection.

        This is a no-op when ``config.collect_dataset`` is ``False``.
        """
        self._dataset.append(
            {
                "state": state,
                "screenshot": screenshot_path,
                "decision": decision,
            }
        )
        if self._dataset_writer is not None:
            self._dataset_writer.write_step(state, screenshot_path, decision)

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _summarize_state(state: dict[str, Any]) -> dict[str, Any]:
        """Return a compact state summary for the history log."""
        return {
            "ready": state.get("ready"),
            "done": state.get("done"),
            "win": state.get("win"),
            "keyNumbers": state.get("keyNumbers", {}),
            "keyFlags": state.get("keyFlags", {}),
        }

    @staticmethod
    def _temp_screenshot_path(step: int) -> str:
        """Generate a temporary screenshot path for a given step."""
        import tempfile

        return str(Path(tempfile.gettempdir()) / f"agent_step_{step:04d}.png")

    @staticmethod
    async def _cleanup_runner(runner: GameRunner) -> None:
        """Safely close the GameRunner, swallowing any errors."""
        try:
            await runner.close()
        except Exception:
            logger.debug("Error closing runner", exc_info=True)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _arrow_to_vector(direction: str) -> tuple[float, float]:
    """Convert a direction label to a normalised (dx, dy) vector."""
    mapping: dict[str, tuple[float, float]] = {
        "up": (0.0, -1.0),
        "down": (0.0, 1.0),
        "left": (-1.0, 0.0),
        "right": (1.0, 0.0),
    }
    return mapping.get(direction, (0.0, 0.0))
