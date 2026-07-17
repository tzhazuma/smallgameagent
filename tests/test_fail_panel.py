"""Tests for fail-panel dismissal (HybridAgent._maybe_dismiss_fail_panel)."""

from __future__ import annotations

from unittest import mock

import pytest

from src.agent.context import AgentContext
from src.agent.hybrid_agent import HybridAgent
from src.agent.probe_adapter import ProbeAdapter

BTN_RETRY = {
    "name": "retryBtn",
    "path": "/main/Canvas/UI/LosePanel/retryBtn",
    "designPosition": {"x": 360.0, "y": 527.171},
    "designSize": {"width": 720.0, "height": 1560.0},
    "dpr": 3,
}
BTN_DOWNLOAD = {
    "name": "downloadBtn",
    "path": "/main/Canvas/UI/LosePanel/downloadBtn",
    "designPosition": {"x": 360.0, "y": 637.334},
    "designSize": {"width": 720.0, "height": 1560.0},
    "dpr": 3,
}


def _agent() -> HybridAgent:
    return HybridAgent(mode="rule", game_id="SSD_00461P01")


def _runner() -> mock.AsyncMock:
    runner = mock.AsyncMock()
    runner._page = mock.Mock()
    runner._page.viewport_size = {"width": 375, "height": 812}
    return runner


class TestFailPanelDismissal:
    async def test_tap_retry_with_design_to_css_mapping(self) -> None:
        agent = _agent()
        ctx = AgentContext()
        probe = mock.AsyncMock(spec=ProbeAdapter)
        probe.find_panel_buttons.return_value = [BTN_DOWNLOAD, BTN_RETRY]
        runner = _runner()

        state = {"ready": True, "keyNumbers": {"_failCount": 1}}
        handled = await agent._maybe_dismiss_fail_panel(probe, runner, state, ctx)

        assert handled is True
        runner.tap.assert_awaited_once()
        _, kwargs = runner.tap.call_args
        # retryBtn: css_x = 360/720*375 = 187.5, css_y = (1-527.171/1560)*812 ≈ 537.6
        assert kwargs["x"] == pytest.approx(187.5)
        assert kwargs["y"] == pytest.approx(537.6, abs=0.5)
        assert ctx.metadata["fail_panel_taps"] == 1

    async def test_skips_when_only_download_button(self) -> None:
        agent = _agent()
        ctx = AgentContext()
        probe = mock.AsyncMock(spec=ProbeAdapter)
        probe.find_panel_buttons.return_value = [BTN_DOWNLOAD]
        runner = _runner()

        state = {"ready": True, "keyNumbers": {"_failCount": 1}}
        handled = await agent._maybe_dismiss_fail_panel(probe, runner, state, ctx)

        assert handled is False
        runner.tap.assert_not_awaited()

    async def test_no_flip_no_tap(self) -> None:
        agent = _agent()
        ctx = AgentContext()
        ctx.metadata["prev_fail_count"] = 1
        probe = mock.AsyncMock(spec=ProbeAdapter)
        runner = _runner()

        state = {"ready": True, "keyNumbers": {"_failCount": 1}}
        assert await agent._maybe_dismiss_fail_panel(probe, runner, state, ctx) is False
        probe.find_panel_buttons.assert_not_awaited()
        runner.tap.assert_not_awaited()

    async def test_flip_without_buttons(self) -> None:
        agent = _agent()
        ctx = AgentContext()
        probe = mock.AsyncMock(spec=ProbeAdapter)
        probe.find_panel_buttons.return_value = []
        runner = _runner()

        state = {"ready": True, "keyNumbers": {"_failCount": 2}}
        assert await agent._maybe_dismiss_fail_panel(probe, runner, state, ctx) is False
        runner.tap.assert_not_awaited()

    async def test_missing_design_size_returns_none(self) -> None:
        agent = _agent()
        ctx = AgentContext()
        probe = mock.AsyncMock(spec=ProbeAdapter)
        bad = dict(BTN_RETRY)
        bad["designSize"] = {"width": 0, "height": 0}
        probe.find_panel_buttons.return_value = [bad]
        runner = _runner()

        state = {"ready": True, "keyNumbers": {"GameManager._failCount": 1}}
        assert await agent._maybe_dismiss_fail_panel(probe, runner, state, ctx) is False


class TestFindPanelButtonsAdapter:
    async def test_returns_list(self) -> None:
        adapter = ProbeAdapter()
        page = mock.AsyncMock()
        page.evaluate.return_value = [BTN_RETRY]
        out = await adapter.find_panel_buttons(page, r"LosePanel")
        assert len(out) == 1
        assert "findPanelButtons" in page.evaluate.call_args[0][0]

    async def test_not_ready_returns_empty(self) -> None:
        adapter = ProbeAdapter()
        page = mock.AsyncMock()
        page.evaluate.return_value = None
        assert await adapter.find_panel_buttons(page) == []
