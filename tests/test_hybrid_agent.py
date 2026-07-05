"""Tests for src.agent.hybrid_agent with AgentContext, WorkingMemory, and EpisodicMemory.

No real browsers or API calls — everything is mocked.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from src.agent.hybrid_agent import HybridAgent
from src.agent.memory import EpisodicMemory


# 1x1 white PNG — valid bytes for PIL to open without error.
MINI_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
    b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_state() -> dict:
    return {
        "ready": True,
        "done": False,
        "win": False,
        "keyNumbers": {"score": 0},
        "keyFlags": {},
    }


@pytest.fixture
def sample_state_done() -> dict:
    return {
        "ready": True,
        "done": True,
        "win": False,
        "keyNumbers": {"score": 100},
        "keyFlags": {},
        "doneReason": "level complete",
    }


@pytest.fixture
def sample_action() -> dict:
    return {
        "action": "move",
        "params": {"dx": 0.5, "dy": 1.0, "duration_ms": 320},
        "reason": "move toward target",
    }


def _make_chat_response(action: dict) -> mock.MagicMock:
    """Build a mock chat response returning *action* as JSON."""
    resp = mock.MagicMock()
    resp.choices = [
        mock.MagicMock(
            message=mock.MagicMock(content=json.dumps(action))
        )
    ]
    return resp


def _make_mock_client(action: dict | None = None) -> mock.MagicMock:
    """Return a mock OpenCodeGoClient with chat returning the given action."""
    client = mock.MagicMock()
    action = action or {
        "action": "move",
        "params": {"dx": 0.5, "dy": 1.0, "duration_ms": 320},
        "reason": "mock move",
    }
    client.chat.return_value = _make_chat_response(action)
    return client


def _make_mock_runner() -> mock.AsyncMock:
    """Return a mock GameRunner with all async methods."""
    runner = mock.AsyncMock()
    runner.start = mock.AsyncMock()
    runner.open_game = mock.AsyncMock()
    runner.close = mock.AsyncMock()
    runner.screenshot = mock.AsyncMock(return_value=MINI_PNG)
    runner.joystick_pulse = mock.AsyncMock()
    runner.tap = mock.AsyncMock()
    runner.wait = mock.AsyncMock()
    runner._page = mock.MagicMock()
    return runner


def _make_mock_probe(states: list[dict] | None = None) -> mock.AsyncMock:
    """Return a mock ProbeAdapter returning *states* in sequence."""
    if states is None:
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {"score": 0}},
            {"ready": True, "done": True, "win": False, "keyNumbers": {"score": 50},
             "doneReason": "done"},
        ]
    probe = mock.AsyncMock()
    probe.inject = mock.AsyncMock()
    probe.wait_for_ready = mock.AsyncMock(return_value=states[0])
    probe.observe_fast = mock.AsyncMock(side_effect=states)
    return probe


def _setup_agent_run(
    monkeypatch: pytest.MonkeyPatch,
    agent: HybridAgent,
    states: list[dict] | None = None,
    mock_runner: mock.AsyncMock | None = None,
    mock_probe: mock.AsyncMock | None = None,
) -> tuple[mock.AsyncMock, mock.AsyncMock]:
    """Inject mock GameRunner and ProbeAdapter for an agent run."""
    if mock_runner is None:
        mock_runner = _make_mock_runner()
    if mock_probe is None:
        mock_probe = _make_mock_probe(states)

    monkeypatch.setattr(
        "src.agent.hybrid_agent.GameRunner",
        lambda headed=False, slow_mo=0, games_dir=None: mock_runner,
    )
    monkeypatch.setattr(
        "src.agent.hybrid_agent.ProbeAdapter",
        lambda probe_source_path=None: mock_probe,
    )
    return mock_runner, mock_probe


# ---------------------------------------------------------------------------
# Construction tests
# ---------------------------------------------------------------------------


class TestHybridAgentConstruction:
    def test_default_init(self) -> None:
        agent = HybridAgent()
        assert agent.mode == "api"
        assert agent._episodic_memory is None
        assert agent._dataset_writer is None

    def test_memory_config_creates_episodic_memory(self, tmp_path) -> None:
        db_path = tmp_path / "test.db"
        agent = HybridAgent(memory_config={"db_path": str(db_path)})
        assert agent._episodic_memory is not None
        assert isinstance(agent._episodic_memory, EpisodicMemory)
        agent._episodic_memory.close()

    def test_collect_dataset_enabled(self) -> None:
        agent = HybridAgent(config={"collect_dataset": True})
        assert agent._collect_dataset is True


# ---------------------------------------------------------------------------
# AgentContext lifecycle tests
# ---------------------------------------------------------------------------


class TestAgentContextLifecycle:
    @pytest.mark.asyncio
    async def test_ctx_probe_state_updated_each_step(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {"score": 0}},
            {"ready": True, "done": True, "win": False, "keyNumbers": {"score": 50},
             "doneReason": "over"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(mode="api", api_client=client)
        mock_runner, mock_probe = _setup_agent_run(monkeypatch, agent, states)

        result = await agent.run_game("/tmp/game.html", max_steps=3)
        assert result["completed"] is True
        ctx = agent._ctx
        assert ctx.probe_state.get("keyNumbers", {}).get("score") == 50

    @pytest.mark.asyncio
    async def test_ctx_screenshot_updated_each_step(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "ok"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(mode="api", api_client=client)
        _setup_agent_run(monkeypatch, agent, states)

        await agent.run_game("/tmp/game.html", max_steps=3)
        ctx = agent._ctx
        assert ctx.screenshot is not None
        assert isinstance(ctx.screenshot, bytes)

    @pytest.mark.asyncio
    async def test_ctx_step_number_increments(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "ok"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(mode="api", api_client=client)
        _setup_agent_run(monkeypatch, agent, states)

        await agent.run_game("/tmp/game.html", max_steps=5)
        ctx = agent._ctx
        assert ctx.step_number >= 2  # at least 2 full steps before done

    @pytest.mark.asyncio
    async def test_ctx_errors_populated_on_exception(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = mock.MagicMock()
        client.chat.side_effect = RuntimeError("API down")
        agent = HybridAgent(mode="api", api_client=client)
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
        ]
        _setup_agent_run(monkeypatch, agent, states)

        await agent.run_game("/tmp/game.html", max_steps=3)
        ctx = agent._ctx
        assert len(ctx.errors) > 0
        assert "Exception" in ctx.errors[0]


# ---------------------------------------------------------------------------
# WorkingMemory tests
# ---------------------------------------------------------------------------


class TestWorkingMemoryIntegration:
    @pytest.mark.asyncio
    async def test_working_memory_push_action_called(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "end"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(mode="api", api_client=client)
        _setup_agent_run(monkeypatch, agent, states)

        await agent.run_game("/tmp/game.html", max_steps=3)
        wm = agent._ctx.working_memory
        assert wm.step_count >= 1

    @pytest.mark.asyncio
    async def test_working_memory_history_has_correct_steps(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "ok"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(mode="api", api_client=client)
        _setup_agent_run(monkeypatch, agent, states)

        await agent.run_game("/tmp/game.html", max_steps=5)
        wm = agent._ctx.working_memory
        assert wm.step_count == 2  # two full steps before done state

    @pytest.mark.asyncio
    async def test_working_memory_recent_actions_returns_records(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "done"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(mode="api", api_client=client)
        _setup_agent_run(monkeypatch, agent, states)

        await agent.run_game("/tmp/game.html", max_steps=5)
        wm = agent._ctx.working_memory
        recent = wm.recent_actions(5)
        assert len(recent) >= 1
        assert recent[-1].action["action"] == "move"


# ---------------------------------------------------------------------------
# EpisodicMemory tests
# ---------------------------------------------------------------------------


class TestEpisodicMemoryIntegration:
    @pytest.mark.asyncio
    async def test_episodic_memory_records_steps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path,
    ) -> None:
        db_path = tmp_path / "em_test.db"
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "end"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(
            mode="api",
            game_id="SSD_00848P01",
            api_client=client,
            memory_config={"db_path": str(db_path)},
        )
        _setup_agent_run(monkeypatch, agent, states)

        await agent.run_game("/tmp/game.html", max_steps=3)

        # Verify DB was written to
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM sessions").fetchall()
        assert len(rows) == 1
        assert rows[0]["game_id"] == "SSD_00848P01"
        step_rows = conn.execute("SELECT * FROM steps").fetchall()
        assert len(step_rows) >= 1
        conn.close()

    @pytest.mark.asyncio
    async def test_ctx_metadata_stores_session_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path,
    ) -> None:
        db_path = tmp_path / "em_meta.db"
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "end"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(
            mode="api",
            game_id="SSD_00848P01",
            api_client=client,
            memory_config={"db_path": str(db_path)},
        )
        _setup_agent_run(monkeypatch, agent, states)

        await agent.run_game("/tmp/game.html", max_steps=3)
        ctx = agent._ctx
        assert "session_id" in ctx.metadata
        assert len(ctx.metadata["session_id"]) == 16

    @pytest.mark.asyncio
    async def test_memory_config_none_no_crash(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "end"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(mode="api", api_client=client)
        _setup_agent_run(monkeypatch, agent, states)

        result = await agent.run_game("/tmp/game.html", max_steps=3)
        assert result["completed"] is True
        assert agent._episodic_memory is None


# ---------------------------------------------------------------------------
# Mode tests — all 7 modes
# ---------------------------------------------------------------------------


class TestAllModes:
    """Verify all 7 modes produce valid action dicts."""

    MODES = ["api", "vlm", "vlm-struct", "vlm-rule", "api-rule", "rule",
             "vlm-struct-api-rule"]

    @pytest.mark.asyncio
    async def test_mode_api_produces_valid_action(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "ok"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(mode="api", api_client=client)
        _setup_agent_run(monkeypatch, agent, states)
        result = await agent.run_game("/tmp/game.html", max_steps=3)
        assert result["completed"] is True

    @pytest.mark.asyncio
    async def test_mode_rule_produces_valid_action(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "ok"},
        ]
        agent = HybridAgent(mode="rule", game_id="SSD_00848P01")
        _setup_agent_run(monkeypatch, agent, states)
        result = await agent.run_game("/tmp/game.html", max_steps=3)
        assert result["completed"] is True

    @pytest.mark.asyncio
    async def test_mode_vlm_struct_enriches_visual_struct(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.agent.context import AgentContext

        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "end"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(mode="vlm-struct", api_client=client)

        # Patch _decide_vlm_struct so it sets visual_struct on ctx
        async def fake_decide_vlm_struct(ctx: AgentContext) -> dict:
            ctx.visual_struct = {"player_position": (100, 200), "targets": ["t1"]}
            return {"action": "move", "params": {"dx": 1, "dy": 0}, "reason": "vlm_struct"}
        monkeypatch.setattr(agent, "_decide_vlm_struct", fake_decide_vlm_struct)

        _setup_agent_run(monkeypatch, agent, states)
        await agent.run_game("/tmp/game.html", max_steps=3)
        ctx = agent._ctx
        assert ctx.visual_struct is not None
        assert ctx.visual_struct.get("player_position") == (100, 200)

    @pytest.mark.asyncio
    async def test_mode_vlm_produces_valid_action(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.agent.context import AgentContext

        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "end"},
        ]
        agent = HybridAgent(mode="vlm")

        async def fake_decide_vlm(ctx: AgentContext) -> dict:
            return {"action": "move", "params": {"dx": 1.0, "dy": 0.0}, "reason": "vlm move"}
        monkeypatch.setattr(agent, "_decide_vlm", fake_decide_vlm)

        _setup_agent_run(monkeypatch, agent, states)
        result = await agent.run_game("/tmp/game.html", max_steps=3)
        assert result["completed"] is True

    @pytest.mark.asyncio
    async def test_mode_vlm_rule_produces_valid_action(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.agent.context import AgentContext

        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "ok"},
        ]
        agent = HybridAgent(mode="vlm-rule", game_id="SSD_00848P01")

        async def fake_decide_vlm_rule(ctx: AgentContext) -> dict:
            return {"action": "move", "params": {"dx": 1, "dy": 0, "duration_ms": 320},
                    "reason": "vlm_rule"}
        monkeypatch.setattr(agent, "_decide_vlm_rule", fake_decide_vlm_rule)

        _setup_agent_run(monkeypatch, agent, states)
        result = await agent.run_game("/tmp/game.html", max_steps=3)
        assert result["completed"] is True

    @pytest.mark.asyncio
    async def test_mode_api_rule_produces_valid_action(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.agent.context import AgentContext

        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "ok"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(mode="api-rule", game_id="SSD_00848P01", api_client=client)

        async def fake_decide_api_rule(ctx: AgentContext) -> dict:
            return {"action": "move", "params": {"dx": 1, "dy": 0, "duration_ms": 320},
                    "reason": "api_rule"}
        monkeypatch.setattr(agent, "_decide_api_rule", fake_decide_api_rule)

        _setup_agent_run(monkeypatch, agent, states)
        result = await agent.run_game("/tmp/game.html", max_steps=3)
        assert result["completed"] is True

    @pytest.mark.asyncio
    async def test_mode_vlm_struct_api_rule_produces_valid_action(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.agent.context import AgentContext

        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "ok"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(
            mode="vlm-struct-api-rule", game_id="SSD_00848P01", api_client=client,
        )

        async def fake_decide_vlm_struct_api_rule(ctx: AgentContext) -> dict:
            ctx.visual_struct = {"targets": ["a"]}
            return {"action": "move", "params": {"dx": 1, "dy": 0, "duration_ms": 320},
                    "reason": "vlm_struct_api_rule"}
        monkeypatch.setattr(agent, "_decide_vlm_struct_api_rule", fake_decide_vlm_struct_api_rule)

        _setup_agent_run(monkeypatch, agent, states)
        result = await agent.run_game("/tmp/game.html", max_steps=3)
        assert result["completed"] is True


# ---------------------------------------------------------------------------
# DatasetWriter integration tests
# ---------------------------------------------------------------------------


class TestDatasetWriterIntegration:
    @pytest.mark.asyncio
    async def test_dataset_writer_records_steps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path,
    ) -> None:
        dataset_dir = tmp_path / "datasets"
        states = [
            {"ready": True, "done": False, "win": False, "keyNumbers": {}},
            {"ready": True, "done": True, "win": False, "doneReason": "end"},
        ]
        client = _make_mock_client()
        agent = HybridAgent(
            mode="api",
            api_client=client,
            config={
                "collect_dataset": True,
                "dataset_output_dir": str(dataset_dir),
            },
        )
        _setup_agent_run(monkeypatch, agent, states)

        result = await agent.run_game("/tmp/game.html", max_steps=3)
        assert "dataset_path" in result
        jsonl_files = list(dataset_dir.glob("*.jsonl"))
        assert len(jsonl_files) >= 1


# ---------------------------------------------------------------------------
# Backward compatibility tests
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_init_without_memory_config_works(self) -> None:
        agent = HybridAgent(mode="api", api_client=_make_mock_client())
        assert agent._episodic_memory is None

    def test_old_style_init_still_works(self) -> None:
        agent = HybridAgent("api", "SSD_00848P01", _make_mock_client())
        assert agent.mode == "api"
        assert agent.game_id == "SSD_00848P01"
