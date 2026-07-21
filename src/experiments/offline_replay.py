#!/usr/bin/env python3
"""Offline replay evaluation on processed-runs trajectories.

Loads ``processed-runs/<game_id>/steps.jsonl`` (the dataset-workflow output),
replays each step through a decision decider without a browser, and compares
predicted actions with the recorded ground-truth actions.

Modes
-----
rule:
    Pure L0 ``RuleEngine`` baseline.
hierarchical:
    ``HierarchicalPlanner`` with L1 disabled (``l1_interval=0``) by default.
    Supports a mock cloud client to verify rule-update wiring offline, or a
    real cloud provider (qwen/kimi/etc.) for L2 strategic planning and rule
    updates.
api-rule:
    Lightweight mode: ask the cloud API for a JSON action directly and compare
    it to the ground truth. Falls back to the rule engine if the API is
    unavailable or unparseable. (``RuleEngine`` does not yet accept injected
    ``RuleSet`` objects, so this mode uses the API as the decider.)

Dataset collection
------------------
With ``--collect-dataset`` each step that has a screenshot is written as one
JSONL record containing ``image`` (screenshot path), ``state`` (adapted probe
state JSON string), and ``action`` (adapted true action JSON string).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Allow running this script directly from the repo root or src/experiments.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.agent.context import AgentContext  # noqa: E402
from src.agent.hierarchical_planner import HierarchicalPlanner  # noqa: E402
from src.agent.llm_agent import LLMAgent  # noqa: E402
from src.agent.memory import EpisodicMemory, ProceduralMemory  # noqa: E402
from src.agent.registry import DecisionRegistry  # noqa: E402
from src.agent.rule_update import RuleParameters  # noqa: E402
from src.agent.strategy_memory import StrategyMemory  # noqa: E402
from src.engine.rules import RuleEngine  # noqa: E402
from src.experiments.game_env import score_trajectory  # noqa: E402

# Import decision makers to register ``multi-bus`` / ``multi-bus-memory``.
import src.agent.decision_makers  # noqa: E402,F401

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
PROCESSED_ROOT = ROOT / "processed-runs"
DEFAULT_GAMES = [
    "SSD_00461P01",
    "SSD_00219P01",
    "SSD_00332P01",
    "SSD_00342P01",
    "SSD_00848P01",
]


def _candidate(node: dict[str, Any]) -> dict[str, Any]:
    """Normalize an interesting/active node into the probe candidate schema."""
    return {
        "name": node.get("name", ""),
        "path": node.get("path", ""),
        "active": bool(node.get("active", True)),
        "worldPosition": node.get("worldPosition") or node.get("world_position") or {"x": 0, "y": 0, "z": 0},
        "screenPosition": node.get("screenPosition") or node.get("screen_position") or {"x": 0, "y": 0},
        "components": node.get("components", []),
    }


def adapt_processed_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a dataset-workflow state snapshot to the probe format.

    The input is the parsed JSON content of
    ``states/step-XXXX-before.json`` (or ``-after.json``).  The output follows
    the schema expected by ``RuleEngine`` and ``HierarchicalPlanner``.
    """
    state = raw.get("state", raw)
    observe = state.get("observe") or {}
    if not observe:
        # Defensive fallback: if the top-level object is the observe block.
        observe = state

    ready = bool(observe.get("ready", state.get("ready", False)))
    # The processed-run probe over-fires done/win on UI nodes (e.g. DownloadBtn).
    # For offline policy evaluation we ignore raw terminal flags so the engine
    # keeps playing; trajectory-level outcomes are scored separately.
    done = False
    win = False
    done_reason = observe.get("doneReason", state.get("doneReason"))

    player_raw = observe.get("player") or {}
    player = {
        "name": player_raw.get("name", ""),
        "path": player_raw.get("path", ""),
        "active": bool(player_raw.get("active", True)),
        "worldPosition": player_raw.get("worldPosition") or {"x": 0, "y": 0, "z": 0},
        "screenPosition": player_raw.get("screenPosition") or {"x": 0, "y": 0},
        "components": player_raw.get("components", []),
    }

    key_numbers = dict(observe.get("numbers") or {})
    key_flags = dict(observe.get("flags") or {})

    candidates: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for node in observe.get("interestingNodes") or []:
        cand = _candidate(node)
        if cand["path"] and cand["path"] in seen_paths:
            continue
        seen_paths.add(cand["path"])
        candidates.append(cand)
    for node in observe.get("activeUiNodes") or []:
        cand = _candidate(node)
        if cand["path"] and cand["path"] in seen_paths:
            continue
        seen_paths.add(cand["path"])
        candidates.append(cand)

    return {
        "ready": ready,
        "done": done,
        "win": win,
        "doneReason": done_reason,
        "player": player,
        "keyNumbers": key_numbers,
        "keyFlags": key_flags,
        "guide_or_target_candidates": candidates,
    }


def load_steps(game_dir: Path) -> list[dict[str, Any]]:
    """Load ``steps.jsonl`` for a processed run."""
    steps_path = game_dir / "steps.jsonl"
    if not steps_path.exists():
        raise FileNotFoundError(f"steps.jsonl not found: {steps_path}")
    with open(steps_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_state(game_dir: Path, rel_path: str | None) -> dict[str, Any] | None:
    """Load a state JSON file relative to the processed run directory."""
    if not rel_path:
        return None
    path = game_dir / rel_path
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _stick_vec(action: dict[str, Any]) -> tuple[float, float]:
    """Extract a normalized direction vector from a recorded action."""
    raw = action.get("raw") or action.get("executed") or action
    stick = raw.get("stick") or {}
    dx = float(stick.get("dx", 0.0))
    dy = float(stick.get("dy", 0.0))
    norm = math.hypot(dx, dy)
    if norm == 0:
        return 0.0, 0.0
    return dx / norm, dy / norm


def _tap_coords(action: dict[str, Any]) -> tuple[float, float] | None:
    """Extract tap screen coordinates from a recorded action."""
    raw = action.get("raw") or action.get("executed") or action
    if "x" in raw and "y" in raw:
        return float(raw["x"]), float(raw["y"])
    to = raw.get("to")
    if isinstance(to, dict) and "x" in to and "y" in to:
        return float(to["x"]), float(to["y"])
    target = action.get("target")
    if isinstance(target, dict) and "x" in target and "y" in target:
        return float(target["x"]), float(target["y"])
    return None


def adapt_ground_truth(action_record: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a recorded dataset action to the current action schema."""
    # Dataset workflows use different top-level keys; prefer the richest one.
    action = (
        action_record.get("action")
        or action_record.get("chosen_action")
        or action_record.get("executed_action")
        or action_record
    )
    if not isinstance(action, dict):
        return None
    atype = action.get("type", "wait")
    duration_ms = int(action.get("duration_ms", 500))

    if atype in ("move_pulse", "move_sequence", "drag"):
        dx, dy = _stick_vec(action)
        return {
            "action": "move",
            "params": {"dx": dx, "dy": dy, "duration_ms": duration_ms},
            "reason": f"gt:{atype}",
        }
    if atype in ("tap", "click"):
        coords = _tap_coords(action)
        if coords is None:
            return {"action": "wait", "params": {"duration_ms": duration_ms}, "reason": "gt:tap:no-coords"}
        return {
            "action": "tap",
            "params": {"x": coords[0], "y": coords[1], "duration_ms": duration_ms},
            "reason": f"gt:{atype}",
        }
    if atype == "wait":
        return {"action": "wait", "params": {"duration_ms": duration_ms}, "reason": "gt:wait"}

    # Unknown action type: treat as wait but preserve original type in reason.
    return {"action": "wait", "params": {"duration_ms": duration_ms}, "reason": f"gt:{atype}:unknown"}


def normalize_action(action: dict[str, Any] | None) -> dict[str, Any]:
    """Return a safe action dict with ``action``, ``params`` and ``reason``."""
    if not isinstance(action, dict):
        return {"action": "wait", "params": {"duration_ms": 500}, "reason": "invalid"}
    out = dict(action)
    out.setdefault("action", "wait")
    out.setdefault("params", {})
    out.setdefault("reason", "")
    return out


def move_cosine(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Cosine similarity between two move action direction vectors."""
    pa = a.get("params") or {}
    pb = b.get("params") or {}
    ax, ay = float(pa.get("dx", 0.0)), float(pa.get("dy", 0.0))
    bx, by = float(pb.get("dx", 0.0)), float(pb.get("dy", 0.0))
    na = math.hypot(ax, ay)
    nb = math.hypot(bx, by)
    if na == 0 or nb == 0:
        return 0.0
    return max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))


def actions_match(pred: dict[str, Any], true: dict[str, Any], tap_tol: float = 15.0) -> tuple[bool, bool, float]:
    """Return (type_match, full_match, move_cosine) for a predicted action."""
    pred = normalize_action(pred)
    true = normalize_action(true)
    ptype = pred.get("action", "wait")
    ttype = true.get("action", "wait")
    type_match = ptype == ttype

    cos = 0.0
    if ptype == "move" and ttype == "move":
        cos = move_cosine(pred, true)

    full_match = False
    if type_match:
        pp = pred.get("params") or {}
        tp = true.get("params") or {}
        if ptype == "wait":
            full_match = True
        elif ptype == "move":
            full_match = cos >= 0.94  # ~20 degrees
        elif ptype == "tap":
            full_match = math.hypot(pp.get("x", 0) - tp.get("x", 0), pp.get("y", 0) - tp.get("y", 0)) <= tap_tol

    return type_match, full_match, cos


@dataclass
class StepRecord:
    """One replay step."""

    step: int
    state: dict[str, Any]
    true_action: dict[str, Any]
    pred_action: dict[str, Any]
    latency_ms: float
    type_match: bool
    action_match: bool
    move_cosine: float
    rule_update_triggered: bool = False
    rule_update_applied: bool = False


class _OfflineWorkingMemory:
    """Lightweight working-memory stand-in for offline replay.

    Satisfies the attribute-based accesses used by the verifier, procedural
    memory, and LLM prompt builder without requiring a full game loop.
    """

    def __init__(
        self,
        *,
        stuck_streak: int = 0,
        stall_streak: int = 0,
        conflict_streak: int = 0,
        last_composite: float = 0.0,
        step_count: int = 0,
    ) -> None:
        self.is_stuck = False
        self.stuck_streak = stuck_streak
        self.stall_streak = stall_streak
        self.conflict_streak = conflict_streak
        self.last_composite = last_composite
        self.step_count = step_count
        self.world_model = SimpleNamespace(stats=lambda: {"stale_events": 0})

    def to_prompt_context(self, n: int = 5) -> str:
        return "[]"


def build_fake_context(
    step_number: int,
    state: dict[str, Any],
    last_composite: float,
    stall_streak: int,
    conflict_streak: int,
    visual_struct: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> AgentContext:
    """Create an ``AgentContext`` that satisfies hierarchical planner needs."""
    wm = _OfflineWorkingMemory(
        stuck_streak=stall_streak,
        stall_streak=stall_streak,
        conflict_streak=conflict_streak,
        last_composite=last_composite,
        step_count=step_number,
    )
    meta = dict(metadata or {})
    return AgentContext(
        step_number=step_number,
        probe_state=state,
        working_memory=wm,
        screenshot=None,
        visual_struct=visual_struct,
        metadata=meta,
        errors=[],
    )


class MockRuleUpdateClient:
    """Fake cloud client that returns a deterministic rule-update request."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[list[dict[str, Any]]] = []
        self.plan_calls: int = 0
        self.update_calls: int = 0

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        self.calls.append(messages)
        system = (messages[0].get("content", "") if messages else "").lower()
        is_plan = "game strategy planner" in system
        is_update = "strategy optimizer" in system
        if is_plan:
            self.plan_calls += 1
            content = json.dumps({
                "plan": [
                    {"action_hint": "wait", "duration_ms": 500, "reason": "mock plan"},
                ],
                "reason": "mock offline replay",
            })
        elif is_update:
            self.update_calls += 1
            content = json.dumps({
                "update_type": "param",
                "target": "rules.mock_param",
                "reason": "Mock L2: increase mock_param for offline replay",
                "payload": {"mock_param": 1.0},
                "confidence": 0.95,
            })
        else:
            content = json.dumps({"update_type": "none", "confidence": 0.0})

        class _Message:
            content: str = ""

        msg = _Message()
        msg.content = content

        class _Choice:
            message = msg

        class _RespLocal:
            choices = [_Choice()]

        return _RespLocal()

    def chat_with_vision(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        raise NotImplementedError


class _Resp:
    class _Choice:
        class _Message:
            content: str = ""
        message = _Message()
    choices = [_Choice()]


def _make_api_client(provider: str | None = None, mock: bool = False) -> Any:
    """Build a cloud API client or a mock for offline hierarchical replay."""
    if mock:
        return MockRuleUpdateClient()
    from src.agent.api_client import MultiProviderClient

    return MultiProviderClient(provider=provider)


class MockActionClient:
    """Deterministic API client that returns a JSON action for ``LLMAgent``.

    The mock always proposes ``move`` toward the first active guide/target
    candidate (screen-space direction) or ``wait`` when no candidate exists.
    This keeps multi-bus replay reproducible and cheap.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        self.calls.append(messages)
        # Pull the most recent state from the conversation.
        state: dict[str, Any] = {}
        for msg in reversed(messages):
            content = msg.get("content", "")
            if isinstance(content, str) and "Current State" in content:
                try:
                    start = content.index("{")
                    state = json.loads(content[start:])
                except Exception:
                    state = {}
                break

        action = self._deterministic_action(state)
        content = json.dumps(action)

        class _Message:
            content: str = ""

        msg = _Message()
        msg.content = content

        class _Choice:
            message = msg

        class _RespLocal:
            choices = [_Choice()]

        return _RespLocal()

    def chat_with_vision(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        class _Message:
            content: str = ""

        msg = _Message()
        msg.content = json.dumps({"has_arrow": False, "arrow_direction": "none"})

        class _Choice:
            message = msg

        class _RespLocal:
            choices = [_Choice()]

        return _RespLocal()

    @staticmethod
    def _deterministic_action(state: dict[str, Any]) -> dict[str, Any]:
        player = state.get("player") or {}
        player_screen = player.get("screenPosition") or {}
        candidates = state.get("guide_or_target_candidates") or []
        for cand in candidates:
            if not cand.get("active", True):
                continue
            screen = cand.get("screenPosition") or {}
            if not screen or not player_screen:
                continue
            dx = float(screen.get("x", 0)) - float(player_screen.get("x", 0))
            dy = float(screen.get("y", 0)) - float(player_screen.get("y", 0))
            norm = math.hypot(dx, dy)
            if norm == 0:
                continue
            return {
                "action": "move",
                "params": {"dx": round(dx / norm, 3), "dy": round(dy / norm, 3), "duration_ms": 320},
                "reason": "mock:move_toward_first_candidate",
            }
        return {"action": "wait", "params": {"duration_ms": 500}, "reason": "mock:no_candidate"}


def _make_llm_agent(provider: str | None, mock: bool) -> LLMAgent:
    """Build an LLM agent for the multi-bus decision analyst."""
    if mock:
        return LLMAgent(api_client=MockActionClient())
    from src.agent.api_client import MultiProviderClient

    return LLMAgent(api_client=MultiProviderClient(provider=provider))


def _make_memory_stores(game_id: str) -> dict[str, Any]:
    """Create lightweight file-backed memory stores for ``multi-bus-memory``.

    Semantic memory is intentionally omitted because it requires ``sqlite-vec``
    and a sentence-transformer download; the other three stores exercise the
    memory-curator code path without external dependencies.
    """
    tmp_root = Path(tempfile.gettempdir()) / "smallgameagent_offline_replay"
    tmp_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_root / f"{game_id}_episodic.db"
    return {
        "episodic_memory": EpisodicMemory(db_path=db_path),
        "procedural_memory": ProceduralMemory(
            json_path=tmp_root / f"{game_id}_procedural.json"
        ),
        "strategy_memory": StrategyMemory(
            store_path=tmp_root / f"{game_id}_strategy.json"
        ),
        "semantic_memory": None,
    }


async def _async_decide(maker: Any, ctx: AgentContext) -> dict[str, Any]:
    """Await an async decision maker and normalise the result."""
    action = await maker.decide(ctx)
    return normalize_action(action)


def _api_action_direct(state: dict[str, Any], client: Any) -> dict[str, Any]:
    """Ask the cloud API for a direct JSON action (api-rule mode)."""
    system = (
        "You are an AI agent playing a mobile game. Given the game state JSON, "
        "output a single JSON action to progress toward winning.\n"
        "Actions: move {dx, dy, duration_ms}, tap {x, y, duration_ms}, wait {duration_ms}.\n"
        "No markdown fences, no commentary."
    )
    state_snippet = json.dumps(
        {k: state.get(k) for k in ("player", "keyNumbers", "keyFlags", "guide_or_target_candidates") if k in state},
        default=str, ensure_ascii=False,
    )[:2000]
    try:
        resp = client.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": f"State:\n{state_snippet}\n\nAction?"}],
            max_tokens=256,
            temperature=0.0,
        )
        text = resp.choices[0].message.content or ""
        data = json.loads(text.strip().strip("`").replace("```json", "").replace("```", ""))
        if isinstance(data, dict) and data.get("action") in ("move", "tap", "wait"):
            return dict(data)
    except Exception as exc:
        logger.warning("api-rule direct call failed: %s", exc)
    return {"action": "wait", "params": {"duration_ms": 500}, "reason": "api-rule-fallback"}


class OfflineReplayResult:
    """Aggregate result for one offline replay run."""

    def __init__(self, game_id: str, mode: str) -> None:
        self.game_id = game_id
        self.mode = mode
        self.step_records: list[StepRecord] = []
        self.rule_update_history: list[dict[str, Any]] = []
        self.rule_update_count: int = 0
        self.total_latency_s: float = 0.0
        self.error: str | None = None

    def compute_metrics(self) -> dict[str, Any]:
        n = len(self.step_records)
        if n == 0:
            return {
                "steps": 0,
                "action_matches": 0,
                "type_matches": 0,
                "move_cosine_similarity": 0.0,
                "activity_ratio": 0.0,
                "stall_ratio": 0.0,
                "mean_latency_ms": 0.0,
                "total_latency_s": round(self.total_latency_s, 3),
                "rule_update_count": self.rule_update_count,
                "rule_update_history": self.rule_update_history,
                "composite": 0.0,
                "rubric": {},
                "error": self.error or "no steps replayed",
            }

        type_matches = sum(1 for s in self.step_records if s.type_match)
        action_matches = sum(1 for s in self.step_records if s.action_match)
        move_steps = [s for s in self.step_records if s.true_action.get("action") == "move"]
        move_cosines = [s.move_cosine for s in move_steps]
        mean_move_cos = sum(move_cosines) / len(move_cosines) if move_cosines else 0.0

        # Build a minimal step_log for score_trajectory.
        step_log = []
        for s in self.step_records:
            player = (s.state.get("player") or {}).get("worldPosition") or {}
            step_log.append({
                "player": {"x": player.get("x", 0), "z": player.get("z", 0)},
                "action": s.pred_action.get("action", "wait"),
                "reason": s.pred_action.get("reason", ""),
                "keyNumbers": s.state.get("keyNumbers") or {},
            })
        score = score_trajectory(step_log, result={"completed": False, "win": False, "steps": n})

        return {
            "steps": n,
            "action_matches": action_matches,
            "type_matches": type_matches,
            "move_cosine_similarity": round(mean_move_cos, 4),
            "activity_ratio": round(score.activity, 4),
            "stall_ratio": round(1.0 - score.activity, 4),
            "mean_latency_ms": round((self.total_latency_s * 1000) / n, 2),
            "total_latency_s": round(self.total_latency_s, 3),
            "rule_update_count": self.rule_update_count,
            "rule_update_history": self.rule_update_history,
            "composite": round(score.composite, 4),
            "rubric": score.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "mode": self.mode,
            "error": self.error,
            "metrics": self.compute_metrics(),
            "step_records": [
                {
                    "step": s.step,
                    "true_action": s.true_action,
                    "pred_action": s.pred_action,
                    "type_match": s.type_match,
                    "action_match": s.action_match,
                    "move_cosine": round(s.move_cosine, 4),
                    "latency_ms": round(s.latency_ms, 2),
                    "rule_update_triggered": s.rule_update_triggered,
                    "rule_update_applied": s.rule_update_applied,
                }
                for s in self.step_records
            ],
        }


def run_offline_replay(
    game_id: str,
    mode: str = "rule",
    max_steps: int | None = None,
    provider: str | None = None,
    l1_interval: int = 0,
    l2_interval: int = 15,
    stuck_threshold: int = 3,
    collect_dataset: bool = False,
    dataset_dir: Path | None = None,
    mock: bool = False,
    max_rounds: int = 2,
) -> OfflineReplayResult:
    """Replay one processed run offline and score the decider."""
    result = OfflineReplayResult(game_id, mode)
    game_dir = PROCESSED_ROOT / game_id
    if not game_dir.exists():
        result.error = f"processed run not found: {game_dir}"
        return result

    try:
        steps = load_steps(game_dir)
    except Exception as exc:
        result.error = f"failed to load steps: {exc}"
        return result

    if max_steps is not None:
        steps = steps[:max_steps]

    rule_params = RuleParameters()
    rule_engine = RuleEngine(game_id, rule_params=rule_params)
    planner: HierarchicalPlanner | None = None
    api_client: Any = None
    multi_bus_maker: Any = None
    memory_stores: dict[str, Any] | None = None

    if mode == "hierarchical":
        api_client = _make_api_client(provider=provider, mock=mock)
        planner = HierarchicalPlanner(
            rule_engine=rule_engine,
            api_client=api_client,
            l1_interval=l1_interval,
            l2_interval=l2_interval,
            stuck_threshold=stuck_threshold,
            rule_params=rule_params,
        )
    elif mode in ("multi-bus", "multi-bus-memory"):
        llm_agent = _make_llm_agent(provider=provider, mock=mock)
        maker_kwargs: dict[str, Any] = {
            "llm_agent": llm_agent,
            "rule_engine": rule_engine,
            "api_client": api_client,
            "visual_analyzer": None,
            "max_rounds": max_rounds,
        }
        if mode == "multi-bus-memory":
            memory_stores = _make_memory_stores(game_id)
            maker_kwargs.update(memory_stores)
        multi_bus_maker = DecisionRegistry.create(mode, **maker_kwargs)
    elif mode == "api-rule":
        api_client = _make_api_client(provider=provider, mock=mock)

    dataset_path: Path | None = None
    dataset_file: Any = None
    if collect_dataset:
        out_dir = dataset_dir or (ROOT / "collected_datasets" / f"offline_replay_{game_id}")
        out_dir.mkdir(parents=True, exist_ok=True)
        dataset_path = out_dir / "samples.jsonl"
        dataset_file = open(dataset_path, "w", encoding="utf-8")

    prev_after_state: dict[str, Any] | None = None
    last_composite = 0.0
    stall_streak = 0
    conflict_streak = 0

    try:
        for step_record in steps:
            step_num = int(step_record.get("step", 0))

            # Prefer before state; fall back to previous after state.
            before_raw = load_state(game_dir, step_record.get("before", {}).get("state"))
            if before_raw is None and prev_after_state is not None:
                before_raw = prev_after_state
            if before_raw is None:
                logger.warning("Step %d: no state available, skipping", step_num)
                continue

            state = adapt_processed_state(before_raw)
            action_record_path = step_record.get("action")
            action_record: dict[str, Any] | None = None
            if action_record_path:
                action_record = load_state(game_dir, action_record_path)
            true_action = adapt_ground_truth(action_record or {"type": "wait"})
            if true_action is None:
                true_action = {"action": "wait", "params": {"duration_ms": 500}, "reason": "unparseable-gt"}

            t0 = time.perf_counter()
            if mode == "rule":
                pred_action = rule_engine.step(state, visual=None)
            elif mode == "hierarchical" and planner is not None:
                ctx = build_fake_context(
                    step_number=step_num,
                    state=state,
                    last_composite=last_composite,
                    stall_streak=stall_streak,
                    conflict_streak=conflict_streak,
                    visual_struct=None,
                    metadata={"game_id": game_id},
                )
                pred_action = planner.step(ctx)
            elif mode in ("multi-bus", "multi-bus-memory") and multi_bus_maker is not None:
                ctx = build_fake_context(
                    step_number=step_num,
                    state=state,
                    last_composite=last_composite,
                    stall_streak=stall_streak,
                    conflict_streak=conflict_streak,
                    visual_struct=None,
                    metadata={"game_id": game_id},
                )
                pred_action = asyncio.run(_async_decide(multi_bus_maker, ctx))
            elif mode == "api-rule":
                if api_client is not None:
                    pred_action = _api_action_direct(state, api_client)
                else:
                    pred_action = rule_engine.step(state, visual=None)
            else:
                pred_action = rule_engine.step(state, visual=None)
            latency_ms = (time.perf_counter() - t0) * 1000

            pred_action = normalize_action(pred_action)
            type_match, action_match, cos = actions_match(pred_action, true_action)

            # Update working-memory signals for the next hierarchical step.
            if action_match:
                stall_streak = 0
                conflict_streak = 0
            else:
                if pred_action.get("action") == "wait" and true_action.get("action") != "wait":
                    stall_streak += 1
                if pred_action.get("action") != true_action.get("action"):
                    conflict_streak += 1

            composite = 0.0
            if action_match:
                composite = 1.0
            elif type_match and true_action.get("action") == "move":
                composite = 0.6 + 0.4 * max(0.0, cos)
            elif type_match:
                composite = 0.6
            last_composite = composite

            rule_update_triggered = False
            rule_update_applied = False
            if mode == "hierarchical" and planner is not None:
                hist = planner.stats().get("rule_update_history", [])
                if len(hist) > result.rule_update_count:
                    result.rule_update_count = len(hist)
                    result.rule_update_history = hist
                    rule_update_applied = True
                # Detect whether the planner would have triggered an update by
                # checking that L2 update calls happened this step.
                if getattr(api_client, "update_calls", 0) > sum(
                    1 for _ in result.rule_update_history
                ):
                    rule_update_triggered = True

            result.step_records.append(StepRecord(
                step=step_num,
                state=state,
                true_action=true_action,
                pred_action=pred_action,
                latency_ms=latency_ms,
                type_match=type_match,
                action_match=action_match,
                move_cosine=cos,
                rule_update_triggered=rule_update_triggered,
                rule_update_applied=rule_update_applied,
            ))
            result.total_latency_s += latency_ms / 1000.0

            if collect_dataset and dataset_file is not None:
                screenshot_rel = step_record.get("before", {}).get("screenshot") or step_record.get("after", {}).get("screenshot")
                image_path = str(game_dir / screenshot_rel) if screenshot_rel else ""
                sample = {
                    "image": image_path,
                    "state": json.dumps(state, ensure_ascii=False, default=str),
                    "action": json.dumps(true_action, ensure_ascii=False, default=str),
                }
                dataset_file.write(json.dumps(sample, ensure_ascii=False, default=str) + "\n")

            # Cache after state for the next step's fallback.
            after_raw = load_state(game_dir, step_record.get("after", {}).get("state"))
            if after_raw is not None:
                prev_after_state = after_raw
    finally:
        if dataset_file is not None:
            dataset_file.close()
        if memory_stores is not None:
            for store in memory_stores.values():
                if store is not None and hasattr(store, "close"):
                    try:
                        store.close()
                    except Exception:
                        pass

    return result


def resolve_games(preferred: list[str]) -> list[str]:
    """Return existing processed-run game IDs, substituting missing ones."""
    available = sorted(
        p.name for p in PROCESSED_ROOT.iterdir()
        if p.is_dir() and (p / "steps.jsonl").exists()
    )
    resolved: list[str] = []
    seen: set[str] = set()
    for gid in preferred:
        if gid in available:
            if gid not in seen:
                resolved.append(gid)
                seen.add(gid)
            continue
        # Pick the alphabetically first available game that is not already used.
        substitute = next((g for g in available if g not in seen), None)
        if substitute:
            logger.warning("Game %s not found; substituting %s", gid, substitute)
            resolved.append(substitute)
            seen.add(substitute)
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline replay evaluation on processed-runs trajectories.")
    parser.add_argument("--game", action="append", help="Game ID(s) to replay. Repeatable.")
    parser.add_argument("--mode", choices=["rule", "hierarchical", "api-rule", "multi-bus", "multi-bus-memory"], default="rule",
                        help="Decider mode.")
    parser.add_argument("--provider", default=None, help="Cloud provider for hierarchical/api-rule/multi-bus (e.g. qwen).")
    parser.add_argument("--mock", action="store_true", help="Use mock cloud client for hierarchical/api-rule/multi-bus.")
    parser.add_argument("--max-steps", type=int, default=None, help="Limit steps per game.")
    parser.add_argument("--max-rounds", type=int, default=2, help="Max decision/verify rounds for multi-bus modes.")
    parser.add_argument("--l1-interval", type=int, default=0, help="L1 local VLM interval (0=disabled).")
    parser.add_argument("--l2-interval", type=int, default=15, help="L2 cloud API interval.")
    parser.add_argument("--stuck-threshold", type=int, default=3, help="Stuck streak threshold for L1/L2.")
    parser.add_argument("--collect-dataset", action="store_true", help="Write VLM training samples JSONL.")
    parser.add_argument("--dataset-dir", type=Path, default=None, help="Override dataset output directory.")
    parser.add_argument("--output", type=Path, default=None, help="Override aggregate JSON output path.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    games = args.game or resolve_games(DEFAULT_GAMES)
    results: list[dict[str, Any]] = []

    for game_id in games:
        logger.info("Replaying %s in %s mode", game_id, args.mode)
        try:
            res = run_offline_replay(
                game_id=game_id,
                mode=args.mode,
                max_steps=args.max_steps,
                provider=args.provider,
                l1_interval=args.l1_interval,
                l2_interval=args.l2_interval,
                stuck_threshold=args.stuck_threshold,
                collect_dataset=args.collect_dataset,
                dataset_dir=args.dataset_dir,
                mock=args.mock,
                max_rounds=args.max_rounds,
            )
        except Exception as exc:
            logger.exception("Replay failed for %s", game_id)
            res = OfflineReplayResult(game_id, args.mode)
            res.error = str(exc)[:300]

        metrics = res.compute_metrics()
        logger.info(
            "%s %s: steps=%d composite=%.3f type_match=%d/%d action_match=%d/%d latency=%.1fms",
            game_id, args.mode, metrics["steps"], metrics["composite"],
            metrics["type_matches"], metrics["steps"],
            metrics["action_matches"], metrics["steps"],
            metrics["mean_latency_ms"],
        )
        if args.mode in ("hierarchical", "multi-bus", "multi-bus-memory"):
            logger.info("  rule_update_count=%d", metrics["rule_update_count"])
        results.append(res.to_dict())

    output_path = args.output
    if output_path is None:
        first_game = games[0] if games else "unknown"
        output_path = ROOT / f"experiment_offline_replay_{first_game}_{args.mode}.json"
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved results to %s", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
