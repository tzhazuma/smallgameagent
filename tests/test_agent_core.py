"""Tests for src.agent.llm_agent.

No real browsers or API calls — everything is mocked.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from src.agent.llm_agent import LLMAgent, _arrow_to_vector


# ---------------------------------------------------------------------------
# Shared fixtures / test data
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_client() -> mock.MagicMock:
    """Return a mock OpenCodeGoClient with text and vision chat support."""
    client = mock.MagicMock()

    # Default chat response: a valid move action.
    default_resp = mock.MagicMock()
    default_resp.choices = [
        mock.MagicMock(
            message=mock.MagicMock(content=json.dumps({
                "action": "move",
                "params": {"dx": 0.5, "dy": 1.0, "duration_ms": 320},
                "reason": "Move toward target",
            }))
        )
    ]
    client.chat.return_value = default_resp

    # Default vision response.
    vision_resp = mock.MagicMock()
    vision_resp.choices = [
        mock.MagicMock(
            message=mock.MagicMock(content=json.dumps({
                "has_arrow": True,
                "arrow_direction": "right",
                "has_target": True,
                "target_visible": True,
                "has_obstacle": False,
                "is_end_screen": False,
                "ui_buttons": [],
                "extra_notes": "Target visible to the right",
            }))
        )
    ]
    client.chat_with_vision.return_value = vision_resp

    return client


@pytest.fixture
def sample_state() -> dict:
    """A typical ready game state."""
    return {
        "ready": True,
        "done": False,
        "win": False,
        "player": {"name": "Player", "path": "Canvas/Player"},
        "keyNumbers": {"score": 150, "coins": 3},
        "keyFlags": {"isGameOver": False, "isWin": False},
        "harvestChain": {"nodes": []},
        "guideSummary": {
            "likelyCurrentTarget": {"name": "Target_1", "path": "Canvas/Target_1"},
        },
        "completionSummary": {"endState": {"done": False}},
    }


@pytest.fixture
def sample_state_win() -> dict:
    """A game state indicating a win."""
    return {
        "ready": True,
        "done": True,
        "win": True,
        "doneReason": "All objectives completed",
        "player": {"name": "Player", "path": "Canvas/Player"},
        "keyNumbers": {"score": 999},
        "keyFlags": {"isWin": True},
    }


@pytest.fixture
def sample_history() -> list[dict]:
    """Two recent history entries."""
    return [
        {
            "step": 0,
            "state_summary": {"ready": True, "done": False, "win": False},
            "decision": {"action": "move", "params": {"dx": 1, "dy": 0}, "reason": "go right"},
        },
        {
            "step": 1,
            "state_summary": {"ready": True, "done": False, "win": False},
            "decision": {"action": "move", "params": {"dx": 0, "dy": 1}, "reason": "go down"},
        },
    ]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestAgentConstruction:
    """LLMAgent instance creation and config handling."""

    def test_constructs_with_client(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        assert agent._client is mock_client
        assert agent._text_model == "deepseek-v4-flash"
        assert agent._vision_model == "mimo-v2.5"

    def test_constructs_with_custom_config(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(
            mock_client,
            config={
                "text_model": "deepseek-v4-pro",
                "vision_model": "mimo-v2.5-pro",
                "max_steps": 100,
            },
        )
        assert agent._text_model == "deepseek-v4-pro"
        assert agent._vision_model == "mimo-v2.5-pro"
        assert agent._max_steps_default == 100

    def test_default_config_none(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client, config=None)
        assert agent._max_steps_default == 200
        assert agent._probe_timeout_ms == 18_000

    def test_dataset_collection_disabled_by_default(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        assert agent._collect_dataset is False

    def test_dataset_collection_enabled(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client, config={"collect_dataset": True})
        assert agent._collect_dataset is True


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


class TestPromptBuilding:
    """_build_text_prompt and _build_vision_prompt."""

    def test_text_prompt_includes_state(self, mock_client: mock.MagicMock, sample_state: dict) -> None:
        agent = LLMAgent(mock_client)
        prompt = agent._build_text_prompt(sample_state, [])
        assert "score" in prompt
        assert "150" in prompt
        assert "ready" in prompt

    def test_text_prompt_includes_history(self, mock_client: mock.MagicMock, sample_state: dict, sample_history: list[dict]) -> None:
        agent = LLMAgent(mock_client)
        prompt = agent._build_text_prompt(sample_state, sample_history)
        assert "go right" in prompt
        assert "go down" in prompt

    def test_text_prompt_truncates_long_history(self, mock_client: mock.MagicMock, sample_state: dict) -> None:
        agent = LLMAgent(mock_client)
        long_history = [{"step": i, "state_summary": {}, "decision": {}} for i in range(30)]
        prompt = agent._build_text_prompt(sample_state, long_history)
        # Only last 5 should appear.
        count = prompt.count('"step"')
        assert count <= 6  # 5 history + 1 if state serialized

    def test_vision_prompt_is_not_empty(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        prompt = agent._build_vision_prompt()
        assert len(prompt) > 50
        assert "has_arrow" in prompt


# ---------------------------------------------------------------------------
# JSON response parsing
# ---------------------------------------------------------------------------


class TestParseLLMResponse:
    """_parse_llm_response handles valid JSON, fences, and garbage."""

    def test_valid_json_no_fences(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        result = agent._parse_llm_response(
            '{"action":"move","params":{"dx":1},"reason":"test"}'
        )
        assert result["action"] == "move"
        assert result["params"]["dx"] == 1

    def test_json_in_markdown_fence(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        result = agent._parse_llm_response(
            '```json\n{"action":"wait","params":{"duration_ms":500},"reason":"pause"}\n```'
        )
        assert result["action"] == "wait"

    def test_json_in_plain_fence(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        result = agent._parse_llm_response(
            '```\n{"action":"tap","params":{"x":100,"y":200},"reason":"button"}\n```'
        )
        assert result["action"] == "tap"

    def test_json_with_surrounding_text(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        result = agent._parse_llm_response(
            'Here is my response: {"action":"move","params":{"dx":0.5,"dy":-0.5},"reason":"nw"} hope that helps'
        )
        assert result["action"] == "move"
        assert result["params"]["dx"] == 0.5

    def test_empty_string_raises(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        with pytest.raises(ValueError, match="Empty LLM response"):
            agent._parse_llm_response("")

    def test_whitespace_only_raises(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        with pytest.raises(ValueError, match="Empty LLM response"):
            agent._parse_llm_response("   \n  ")

    def test_no_json_object_raises(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        with pytest.raises(ValueError, match="No JSON object found"):
            agent._parse_llm_response("The game should move right, I think.")

    def test_malformed_json_raises(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        with pytest.raises((json.JSONDecodeError, ValueError)):
            agent._parse_llm_response('{"action": move, "params": {}}')

    def test_none_raises(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        with pytest.raises(ValueError):
            agent._parse_llm_response(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Decision fusion
# ---------------------------------------------------------------------------


class TestDecisionFusion:
    """_fuse_decisions combines text + vision outputs correctly."""

    def test_text_primary_when_vision_none(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        text = {"action": "move", "params": {"dx": 1.0, "dy": 0.0}, "reason": "text says right"}
        result = agent._fuse_decisions(text, None)
        assert result["action"] == "move"
        assert result["reason"] == "text says right"

    def test_text_fallback_when_none(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        result = agent._fuse_decisions(None, None)
        assert result["action"] == "wait"
        assert result["reason"] == "No valid decision available"

    def test_vision_end_screen_overrides(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        text = {"action": "move", "params": {"dx": 1.0, "dy": 0.0}, "reason": "go"}
        vision = {"is_end_screen": True, "extra_notes": "victory screen visible"}
        result = agent._fuse_decisions(text, vision)
        assert result["action"] == "wait"
        assert result["params"]["duration_ms"] == 2000
        assert "end screen" in result["reason"].lower()

    def test_vision_arrow_overrides_direction(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        text = {"action": "move", "params": {"dx": 0.0, "dy": 0.0}, "reason": "idle"}
        vision = {"has_arrow": True, "arrow_direction": "left"}
        result = agent._fuse_decisions(text, vision)
        assert result["action"] == "move"
        assert result["params"]["dx"] == -1.0
        assert result["params"]["dy"] == 0.0
        assert "following left arrow" in result["reason"]

    def test_vision_arrow_ignores_none_direction(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        text = {"action": "move", "params": {"dx": 0.5, "dy": 0.5}, "reason": "text choice"}
        vision = {"has_arrow": True, "arrow_direction": "none"}
        result = agent._fuse_decisions(text, vision)
        assert result["action"] == "move"
        assert result["params"]["dx"] == 0.5  # text preserved

    def test_vision_extra_notes_appended(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        text = {"action": "wait", "params": {"duration_ms": 300}, "reason": "text pause"}
        vision = {"extra_notes": "obstacle ahead blocking path"}
        result = agent._fuse_decisions(text, vision)
        assert "obstacle ahead" in result["reason"]

    def test_invalid_action_falls_back_to_wait(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        text = {"action": "jump", "params": {}, "reason": "unknown"}
        result = agent._fuse_decisions(text, None)
        assert result["action"] == "wait"
        assert "invalid" in result["reason"].lower()

    def test_malformed_text_dict(self, mock_client: mock.MagicMock) -> None:
        """When text response is a dict but has no action key."""
        agent = LLMAgent(mock_client)
        text = {"garbage": True}
        result = agent._fuse_decisions(text, None)
        assert result["action"] == "wait"

    def test_vision_non_dict_ignored(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        text = {"action": "move", "params": {"dx": 1, "dy": 0}, "reason": "go"}
        result = agent._fuse_decisions(text, "not a dict")  # type: ignore[arg-type]
        assert result["action"] == "move"

    def test_vision_arrow_up(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        text = {"action": "move", "params": {}, "reason": "go"}
        vision = {"has_arrow": True, "arrow_direction": "up"}
        result = agent._fuse_decisions(text, vision)
        assert result["params"]["dx"] == 0.0
        assert result["params"]["dy"] == -1.0

    def test_vision_arrow_down(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        text = {"action": "move", "params": {}, "reason": "go"}
        vision = {"has_arrow": True, "arrow_direction": "down"}
        result = agent._fuse_decisions(text, vision)
        assert result["params"]["dx"] == 0.0
        assert result["params"]["dy"] == 1.0


# ---------------------------------------------------------------------------
# Action execution routing
# ---------------------------------------------------------------------------


class TestActionExecution:
    """_execute routes decisions to the correct CDP methods."""

    @pytest.fixture
    def mock_runner(self) -> mock.AsyncMock:
        """A mock GameRunner with async joystick/tap/wait methods."""
        runner = mock.AsyncMock()
        runner.joystick_pulse = mock.AsyncMock()
        runner.tap = mock.AsyncMock()
        runner.wait = mock.AsyncMock()
        runner._page = mock.MagicMock()  # needed by _observe
        return runner

    @pytest.mark.asyncio
    async def test_execute_move_calls_joystick(
        self, mock_client: mock.MagicMock, mock_runner: mock.AsyncMock
    ) -> None:
        agent = LLMAgent(mock_client)
        decision = {
            "action": "move",
            "params": {"dx": 0.8, "dy": -0.3, "duration_ms": 420},
            "reason": "chase target",
        }
        await agent._execute(decision, mock_runner, {})
        mock_runner.joystick_pulse.assert_called_once_with(
            0.8, -0.3, 420, anchor=(91, 699), radius=50,
        )
        mock_runner.tap.assert_not_called()
        mock_runner.wait.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_move_uses_game_profile_anchor(
        self, mock_runner: mock.AsyncMock
    ) -> None:
        client = mock.MagicMock()
        client.chat.return_value = mock.MagicMock()
        agent = LLMAgent(
            client,
            config={
                "game_profile": {
                    "joystick": {"anchor": [200, 500], "radius": 80},
                }
            },
        )
        decision = {
            "action": "move",
            "params": {"dx": 1.0, "dy": 0.0},
            "reason": "right",
        }
        await agent._execute(decision, mock_runner, {})
        mock_runner.joystick_pulse.assert_called_once_with(
            1.0, 0.0, 320, anchor=(200, 500), radius=80,
        )

    @pytest.mark.asyncio
    async def test_execute_move_params_override_anchor(
        self, mock_runner: mock.AsyncMock
    ) -> None:
        client = mock.MagicMock()
        agent = LLMAgent(client)
        decision = {
            "action": "move",
            "params": {"dx": 0.0, "dy": 1.0, "anchor_x": 300, "anchor_y": 600},
            "reason": "custom anchor",
        }
        await agent._execute(decision, mock_runner, {})
        mock_runner.joystick_pulse.assert_called_once_with(
            0.0, 1.0, 320, anchor=(300, 600), radius=50,
        )

    @pytest.mark.asyncio
    async def test_execute_tap(
        self, mock_client: mock.MagicMock, mock_runner: mock.AsyncMock
    ) -> None:
        agent = LLMAgent(mock_client)
        decision = {
            "action": "tap",
            "params": {"x": 200.0, "y": 300.0, "duration_ms": 150},
            "reason": "click button",
        }
        await agent._execute(decision, mock_runner, {})
        mock_runner.tap.assert_called_once_with(200.0, 300.0, 150)
        mock_runner.joystick_pulse.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_tap_defaults(
        self, mock_client: mock.MagicMock, mock_runner: mock.AsyncMock
    ) -> None:
        agent = LLMAgent(mock_client)
        decision = {"action": "tap", "params": {}, "reason": "tap"}
        await agent._execute(decision, mock_runner, {})
        mock_runner.tap.assert_called_once_with(187, 400, 100)

    @pytest.mark.asyncio
    async def test_execute_wait(
        self, mock_client: mock.MagicMock, mock_runner: mock.AsyncMock
    ) -> None:
        agent = LLMAgent(mock_client)
        decision = {
            "action": "wait",
            "params": {"duration_ms": 750},
            "reason": "cooldown",
        }
        await agent._execute(decision, mock_runner, {})
        mock_runner.wait.assert_called_once_with(750)
        mock_runner.joystick_pulse.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_unknown_action_falls_back(
        self, mock_client: mock.MagicMock, mock_runner: mock.AsyncMock
    ) -> None:
        agent = LLMAgent(mock_client)
        decision = {"action": "dance", "params": {}, "reason": "unknown"}
        await agent._execute(decision, mock_runner, {})
        mock_runner.wait.assert_called_once_with(500)


# ---------------------------------------------------------------------------
# Arrow to vector helper
# ---------------------------------------------------------------------------


class TestArrowToVector:
    """_arrow_to_vector converts direction labels."""

    def test_up(self) -> None:
        assert _arrow_to_vector("up") == (0.0, -1.0)

    def test_down(self) -> None:
        assert _arrow_to_vector("down") == (0.0, 1.0)

    def test_left(self) -> None:
        assert _arrow_to_vector("left") == (-1.0, 0.0)

    def test_right(self) -> None:
        assert _arrow_to_vector("right") == (1.0, 0.0)

    def test_unknown(self) -> None:
        assert _arrow_to_vector("diagonal") == (0.0, 0.0)


# ---------------------------------------------------------------------------
# Completion check
# ---------------------------------------------------------------------------


class TestIsFinished:
    """_is_finished detects terminal game states."""

    def test_not_finished(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        summary: dict = {}
        assert not agent._is_finished({"ready": True, "done": False, "win": False}, summary)
        assert summary == {}

    def test_win_detected(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        summary: dict = {}
        assert agent._is_finished({"ready": True, "done": True, "win": True}, summary)
        assert summary["completed"] is True
        assert summary["win"] is True

    def test_done_without_win(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        summary: dict = {}
        assert agent._is_finished(
            {"ready": True, "done": True, "win": False, "doneReason": "timeout"},
            summary,
        )
        assert summary["completed"] is True
        assert summary["win"] is False
        assert summary["reason"] == "timeout"


# ---------------------------------------------------------------------------
# State summary
# ---------------------------------------------------------------------------


class TestSummarizeState:
    """_summarize_state extracts compact summaries."""

    def test_extracts_keys(self, mock_client: mock.MagicMock, sample_state: dict) -> None:
        agent = LLMAgent(mock_client)
        summary = agent._summarize_state(sample_state)
        assert summary["ready"] is True
        assert summary["done"] is False
        assert summary["win"] is False
        assert summary["keyNumbers"]["score"] == 150

    def test_handles_missing_keys(self, mock_client: mock.MagicMock) -> None:
        agent = LLMAgent(mock_client)
        summary = agent._summarize_state({})
        assert summary["ready"] is None
        assert summary["keyNumbers"] == {}


# ---------------------------------------------------------------------------
# Full loop mock test
# ---------------------------------------------------------------------------


class TestFullRunLoop:
    """Mocked end-to-end run_game() cycle."""

    def _build_agent_and_runner(
        self,
        monkeypatch: pytest.MonkeyPatch,
        state_sequence: list[dict],
        text_responses: list[dict] | None = None,
    ) -> tuple[LLMAgent, mock.AsyncMock, mock.AsyncMock]:
        """Set up a fully mocked agent + runner for the main loop.

        Parameters
        ----------
        monkeypatch:
            Pytest monkeypatch fixture.
        state_sequence:
            States returned by ProbeAdapter.observe_fast(), one per step.
        text_responses:
            Actions returned by the text LLM per step.  Defaults to a
            series of ``move`` actions.
        """
        # ---- Build a mock client ----
        client = mock.MagicMock()
        if text_responses is None:
            text_responses = [
                {"action": "move", "params": {"dx": 1, "dy": 0, "duration_ms": 320}, "reason": "step 0"},
                {"action": "move", "params": {"dx": 0, "dy": 1, "duration_ms": 320}, "reason": "step 1"},
                {"action": "wait", "params": {"duration_ms": 500}, "reason": "done"},
            ]

        def _chat_response(messages, **kwargs):
            resp = mock.MagicMock()
            # Use the response for the current call index.
            idx = client.chat.call_count  # 0-indexed before increment
            if idx < len(text_responses):
                content = json.dumps(text_responses[idx])
            else:
                content = json.dumps({"action": "wait", "params": {"duration_ms": 500}, "reason": "overflow"})
            resp.choices = [mock.MagicMock(message=mock.MagicMock(content=content))]
            return resp

        client.chat.side_effect = _chat_response

        vision_resp = mock.MagicMock()
        vision_resp.choices = [
            mock.MagicMock(message=mock.MagicMock(content=json.dumps({
                "has_arrow": False, "arrow_direction": "none",
                "has_target": False, "target_visible": False,
                "has_obstacle": False, "is_end_screen": False,
                "ui_buttons": [],
            })))
        ]
        client.chat_with_vision.return_value = vision_resp

        # Encode image as a simple base64 data URI (we mock this on the client too).
        import base64
        client.encode_image_base64.return_value = "data:image/png;base64," + base64.b64encode(b"fake").decode()

        # ---- Build the agent ----
        config = {
            "max_steps": 3,
            "step_cooldown_ms": 0,
            "probe_timeout_ms": 1000,
            "probe_retry_delay_ms": 10,
        }
        agent = LLMAgent(client, config=config)

        # ---- Mock GameRunner ----
        mock_runner = mock.AsyncMock()
        mock_runner.start = mock.AsyncMock()
        mock_runner.open_game = mock.AsyncMock()
        mock_runner.close = mock.AsyncMock()
        mock_runner.screenshot = mock.AsyncMock(return_value=b"fake_png_bytes")
        mock_runner.joystick_pulse = mock.AsyncMock()
        mock_runner.tap = mock.AsyncMock()
        mock_runner.wait = mock.AsyncMock()
        mock_runner._page = mock.MagicMock()

        # ---- Mock ProbeAdapter ----
        mock_probe = mock.AsyncMock()
        mock_probe.inject = mock.AsyncMock()
        mock_probe.wait_for_ready = mock.AsyncMock(return_value=state_sequence[0])
        mock_probe.observe_fast = mock.AsyncMock(side_effect=state_sequence + [state_sequence[-1]])
        mock_probe._source = "mock probe source"

        # Patch constructors.
        monkeypatch.setattr(
            "src.agent.llm_agent.GameRunner",
            lambda headed=False, slow_mo=0, games_dir=None: mock_runner,
        )
        monkeypatch.setattr(
            "src.agent.llm_agent.ProbeAdapter",
            lambda probe_source_path=None: mock_probe,
        )

        return agent, mock_runner, mock_probe

    @pytest.mark.asyncio
    async def test_run_game_observes_thinks_acts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Main loop runs the full observe → think → act cycle."""
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {"score": 0}},
            {"ready": True, "done": False, "win": False, "keyNumbers": {"score": 50}},
            {"ready": True, "done": True, "win": False, "keyNumbers": {"score": 100}},
        ]
        agent, mock_runner, mock_probe = self._build_agent_and_runner(monkeypatch, states)

        result = await agent.run_game("/tmp/game.html", max_steps=3)

        # Should have completed because 'done' was detected.
        assert result["completed"] is True
        assert result["win"] is False
        assert result["steps"] <= 3

        # Runner lifecycle was called.
        mock_runner.start.assert_called_once()
        mock_runner.open_game.assert_called_once()
        mock_runner.close.assert_called_once()

        # Probe was injected and observed.
        mock_probe.inject.assert_called()
        mock_probe.wait_for_ready.assert_called_once()
        mock_probe.observe_fast.assert_called()

        # At least one screenshot was taken.
        mock_runner.screenshot.assert_called()

    @pytest.mark.asyncio
    async def test_run_game_detects_win(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Agent detects win state and returns immediately."""
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": True, "keyNumbers": {"score": 999}},
        ]
        agent, mock_runner, _ = self._build_agent_and_runner(monkeypatch, states)

        result = await agent.run_game("/tmp/game.html", max_steps=5)
        assert result["completed"] is True
        assert result["win"] is True
        assert result["steps"] <= 2

    @pytest.mark.asyncio
    async def test_run_game_probe_not_ready_returns_early(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When probe never reports ready the agent returns immediately."""
        config = {
            "max_steps": 5,
            "probe_timeout_ms": 100,
            "probe_retry_delay_ms": 10,
            "step_cooldown_ms": 0,
        }
        client = mock.MagicMock()
        agent = LLMAgent(client, config=config)

        mock_runner = mock.AsyncMock()
        mock_runner.start = mock.AsyncMock()
        mock_runner.open_game = mock.AsyncMock()
        mock_runner.close = mock.AsyncMock()
        mock_runner._page = mock.MagicMock()

        mock_probe = mock.AsyncMock()
        mock_probe.inject = mock.AsyncMock()
        mock_probe.wait_for_ready = mock.AsyncMock(return_value={"ready": False})

        monkeypatch.setattr(
            "src.agent.llm_agent.GameRunner",
            lambda headed=False, slow_mo=0, games_dir=None: mock_runner,
        )
        monkeypatch.setattr(
            "src.agent.llm_agent.ProbeAdapter",
            lambda probe_source_path=None: mock_probe,
        )

        result = await agent.run_game("/tmp/game.html")
        assert result["completed"] is False
        assert "ready" in result["reason"]

    @pytest.mark.asyncio
    async def test_run_game_dataset_collection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When collect_dataset=True, dataset is populated."""
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {"score": 0}},
            {"ready": True, "done": True, "win": False, "keyNumbers": {"score": 50}},
        ]
        config = {
            "max_steps": 3,
            "step_cooldown_ms": 0,
            "probe_timeout_ms": 1000,
            "probe_retry_delay_ms": 10,
            "collect_dataset": True,
        }
        client = mock.MagicMock()

        call_count = [0]

        def _chat_response(messages, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            resp = mock.MagicMock()
            resp.choices = [
                mock.MagicMock(
                    message=mock.MagicMock(content=json.dumps({
                        "action": "wait",
                        "params": {"duration_ms": 100},
                        "reason": f"step {idx}",
                    }))
                )
            ]
            return resp

        client.chat.side_effect = _chat_response
        client.chat_with_vision.return_value = mock.MagicMock(
            choices=[mock.MagicMock(message=mock.MagicMock(content="{}"))]
        )

        agent = LLMAgent(client, config=config)

        mock_runner = mock.AsyncMock()
        mock_runner.start = mock.AsyncMock()
        mock_runner.open_game = mock.AsyncMock()
        mock_runner.close = mock.AsyncMock()
        mock_runner.screenshot = mock.AsyncMock(return_value=b"fake_png")
        mock_runner.joystick_pulse = mock.AsyncMock()
        mock_runner.tap = mock.AsyncMock()
        mock_runner.wait = mock.AsyncMock()
        mock_runner._page = mock.MagicMock()

        mock_probe = mock.AsyncMock()
        mock_probe.inject = mock.AsyncMock()
        mock_probe.wait_for_ready = mock.AsyncMock(return_value=states[0])
        mock_probe.observe_fast = mock.AsyncMock(side_effect=states + [states[-1]])

        monkeypatch.setattr(
            "src.agent.llm_agent.GameRunner",
            lambda headed=False, slow_mo=0, games_dir=None: mock_runner,
        )
        monkeypatch.setattr(
            "src.agent.llm_agent.ProbeAdapter",
            lambda probe_source_path=None: mock_probe,
        )

        result = await agent.run_game("/tmp/game.html", max_steps=3)
        assert "dataset" in result
        assert len(result["dataset"]) > 0

    @pytest.mark.asyncio
    async def test_run_game_handles_exception_cleanly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Agent returns a summary even when an error occurs mid-loop."""
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
        ]
        config = {
            "max_steps": 3,
            "step_cooldown_ms": 0,
            "probe_timeout_ms": 1000,
            "probe_retry_delay_ms": 10,
        }
        client = mock.MagicMock()
        client.chat.side_effect = RuntimeError("API failure")

        agent = LLMAgent(client, config=config)

        mock_runner = mock.AsyncMock()
        mock_runner.start = mock.AsyncMock()
        mock_runner.open_game = mock.AsyncMock()
        mock_runner.close = mock.AsyncMock()
        mock_runner.screenshot = mock.AsyncMock(return_value=b"fake")
        mock_runner.joystick_pulse = mock.AsyncMock()
        mock_runner._page = mock.MagicMock()

        mock_probe = mock.AsyncMock()
        mock_probe.inject = mock.AsyncMock()
        mock_probe.wait_for_ready = mock.AsyncMock(return_value=states[0])
        mock_probe.observe_fast = mock.AsyncMock(return_value=states[0])

        monkeypatch.setattr(
            "src.agent.llm_agent.GameRunner",
            lambda headed=False, slow_mo=0, games_dir=None: mock_runner,
        )
        monkeypatch.setattr(
            "src.agent.llm_agent.ProbeAdapter",
            lambda probe_source_path=None: mock_probe,
        )

        result = await agent.run_game("/tmp/game.html", max_steps=3)
        assert result["completed"] is False
        assert "Exception" in result["reason"]
        mock_runner.close.assert_called_once()  # cleanup ran


# ---------------------------------------------------------------------------
# Prompt template constants
# ---------------------------------------------------------------------------


class TestPromptConstants:
    """Verify that class-level prompt constants are well-formed."""

    def test_text_prompt_contains_format_placeholders(self) -> None:
        assert "{state}" in LLMAgent.TEXT_PROMPT
        assert "{history}" in LLMAgent.TEXT_PROMPT

    def test_text_prompt_validates_action_format(self) -> None:
        prompt = LLMAgent.TEXT_PROMPT
        assert '"action"' in prompt
        assert '"params"' in prompt

    def test_vision_prompt_contains_output_schema(self) -> None:
        prompt = LLMAgent.VISION_PROMPT
        assert "has_arrow" in prompt
        assert "arrow_direction" in prompt
        assert "is_end_screen" in prompt


# ---------------------------------------------------------------------------
# think_text JSON retry
# ---------------------------------------------------------------------------


class TestThinkTextRetry:
    """_think_text retries on malformed JSON and returns fallback."""

    @pytest.mark.asyncio
    async def test_retries_then_returns_fallback(
        self, mock_client: mock.MagicMock, sample_state: dict
    ) -> None:
        """When all responses are bad JSON, fallback wait is returned."""
        agent = LLMAgent(mock_client, config={"max_json_retries": 1})

        bad_resp = mock.MagicMock()
        bad_resp.choices = [
            mock.MagicMock(message=mock.MagicMock(content="not json at all"))
        ]
        mock_client.chat.return_value = bad_resp

        result = await agent._think_text(sample_state, [])
        assert result["action"] == "wait"
        assert result["reason"] == "fallback"

    @pytest.mark.asyncio
    async def test_recovers_on_retry(
        self, mock_client: mock.MagicMock, sample_state: dict
    ) -> None:
        """First response is bad JSON, second is valid."""
        agent = LLMAgent(mock_client, config={"max_json_retries": 2})

        bad_resp = mock.MagicMock()
        bad_resp.choices = [
            mock.MagicMock(message=mock.MagicMock(content="oops not json"))
        ]
        good_resp = mock.MagicMock()
        good_resp.choices = [
            mock.MagicMock(
                message=mock.MagicMock(content=json.dumps({
                    "action": "tap", "params": {"x": 50, "y": 50}, "reason": "recovered",
                }))
            )
        ]
        mock_client.chat.side_effect = [bad_resp, good_resp]

        result = await agent._think_text(sample_state, [])
        assert result["action"] == "tap"
        assert result["reason"] == "recovered"
