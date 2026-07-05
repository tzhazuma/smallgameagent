"""Tests for the role framework (BaseAgentRole ABC, RoleCard, RolePipeline)."""

from __future__ import annotations

import sys
from dataclasses import asdict
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest

from src.agent.roles.base import BaseAgentRole, RoleCard, run_pipeline
from src.agent.roles.memory_curator import MemoryCurator
from src.agent.roles.state_mapper import StateMapper
from src.agent.roles.verifier import Verdict, Verifier

if TYPE_CHECKING:
    from src.agent.context import AgentContext


# ---------------------------------------------------------------------------
# Helpers — concrete role for testing
# ---------------------------------------------------------------------------


class _ConcreteRole(BaseAgentRole):
    """Minimal concrete role used in tests."""

    @property
    def role_name(self) -> str:
        return "test_role"

    @property
    def capabilities(self) -> list[str]:
        return ["observe", "reason", "act"]

    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        return {"observed": True}

    async def reason(self, ctx: AgentContext) -> dict[str, Any]:
        return {"decision": "proceed"}

    async def act(self, ctx: AgentContext) -> None:
        ctx.final_action = {"action": "done"}


class _CounterRole(BaseAgentRole):
    """Role that increments a shared counter on each pipeline stage."""

    def __init__(self, counter: list[int]) -> None:
        self._counter = counter

    @property
    def role_name(self) -> str:
        return "counter"

    @property
    def capabilities(self) -> list[str]:
        return ["count"]

    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        self._counter.append(0)
        return {}

    async def reason(self, ctx: AgentContext) -> dict[str, Any]:
        self._counter.append(1)
        return {}

    async def act(self, ctx: AgentContext) -> None:
        self._counter.append(2)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBaseRole:
    """Test suite for BaseAgentRole, RoleCard, and RolePipeline."""

    # ── 1. ABC cannot be instantiated directly ─────────────────────────

    def test_abc_cannot_instantiate(self) -> None:
        """BaseAgentRole raises TypeError when instantiated directly."""
        with pytest.raises(TypeError, match="abstract"):
            BaseAgentRole()  # type: ignore[abstract]

    # ── 2. Concrete subclass works ─────────────────────────────────────

    async def test_concrete_role_calls_all_methods(self) -> None:
        """A concrete role can be instantiated and its methods invoked."""
        ctx: AgentContext = mock.MagicMock()
        role = _ConcreteRole()

        obs = await role.observe(ctx)
        assert obs == {"observed": True}

        dec = await role.reason(ctx)
        assert dec == {"decision": "proceed"}

        await role.act(ctx)
        assert ctx.final_action == {"action": "done"}

    # ── 3. RoleCard serialisation via asdict ───────────────────────────

    def test_role_card_asdict(self) -> None:
        """RoleCard can be serialised to a plain dict."""
        card = RoleCard(
            name="explorer",
            capabilities=["scan", "map"],
            input_keys=["probe_state"],
            output_keys=["map_data"],
            description="Explores the game world",
        )
        d = asdict(card)
        assert d == {
            "name": "explorer",
            "capabilities": ["scan", "map"],
            "input_keys": ["probe_state"],
            "output_keys": ["map_data"],
            "description": "Explores the game world",
        }

    # ── 4. to_card() returns correct RoleCard ──────────────────────────

    def test_to_card_returns_role_card(self) -> None:
        """BaseAgentRole.to_card() builds a RoleCard from properties."""
        role = _ConcreteRole()
        card = role.to_card()

        assert isinstance(card, RoleCard)
        assert card.name == "test_role"
        assert card.capabilities == ["observe", "reason", "act"]

    # ── 5. RolePipeline runs roles in order ────────────────────────────

    async def test_run_pipeline_executes_in_order(self) -> None:
        """Pipeline runs observe→reason→act sequentially for each role."""
        ctx: AgentContext = mock.MagicMock()
        counter: list[int] = []
        roles = [_CounterRole(counter), _CounterRole(counter)]

        await run_pipeline(ctx, roles)

        # Two roles × three stages each = 6 entries
        assert len(counter) == 6
        # First role: 0 (observe), 1 (reason), 2 (act)
        assert counter[0:3] == [0, 1, 2]
        # Second role: 0, 1, 2
        assert counter[3:6] == [0, 1, 2]

    # ── 6. RolePipeline handles empty list ─────────────────────────────

    async def test_run_pipeline_empty_roles(self) -> None:
        """Pipeline with an empty list is a no-op."""
        ctx: AgentContext = mock.MagicMock()
        await run_pipeline(ctx, [])
        # No roles — ctx should not have been touched
        ctx.final_action = {"action": "should_not_be_overwritten"}
        # Just verify we got here without error
        assert ctx.final_action == {"action": "should_not_be_overwritten"}

    # ── 7. capabilities is list[str] ───────────────────────────────────

    def test_capabilities_is_list_of_str(self) -> None:
        """capabilities property returns a list of strings."""
        role = _ConcreteRole()
        caps = role.capabilities
        assert isinstance(caps, list)
        assert all(isinstance(c, str) for c in caps)
        assert caps == ["observe", "reason", "act"]


# ---------------------------------------------------------------------------
# TestMemoryCurator
# ---------------------------------------------------------------------------


class TestMemoryCurator:
    """Test suite for MemoryCurator role — cross-session memory orchestration."""

    # ── 1. role_name property ───────────────────────────────────────────

    async def test_role_name(self) -> None:
        """MemoryCurator.role_name returns 'MemoryCurator'."""
        curator = MemoryCurator()
        assert curator.role_name == "MemoryCurator"

    # ── 2. capabilities property ────────────────────────────────────────

    async def test_capabilities(self) -> None:
        """MemoryCurator.capabilities returns the expected tag list."""
        curator = MemoryCurator()
        assert curator.capabilities == [
            "cross-session-memory",
            "knowledge-extraction",
            "memory-lifecycle-management",
        ]

    # ── 3. observe extracts session_phase ───────────────────────────────

    async def test_observe_detects_session_phase(self) -> None:
        """observe extracts phase and session_id from ctx.metadata."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {"session_phase": "start", "session_id": "abc123"}
        curator = MemoryCurator()
        obs = await curator.observe(ctx)
        assert obs == {"phase": "start", "session_id": "abc123"}

    # ── 4. observe defaults to mid ──────────────────────────────────────

    async def test_observe_defaults_to_mid(self) -> None:
        """observe defaults to 'mid' phase when session_phase is missing."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {}
        curator = MemoryCurator()
        obs = await curator.observe(ctx)
        assert obs == {"phase": "mid", "session_id": None}

    # ── 5. phase start loads previous sessions ──────────────────────────

    async def test_phase_start_loads_previous_sessions(self) -> None:
        """At phase 'start', episodic.find_similar is called."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "session_phase": "start",
            "game_id": "game_001",
            "session_id": "s1",
        }

        episodic = mock.AsyncMock()
        episodic.find_similar = mock.AsyncMock(return_value=[{"id": "prev1"}])

        curator = MemoryCurator(episodic_memory=episodic)
        reasoning = await curator.reason(ctx)

        episodic.find_similar.assert_awaited_once_with("game_001", top_k=3)
        assert reasoning["previous_sessions"] == [{"id": "prev1"}]

    # ── 6. phase start loads knowledge ──────────────────────────────────

    async def test_phase_start_loads_knowledge(self) -> None:
        """At phase 'start', semantic.query is called."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "session_phase": "start",
            "game_id": "game_001",
            "session_id": "s1",
        }

        episodic = mock.AsyncMock()
        episodic.find_similar = mock.AsyncMock(return_value=[])
        semantic = mock.AsyncMock()
        semantic.query = mock.AsyncMock(
            return_value=[{"content": "strategy_tip"}]
        )

        curator = MemoryCurator(
            episodic_memory=episodic, semantic_memory=semantic
        )
        reasoning = await curator.reason(ctx)

        semantic.query.assert_awaited_once_with(
            "game strategy", game_id="game_001", top_k=3
        )
        assert reasoning["relevant_knowledge"] == [{"content": "strategy_tip"}]

    # ── 7. phase start matches procedural rule ──────────────────────────

    async def test_phase_start_matches_procedural_rule(self) -> None:
        """At phase 'start', procedural.match is called."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "session_phase": "start",
            "game_id": "game_001",
            "session_id": "s1",
        }

        episodic = mock.AsyncMock()
        episodic.find_similar = mock.AsyncMock(return_value=[])
        procedural = mock.MagicMock()
        rule = mock.MagicMock()
        rule.name = "escape_stuck"
        procedural.match = mock.MagicMock(return_value=rule)

        curator = MemoryCurator(
            episodic_memory=episodic, procedural_memory=procedural
        )
        reasoning = await curator.reason(ctx)

        procedural.match.assert_called_once_with(
            ctx, getattr(ctx, "working_memory", None)
        )
        assert reasoning["matched_rule"] is rule

    # ── 8. phase end calls end_session ──────────────────────────────────

    async def test_phase_end_calls_end_session(self) -> None:
        """At phase 'end', episodic.end_session is called with result."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "session_phase": "end",
            "session_id": "s1",
            "result": "win",
            "score": 100.0,
        }

        episodic = mock.AsyncMock()
        episodic.end_session = mock.AsyncMock()
        episodic.summarize_session = mock.MagicMock(return_value="summary")

        curator = MemoryCurator(episodic_memory=episodic)
        reasoning = await curator.reason(ctx)

        episodic.end_session.assert_awaited_once_with("s1", "win", 100.0)
        assert reasoning["session_summary"] == "summary"

    # ── 9. phase end calls extract_from_session ─────────────────────────

    async def test_phase_end_calls_extract_from_session(self) -> None:
        """At phase 'end', semantic.extract_from_session is called."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "session_phase": "end",
            "session_id": "s1",
            "game_id": "game_001",
            "result": "lose",
        }

        episodic = mock.AsyncMock()
        episodic.end_session = mock.AsyncMock()
        episodic.summarize_session = mock.MagicMock(
            return_value="step 1: action=wait"
        )
        semantic = mock.AsyncMock()
        semantic.extract_from_session = mock.AsyncMock(return_value=3)

        curator = MemoryCurator(
            episodic_memory=episodic, semantic_memory=semantic
        )
        reasoning = await curator.reason(ctx)

        semantic.extract_from_session.assert_awaited_once_with(
            "step 1: action=wait", "game_001"
        )
        assert reasoning["knowledge_extracted"] == 3

    # ── 10. act writes previous_sessions to metadata ────────────────────

    async def test_act_writes_previous_sessions_to_metadata(self) -> None:
        """act writes previous_sessions into ctx.metadata at start."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "session_phase": "start",
            "game_id": "game_001",
            "session_id": "s1",
        }

        episodic = mock.AsyncMock()
        episodic.find_similar = mock.AsyncMock(
            return_value=[{"id": "prev1"}]
        )

        curator = MemoryCurator(episodic_memory=episodic)
        await curator.act(ctx)

        assert ctx.metadata["previous_sessions"] == [{"id": "prev1"}]

    # ── 11. act writes relevant_knowledge to metadata ───────────────────

    async def test_act_writes_relevant_knowledge_to_metadata(self) -> None:
        """act writes relevant_knowledge into ctx.metadata at start."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "session_phase": "start",
            "game_id": "game_001",
            "session_id": "s1",
        }

        episodic = mock.AsyncMock()
        episodic.find_similar = mock.AsyncMock(return_value=[])
        semantic = mock.AsyncMock()
        semantic.query = mock.AsyncMock(
            return_value=[{"content": "tip"}]
        )

        curator = MemoryCurator(
            episodic_memory=episodic, semantic_memory=semantic
        )
        await curator.act(ctx)

        assert ctx.metadata["relevant_knowledge"] == [{"content": "tip"}]

    # ── 12. missing memory components handled gracefully ────────────────

    async def test_handles_missing_memory_components_gracefully(self) -> None:
        """No crash when all memory components are None."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "session_phase": "start",
            "game_id": "game_001",
            "session_id": "s1",
        }

        curator = MemoryCurator()
        reasoning = await curator.reason(ctx)

        # With no episodic, start block is skipped entirely
        assert reasoning["phase"] == "start"
        assert "previous_sessions" not in reasoning
        assert "relevant_knowledge" not in reasoning
        assert "matched_rule" not in reasoning

    # ── 13. phase mid does nothing ──────────────────────────────────────

    async def test_phase_mid_does_nothing(self) -> None:
        """Phase 'mid' does not call any memory method."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {"session_phase": "mid", "session_id": "s1"}

        episodic = mock.AsyncMock()
        semantic = mock.AsyncMock()
        procedural = mock.MagicMock()

        curator = MemoryCurator(
            episodic_memory=episodic,
            semantic_memory=semantic,
            procedural_memory=procedural,
        )
        reasoning = await curator.reason(ctx)

        assert reasoning["phase"] == "mid"
        episodic.find_similar.assert_not_called()
        episodic.end_session.assert_not_called()
        semantic.query.assert_not_called()
        semantic.extract_from_session.assert_not_called()
        procedural.match.assert_not_called()

    # ── 14. phase end without session_id is safe ────────────────────────

    async def test_phase_end_without_session_id_is_safe(self) -> None:
        """Phase 'end' with None session_id does not call end_session."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "session_phase": "end",
            "session_id": None,
            "game_id": "game_001",
        }

        episodic = mock.AsyncMock()
        curator = MemoryCurator(episodic_memory=episodic)
        reasoning = await curator.reason(ctx)

        assert reasoning["phase"] == "end"
        episodic.end_session.assert_not_called()


# ---------------------------------------------------------------------------
# StateMapper tests
# ---------------------------------------------------------------------------


class TestStateMapper:
    """Test suite for the StateMapper role."""

    # ── 1. Role identity ─────────────────────────────────────────────

    def test_role_name_is_state_mapper(self) -> None:
        """role_name property returns 'StateMapper'."""
        mapper = StateMapper()
        assert mapper.role_name == "StateMapper"

    def test_capabilities_includes_visual_state_extraction(self) -> None:
        """capabilities list includes expected tags."""
        mapper = StateMapper()
        assert "visual-state-extraction" in mapper.capabilities
        assert "semantic-memory-lookup" in mapper.capabilities
        assert "scene-element-detection" in mapper.capabilities

    # ── 2. observe ──────────────────────────────────────────────────

    async def test_observe_returns_dict_with_probe_state(self) -> None:
        """observe returns a dict containing the probe_state value."""
        ctx: AgentContext = mock.MagicMock()
        ctx.probe_state = {"keyNumbers": {"score": 42}, "ready": True}
        ctx.screenshot = None

        mapper = StateMapper()
        obs = await mapper.observe(ctx)

        assert isinstance(obs, dict)
        assert obs["probe_state"] == {"keyNumbers": {"score": 42}, "ready": True}

    async def test_observe_reports_screenshot_presence(self) -> None:
        """observe correctly reports whether a screenshot is present."""
        mapper = StateMapper()

        # When screenshot is present
        ctx_with: AgentContext = mock.MagicMock()
        ctx_with.screenshot = b"fake_png_bytes"
        ctx_with.probe_state = {}
        obs_with = await mapper.observe(ctx_with)
        assert obs_with["screenshot"] is True

        # When screenshot is None
        ctx_without: AgentContext = mock.MagicMock()
        ctx_without.screenshot = None
        ctx_without.probe_state = {}
        obs_without = await mapper.observe(ctx_without)
        assert obs_without["screenshot"] is False

    # ── 3. reason ───────────────────────────────────────────────────

    async def test_reason_queries_semantic_memory(self) -> None:
        """reason calls semantic_memory.query with expected arguments."""
        ctx: AgentContext = mock.MagicMock()
        ctx.screenshot = b"fake_png_bytes"
        ctx.probe_state = {"keyNumbers": {"lives": 3}}
        ctx.metadata = {"game_id": "SSD_00848P01"}

        mock_memory = mock.MagicMock()
        mock_memory.query = mock.AsyncMock(
            return_value=[{"content": "early phase", "confidence": 0.9}]
        )

        mapper = StateMapper(vlm_predict_fn=mock.MagicMock(), semantic_memory=mock_memory)
        result = await mapper.reason(ctx)

        assert "known_patterns" in result
        assert len(result["known_patterns"]) == 1
        assert result["known_patterns"][0]["content"] == "early phase"
        mock_memory.query.assert_awaited_once()

    async def test_reason_bypasses_vlm_when_no_predict_fn(self) -> None:
        """reason does not crash when vlm_predict_fn is None."""
        ctx: AgentContext = mock.MagicMock()
        ctx.screenshot = b"fake_png_bytes"
        ctx.probe_state = {"keyNumbers": {"score": 100}}

        mapper = StateMapper(vlm_predict_fn=None, semantic_memory=None)
        result = await mapper.reason(ctx)

        assert result["visual_struct"] == {}
        assert result["known_patterns"] == []
        assert result["elements_detected"] == []

    async def test_handles_missing_semantic_memory_gracefully(self) -> None:
        """reason returns empty known_patterns when no semantic_memory."""
        ctx: AgentContext = mock.MagicMock()
        ctx.screenshot = b"fake_png_bytes"
        ctx.probe_state = {"keyNumbers": {"score": 100}}

        mapper = StateMapper(vlm_predict_fn=mock.MagicMock(), semantic_memory=None)
        result = await mapper.reason(ctx)

        assert "known_patterns" in result
        assert result["known_patterns"] == []

    # ── 4. act ──────────────────────────────────────────────────────

    async def test_act_writes_visual_struct_to_ctx(self) -> None:
        """act sets ctx.visual_struct when VLM extraction succeeds."""
        ctx: AgentContext = mock.MagicMock()
        ctx.screenshot = b"fake_png_bytes"
        ctx.probe_state = {}
        ctx.metadata = {}
        ctx.visual_struct = None

        # Mock the src.inference module at sys.modules level to bypass
        # the fastapi import failure in src.inference.__init__.py
        mock_extract = mock.MagicMock(
            return_value={"has_arrow": True, "has_target": False}
        )
        mock_struct_mod = mock.MagicMock()
        mock_struct_mod.extract_visual_structure = mock_extract
        mock_inf = mock.MagicMock()
        mock_inf.struct_extractor = mock_struct_mod

        mapper = StateMapper(
            vlm_predict_fn=mock.MagicMock(return_value={"reason": '{"has_arrow": true}'}),
            semantic_memory=None,
        )

        with mock.patch.dict(
            sys.modules,
            {
                "src.inference": mock_inf,
                "src.inference.struct_extractor": mock_struct_mod,
            },
        ):
            with mock.patch("PIL.Image.open") as mock_img:
                mock_img.return_value.convert.return_value = mock.MagicMock()
                await mapper.act(ctx)

        assert ctx.visual_struct == {"has_arrow": True, "has_target": False}

    async def test_act_writes_relevant_knowledge_to_ctx_metadata(self) -> None:
        """act sets metadata.relevant_knowledge when patterns are found."""
        ctx: AgentContext = mock.MagicMock()
        ctx.screenshot = b"fake_png_bytes"
        ctx.probe_state = {"keyNumbers": {"score": 200}}
        ctx.metadata = {}
        ctx.visual_struct = None

        mock_memory = mock.MagicMock()
        mock_memory.query = mock.AsyncMock(
            return_value=[{"content": "mid-game phase", "confidence": 0.85}]
        )

        mapper = StateMapper(
            vlm_predict_fn=mock.MagicMock(), semantic_memory=mock_memory
        )
        await mapper.act(ctx)

        assert "relevant_knowledge" in ctx.metadata
        assert ctx.metadata["relevant_knowledge"] == [
            {"content": "mid-game phase", "confidence": 0.85}
        ]


# ---------------------------------------------------------------------------
# Verifier tests
# ---------------------------------------------------------------------------


class TestVerifier:
    """Test suite for Verifier role and Verdict dataclass."""

    # ── 1. Role identity ────────────────────────────────────────────────

    def test_role_name(self) -> None:
        """role_name property returns 'Verifier'."""
        assert Verifier().role_name == "Verifier"

    # ── 2. Capabilities ─────────────────────────────────────────────────

    def test_capabilities(self) -> None:
        """capabilities list includes all expected tags."""
        caps = Verifier().capabilities
        assert isinstance(caps, list)
        assert "action-validation" in caps
        assert "stuck-detection" in caps
        assert "progress-tracking" in caps

    # ── 3. Position change detection ────────────────────────────────────

    async def test_observe_detects_position_change(self) -> None:
        """Observe returns position_changed=True when player moved."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "prev_probe_state": {
                "player": {"worldPosition": {"x": 0.0, "z": 0.0}}
            }
        }
        ctx.probe_state = {
            "player": {"worldPosition": {"x": 10.5, "z": 20.3}}
        }
        verifier = Verifier()
        obs = await verifier.observe(ctx)
        assert obs["position_changed"] is True

    # ── 4. No position change ──────────────────────────────────────────

    async def test_observe_detects_no_position_change(self) -> None:
        """Observe returns position_changed=False when player stayed."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "prev_probe_state": {
                "player": {"worldPosition": {"x": 5.0, "z": 5.0}}
            }
        }
        ctx.probe_state = {
            "player": {"worldPosition": {"x": 5.0, "z": 5.0}}
        }
        verifier = Verifier()
        obs = await verifier.observe(ctx)
        assert obs["position_changed"] is False

    # ── 5. Score increase ──────────────────────────────────────────────

    async def test_observe_detects_score_increase(self) -> None:
        """Observe detects score increase from keyNumbers."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "prev_probe_state": {"keyNumbers": {"score": 100.0}}
        }
        ctx.probe_state = {"keyNumbers": {"score": 150.0}}
        verifier = Verifier()
        obs = await verifier.observe(ctx)
        assert obs["score_delta"] == 50.0

    # ── 6. Escape rotate when stuck ────────────────────────────────────

    async def test_reason_recommends_escape_rotate_when_stuck(self) -> None:
        """Reason recommends escape_rotate when is_stuck is True."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "prev_probe_state": {
                "player": {"worldPosition": {"x": 0.0, "z": 0.0}}
            }
        }
        ctx.probe_state = {
            "player": {"worldPosition": {"x": 0.0, "z": 0.0}}
        }
        ctx.working_memory = mock.MagicMock()
        ctx.working_memory.is_stuck = True
        ctx.working_memory.stuck_streak = 5
        verifier = Verifier()
        verdict = await verifier.reason(ctx)
        assert verdict.stuck is True
        assert verdict.recommendation == "escape_rotate"
        assert verdict.action_effective is False

    # ── 7. Reobserve when not effective ────────────────────────────────

    async def test_reason_recommends_reobserve_when_not_effective(self) -> None:
        """Reason recommends reobserve when action had no effect."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "prev_probe_state": {
                "player": {"worldPosition": {"x": 0.0, "z": 0.0}}
            }
        }
        ctx.probe_state = {
            "player": {"worldPosition": {"x": 0.0, "z": 0.0}}
        }
        ctx.working_memory = mock.MagicMock()
        ctx.working_memory.is_stuck = False
        ctx.working_memory.stuck_streak = 0
        verifier = Verifier()
        verdict = await verifier.reason(ctx)
        assert verdict.action_effective is False
        assert verdict.recommendation == "reobserve"

    # ── 8. No recommendation when everything is fine ───────────────────

    async def test_reason_returns_none_when_everything_fine(self) -> None:
        """Reason returns no recommendation when action was effective."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "prev_probe_state": {
                "player": {"worldPosition": {"x": 0.0, "z": 0.0}}
            }
        }
        ctx.probe_state = {
            "player": {"worldPosition": {"x": 10.0, "z": 20.0}}
        }
        ctx.working_memory = mock.MagicMock()
        ctx.working_memory.is_stuck = False
        ctx.working_memory.stuck_streak = 0
        verifier = Verifier()
        verdict = await verifier.reason(ctx)
        assert verdict.action_effective is True
        assert verdict.recommendation is None

    # ── 9. Act writes verdict to metadata ──────────────────────────────

    async def test_act_writes_verdict_to_metadata(self) -> None:
        """Act stores the Verdict in ctx.metadata['verdict']."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {"prev_probe_state": {}}
        ctx.probe_state = {}
        ctx.errors = []
        ctx.working_memory = mock.MagicMock()
        ctx.working_memory.is_stuck = False
        ctx.working_memory.stuck_streak = 0
        verifier = Verifier()
        await verifier.act(ctx)
        assert "verdict" in ctx.metadata
        assert isinstance(ctx.metadata["verdict"], Verdict)

    # ── 10. Act appends to errors when recommendation exists ───────────

    async def test_act_appends_to_errors_when_recommendation(self) -> None:
        """Act appends an error message when recommendation is present."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "prev_probe_state": {
                "player": {"worldPosition": {"x": 0.0, "z": 0.0}}
            }
        }
        ctx.probe_state = {
            "player": {"worldPosition": {"x": 0.0, "z": 0.0}}
        }
        ctx.errors = []
        ctx.working_memory = mock.MagicMock()
        ctx.working_memory.is_stuck = True
        ctx.working_memory.stuck_streak = 5
        verifier = Verifier()
        await verifier.act(ctx)
        assert len(ctx.errors) == 1
        assert "Verifier:" in ctx.errors[0]
        assert "escape_rotate" in ctx.errors[0]

    # ── 11. Confidence decreases with repeated failures ────────────────

    async def test_confidence_decreases_with_repeated_failures(self) -> None:
        """Confidence drops as stuck_streak increases."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {"prev_probe_state": {}}
        ctx.probe_state = {}
        ctx.working_memory = mock.MagicMock()
        ctx.working_memory.is_stuck = False

        # stuck_streak=3 → max(0.5, 1.0 - 0.3) = 0.7
        ctx.working_memory.stuck_streak = 3
        v1 = await Verifier().reason(ctx)
        assert v1.confidence == pytest.approx(0.7)

        # stuck_streak=8 → max(0.5, 1.0 - 0.8) = 0.5
        ctx.working_memory.stuck_streak = 8
        v2 = await Verifier().reason(ctx)
        assert v2.confidence == pytest.approx(0.5)

    # ── 12. Missing working memory ─────────────────────────────────────

    async def test_handles_missing_working_memory(self) -> None:
        """No crash when working_memory is None."""
        ctx: AgentContext = mock.MagicMock()
        ctx.metadata = {
            "prev_probe_state": {
                "player": {"worldPosition": {"x": 0.0, "z": 0.0}}
            }
        }
        ctx.probe_state = {
            "player": {"worldPosition": {"x": 0.0, "z": 0.0}}
        }
        ctx.working_memory = None
        verifier = Verifier()
        verdict = await verifier.reason(ctx)
        assert verdict.stuck is False
        assert verdict.action_effective is False
        assert verdict.recommendation == "reobserve"
