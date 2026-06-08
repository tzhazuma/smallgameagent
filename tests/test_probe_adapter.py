"""Tests for src.agent.probe_adapter."""

from __future__ import annotations


import pytest

from src.agent.probe_adapter import ProbeAdapter


# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------

SAMPLE_OBSERVE = {
    "ready": True,
    "done": False,
    "win": False,
    "doneReason": "",
    "player": {"name": "Player", "path": "Canvas/Player"},
    "managers": [
        {
            "className": "GameManager",
            "nodeName": "GameManager",
            "nodePath": "Canvas/GameManager",
            "flags": {"isGameOver": False},
            "numbers": {"score": 100},
            "interestingKeys": ["score"],
        }
    ],
    "activeUiNodes": [],
    "interestingNodes": [],
    "numbers": {"score": 100},
    "flags": {"isGameOver": False},
    "raw": {"nodeCount": 42, "events": []},
}

SAMPLE_OBSERVE_NOT_READY = {"ready": False, "done": False, "win": False}

SAMPLE_OBSERVE_FAST = {
    "ready": True,
    "done": False,
    "win": False,
    "player": {"name": "Player"},
    "keyNumbers": {"score": 200},
    "keyFlags": {"isWin": False},
    "harvestChain": {"nodes": []},
    "sellChain": {"nodes": []},
    "machineChain": {"nodes": []},
    "guideSummary": {"managers": [], "controllers": []},
    "completionSummary": {"endState": {"done": False}},
}

SAMPLE_OBSERVE_FAST_WIN = {
    **SAMPLE_OBSERVE_FAST,
    "done": True,
    "win": True,
    "keyFlags": {"isWin": True},
}

SAMPLE_GUIDE_SUMMARY = {
    "managers": [],
    "controllers": [],
    "likelyCurrentTarget": {"name": "GuideTarget", "path": "Canvas/GuideTarget"},
    "candidateTargets": [],
}

SAMPLE_COMPLETION_SUMMARY = {
    "ready": True,
    "endState": {"done": False, "win": False},
    "activeEndNodes": [],
}

SAMPLE_DUMP_SCENE = {
    "ready": True,
    "nodeCount": 3,
    "nodes": [
        {"name": "Canvas", "path": "Canvas"},
        {"name": "Player", "path": "Canvas/Player"},
        {"name": "Enemy", "path": "Canvas/Enemy"},
    ],
}

SAMPLE_MOVE_OK = {"ok": True, "backend": "cocos-actor-move", "elapsedMs": 260}
SAMPLE_MOVE_FAIL = {"ok": False, "reason": "Actor component not found"}

SAMPLE_COMPONENT_SNAPSHOT = [
    {
        "id": "Actor@Canvas/Player",
        "className": "Actor",
        "nodeName": "Player",
        "nodePath": "Canvas/Player",
        "active": True,
        "enabled": True,
        "primitiveFields": {},
        "numericFields": {"speed": 5.0},
        "booleanFields": {"isLimitMove": False},
        "stringFields": {},
        "tags": ["collider"],
    },
    {
        "id": "GameManager@Canvas/GameManager",
        "className": "GameManager",
        "nodeName": "GameManager",
        "nodePath": "Canvas/GameManager",
        "active": True,
        "enabled": True,
        "primitiveFields": {},
        "numericFields": {"score": 100},
        "booleanFields": {"isGameOver": False},
        "stringFields": {},
        "tags": ["manager"],
    },
]


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class _FakePage:
    """Minimal Playwright Page mock that captures evaluate / add_init_script."""

    def __init__(self) -> None:
        self._init_scripts: list[str] = []
        self._evals: list[str] = []
        self._next_result: object = None
        self._result_queue: list[object] = []

    async def add_init_script(self, script: str) -> None:
        self._init_scripts.append(script)

    async def evaluate(self, expression: str) -> object:
        self._evals.append(expression)
        if self._result_queue:
            return self._result_queue.pop(0)
        return self._next_result


def _eval_side_effect(*results: object):
    """Create a coroutine-based side-effect that returns each result once."""

    async def side_effect(_expression: str) -> object:
        return results[0] if results else None

    return side_effect


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProbeAdapterConstructor:
    def test_loads_probe_source(self) -> None:
        adapter = ProbeAdapter()
        assert isinstance(adapter._source, str)
        assert len(adapter._source) > 1000
        assert "installPlayableAgentProbe" in adapter._source
        assert "window.__playableAgentProbe" in adapter._source

    def test_source_is_non_empty(self) -> None:
        adapter = ProbeAdapter()
        assert adapter._source.strip()

    def test_source_is_iife(self) -> None:
        adapter = ProbeAdapter()
        source = adapter._source.strip()
        assert source.startswith("("), f"Expected IIFE wrapper, got: {source[:80]}"
        # The IIFE ends with })();
        assert "})();" in source or ")()" in source[-10:]

    def test_default_path_exists(self) -> None:

        from src.agent.probe_adapter import DEFAULT_PROBE_PATH

        assert DEFAULT_PROBE_PATH.exists(), f"Probe not found at {DEFAULT_PROBE_PATH}"

    def test_raises_on_nonexistent_path(self, tmp_path) -> None:
        bad = tmp_path / "nonexistent.js"
        with pytest.raises(FileNotFoundError):
            ProbeAdapter(probe_source_path=str(bad))

    def test_raises_on_empty_file(self, tmp_path) -> None:
        empty = tmp_path / "empty.js"
        empty.write_text(
            "export const browserProbeSource = String.raw `\n`;"
        )
        with pytest.raises(ValueError, match="Empty probe source"):
            ProbeAdapter(probe_source_path=str(empty))


class TestInject:
    async def test_inject_calls_add_init_script(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        await adapter.inject(page)
        assert len(page._init_scripts) == 1
        assert "installPlayableAgentProbe" in page._init_scripts[0]

    async def test_inject_calls_evaluate(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        await adapter.inject(page)
        assert len(page._evals) == 1
        assert "installPlayableAgentProbe" in page._evals[0]


class TestObserve:
    async def test_observe_returns_dict(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_OBSERVE
        result = await adapter.observe(page)
        assert result == SAMPLE_OBSERVE
        assert result["ready"] is True

    async def test_observe_not_ready(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_OBSERVE_NOT_READY
        result = await adapter.observe(page)
        assert result["ready"] is False

    async def test_observe_handles_probe_missing(self) -> None:
        """When probe is not installed, evaluate returns None."""
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = None
        result = await adapter.observe(page)
        assert result == {"ready": False}

    async def test_observe_handles_non_dict_result(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = "garbage"
        result = await adapter.observe(page)
        assert result == {"ready": False}


class TestObserveFast:
    async def test_observe_fast_returns_dict(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_OBSERVE_FAST
        result = await adapter.observe_fast(page)
        assert result["ready"] is True
        assert "harvestChain" in result

    async def test_observe_fast_not_ready(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = {"ready": False}
        result = await adapter.observe_fast(page)
        assert result["ready"] is False


class TestWaitForReady:
    async def test_returns_on_first_ready(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_OBSERVE
        result = await adapter.wait_for_ready(page, timeout_ms=5000, poll_interval_ms=50)
        assert result["ready"] is True

    async def test_polls_until_ready(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        # First call: not ready; second call: ready.
        page._result_queue = [SAMPLE_OBSERVE_NOT_READY, SAMPLE_OBSERVE]
        result = await adapter.wait_for_ready(page, timeout_ms=5000, poll_interval_ms=50)
        assert result["ready"] is True
        assert len(page._evals) == 2  # two observe() calls

    async def test_times_out(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_OBSERVE_NOT_READY
        result = await adapter.wait_for_ready(page, timeout_ms=200, poll_interval_ms=50)
        assert result == {"ready": False}


class TestMoveByCocos:
    async def test_move_ok(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_MOVE_OK
        result = await adapter.move_by_cocos(page, 1.0, 0.0, 250)
        assert result["ok"] is True
        assert result["backend"] == "cocos-actor-move"

    async def test_move_fail(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_MOVE_FAIL
        result = await adapter.move_by_cocos(page, 1.0, 0.0, 250)
        assert result["ok"] is False

    async def test_move_passes_options(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_MOVE_OK
        await adapter.move_by_cocos(page, 0.0, 1.0, 100, {"priority": "high"})
        # Verify the evaluate call includes the options dict.
        last_eval = page._evals[-1]
        assert "priority" in last_eval


class TestGuideSummary:
    async def test_returns_guide_summary(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_GUIDE_SUMMARY
        result = await adapter.get_guide_summary(page)
        assert "likelyCurrentTarget" in result

    async def test_handles_non_dict(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = None
        result = await adapter.get_guide_summary(page)
        assert result == {"ready": False}


class TestCompletionSummary:
    async def test_returns_completion_summary(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_COMPLETION_SUMMARY
        result = await adapter.get_completion_summary(page)
        assert "endState" in result

    async def test_handles_missing_probe(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = None
        result = await adapter.get_completion_summary(page)
        assert result == {"ready": False}


class TestGetRawSceneGraph:
    async def test_returns_node_list(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_DUMP_SCENE
        result = await adapter.get_raw_scene_graph(page)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0]["name"] == "Canvas"

    async def test_respects_max_nodes(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_DUMP_SCENE
        result = await adapter.get_raw_scene_graph(page, max_nodes=1)
        assert len(result) == 1

    async def test_not_ready_returns_empty(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = {"ready": False}
        result = await adapter.get_raw_scene_graph(page)
        assert result == []


class TestSnapshotComponents:
    async def test_indexes_by_id(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = SAMPLE_COMPONENT_SNAPSHOT
        result = await adapter.snapshot_components(page, "Actor|Manager")
        assert isinstance(result, dict)
        assert "Actor@Canvas/Player" in result
        assert result["Actor@Canvas/Player"]["booleanFields"]["isLimitMove"] is False

    async def test_handles_non_list(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = "not a list"
        result = await adapter.snapshot_components(page, "Actor")
        assert result == {"ready": False}

    async def test_empty_snapshot(self) -> None:
        adapter = ProbeAdapter()
        page = _FakePage()
        page._next_result = []
        result = await adapter.snapshot_components(page, "Actor")
        assert result == {}
