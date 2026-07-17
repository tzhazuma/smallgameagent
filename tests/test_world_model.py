"""Tests for src.agent.world_model — VersionedWorldModel.

Pure unit tests with no external dependencies.
"""

from __future__ import annotations

import pytest

from src.agent.world_model import (
    REASON_CAPABILITY_FLIP,
    REASON_SCENE_SHIFT,
    ChangeReport,
    EntityRecord,
    PlanArtifact,
    VersionedWorldModel,
)


@pytest.fixture
def model() -> VersionedWorldModel:
    return VersionedWorldModel()


# ---------------------------------------------------------------- capability flip


def test_capability_flip_stales_interaction_plan(model: VersionedWorldModel) -> None:
    report0 = model.write_observation(
        {"keyFlags": {"autoFishing": True}, "keyNumbers": {"gold": 100}}, step=0
    )
    assert "autoFishing" in report0.new_entities
    assert report0.bumped_epochs == {}  # first sighting is not a flip

    model.register_plan("interact_fish", "interaction", ["autoFishing"], step=0)
    model.register_plan("route_gold", "route", ["gold"], step=0)

    # First flip: true -> false.
    report1 = model.write_observation({"keyFlags": {"autoFishing": False}}, step=1)
    assert model.capability_epoch == 1
    assert report1.bumped_epochs == {"capability": 1}
    assert "interact_fish" in report1.stale_plans
    assert model.is_stale("interact_fish")
    assert model.get_plan("interact_fish").stale_reason == REASON_CAPABILITY_FLIP
    assert not model.is_stale("route_gold")

    # Second flip: false -> true.
    model.write_observation({"keyFlags": {"autoFishing": True}}, step=2)
    assert model.capability_epoch == 2
    assert not model.is_stale("route_gold")
    assert model.get_plan("route_gold").stale_reason is None


def test_number_plan_stales_via_entity_changed(model: VersionedWorldModel) -> None:
    model.write_observation({"keyNumbers": {"gold": 100}}, step=0)
    model.register_plan("spend_gold", "route", ["gold"], step=0)

    report = model.write_observation({"keyNumbers": {"gold": 120}}, step=1)
    assert report.bumped_epochs == {}
    assert model.is_stale("spend_gold")
    assert model.get_plan("spend_gold").stale_reason == "entity_changed"


# ---------------------------------------------------------------- scene shift


def test_scene_shift_stales_path_but_not_interaction(model: VersionedWorldModel) -> None:
    report0 = model.write_observation(
        {"guide_or_target_candidates": ["UnlockItem_3", "Arr3D"]}, step=0
    )
    # First topology frame only initializes the baseline.
    assert report0.bumped_epochs == {}
    assert model.scene_epoch == 0

    model.register_plan("path_to_arr", "path", ["Arr3D"], step=0)
    model.register_plan("interact_arr", "interaction", ["Arr3D"], step=0)

    # A new obstacle node appears.
    report1 = model.write_observation(
        {"guide_or_target_candidates": ["UnlockItem_3", "Arr3D", "Obstacle_1"]}, step=1
    )
    assert report1.bumped_epochs == {"scene": 1}
    assert model.scene_epoch == 1
    assert "Obstacle_1" in report1.new_entities

    assert model.is_stale("path_to_arr")
    assert model.get_plan("path_to_arr").stale_reason == REASON_SCENE_SHIFT
    # interaction plan has no capability dependency -> unaffected by scene shift.
    assert not model.is_stale("interact_arr")


def test_vanished_node_bumps_scene_epoch(model: VersionedWorldModel) -> None:
    model.write_observation({"guide_or_target_candidates": ["A", "B"]}, step=0)
    model.register_plan("route_ab", "route", ["A", "B"], step=0)

    model.write_observation({"guide_or_target_candidates": ["A"]}, step=1)
    assert model.scene_epoch == 1
    assert model.is_stale("route_ab")
    assert model.get_plan("route_ab").stale_reason == REASON_SCENE_SHIFT


# ---------------------------------------------------------------- idempotency


def test_check_staleness_idempotent(model: VersionedWorldModel) -> None:
    model.write_observation({"keyFlags": {"autoFishing": True}}, step=0)
    model.register_plan("interact_fish", "interaction", ["autoFishing"], step=0)

    model.write_observation({"keyFlags": {"autoFishing": False}}, step=1)
    assert model.stats()["stale_events"] == 1

    assert model.check_staleness() == []
    assert model.check_staleness() == []
    assert model.stats()["stale_events"] == 1
    assert model.get_plan("interact_fish").stale_step == 1


# ---------------------------------------------------------------- numbers / tolerance


def test_number_changes_never_bump_epochs(model: VersionedWorldModel) -> None:
    model.write_observation({"keyNumbers": {"gold": 100, "storageY": 2.52}}, step=0)
    report = model.write_observation({"keyNumbers": {"gold": 120, "storageY": 2.52}}, step=1)

    assert report.bumped_epochs == {}
    assert model.scene_epoch == 0
    assert model.capability_epoch == 0
    assert report.changed_entities == ["gold"]
    assert model.stats()["entity_count"] == 2


def test_missing_and_malformed_keys_tolerated(model: VersionedWorldModel) -> None:
    report = model.write_observation({}, step=0)
    assert report.changed_entities == []
    assert report.new_entities == []
    assert report.stale_plans == []

    report = model.write_observation({"done": True, "win": False, "player": {"x": 1}}, step=1)
    assert report.bumped_epochs == {}

    report = model.write_observation({"keyFlags": None, "keyNumbers": "oops"}, step=2)
    assert report.bumped_epochs == {}

    report = model.write_observation(None, step=3)
    assert isinstance(report, ChangeReport)


# ---------------------------------------------------------------- report / stats / roundtrip


def test_stats_and_change_report(model: VersionedWorldModel) -> None:
    model.write_observation(
        {
            "keyFlags": {"autoFishing": True},
            "keyNumbers": {"gold": 100},
            "guide_or_target_candidates": ["Arr3D"],
        },
        step=0,
    )
    model.register_plan("interact_fish", "interaction", ["autoFishing"], step=0)
    report = model.write_observation({"keyFlags": {"autoFishing": False}}, step=1)

    report_d = report.to_dict()
    assert report_d["step"] == 1
    assert report_d["changed_entities"] == ["autoFishing"]
    assert report_d["bumped_epochs"] == {"capability": 1}
    assert report_d["stale_plans"] == ["interact_fish"]

    stats = model.stats()
    assert stats == {
        "entity_count": 3,
        "plan_count": 1,
        "stale_plan_count": 1,
        "scene_epoch": 0,
        "capability_epoch": 1,
        "capability_flips": 1,
        "stale_events": 1,
    }


def test_serialization_roundtrip(model: VersionedWorldModel) -> None:
    model.write_observation(
        {
            "keyFlags": {"autoFishing": True, "hasNet": False},
            "keyNumbers": {"gold": 100},
            "guide_or_target_candidates": ["UnlockItem_3", "Arr3D"],
        },
        step=0,
    )
    model.register_plan("interact_fish", "interaction", ["autoFishing"], step=0)
    model.register_plan("path_to_arr", "path", ["Arr3D"], step=0)
    model.write_observation({"keyFlags": {"autoFishing": False}}, step=1)
    model.write_observation({"guide_or_target_candidates": ["Arr3D", "Obstacle_1"]}, step=2)

    data = model.to_dict()
    restored = VersionedWorldModel.from_dict(data)

    assert restored.to_dict() == data
    assert restored.stats() == model.stats()
    assert restored.is_stale("interact_fish")
    assert restored.is_stale("path_to_arr")

    # Restored model keeps behaving: a new flip stales a freshly registered plan.
    restored.register_plan("interact_fish_v2", "interaction", ["autoFishing"], step=3)
    restored.write_observation({"keyFlags": {"autoFishing": True}}, step=4)
    assert restored.is_stale("interact_fish_v2")
    assert restored.get_plan("interact_fish_v2").stale_reason == REASON_CAPABILITY_FLIP


def test_entity_and_plan_record_roundtrip() -> None:
    ent = EntityRecord("autoFishing", "flag", True, 3, 0, 7)
    assert EntityRecord.from_dict(ent.to_dict()) == ent

    plan = PlanArtifact(
        plan_id="p1",
        kind="path",
        depends_on={"A", "B"},
        scene_epoch_at_creation=1,
        capability_epoch_at_creation=2,
        entity_versions_at_creation={"A": 1, "B": 4},
        created_step=5,
        stale=True,
        stale_reason=REASON_SCENE_SHIFT,
        stale_step=9,
    )
    assert PlanArtifact.from_dict(plan.to_dict()) == plan


# ---------------------------------------------------------------- real-world replay


def test_replay_autofishing_12_flips_in_266_steps(model: VersionedWorldModel) -> None:
    """Reproduce the real failure: autoFishing flips 12 times within 266 steps."""
    model.write_observation({"keyFlags": {"autoFishing": True}, "keyNumbers": {"gold": 0}}, step=0)
    model.register_plan("interact_fish", "interaction", ["autoFishing"], step=0)
    model.register_plan("route_gold", "route", ["gold"], step=0)

    flips = 0
    prev = True
    for step in range(1, 266):
        # Flip every 22 steps: flips at 22, 44, ..., 264 -> exactly 12 flips.
        value = (step // 22) % 2 == 0
        obs = {"keyFlags": {"autoFishing": value}, "keyNumbers": {"gold": step}}
        model.write_observation(obs, step)
        if value != prev:
            flips += 1
            prev = value
    assert flips == 12

    stats = model.stats()
    assert stats["capability_flips"] == 12
    assert model.capability_epoch == 12
    assert stats["entity_count"] == 2

    # The interaction plan staled at the first flip and is never double-counted.
    assert model.is_stale("interact_fish")
    assert model.get_plan("interact_fish").stale_reason == REASON_CAPABILITY_FLIP
    # The route plan depends only on gold, which changes every step -> entity_changed.
    assert model.is_stale("route_gold")
    assert model.get_plan("route_gold").stale_reason == "entity_changed"

    scope = model.local_replan_scope(["interact_fish"])
    assert scope == {"autoFishing"}
    assert "gold" not in scope


def test_local_replan_scope_ignores_unknown_plans(model: VersionedWorldModel) -> None:
    model.write_observation({"keyFlags": {"autoFishing": True}}, step=0)
    plan = model.register_plan("interact_fish", "interaction", ["autoFishing"], step=0)

    scope = model.local_replan_scope([plan, "nonexistent"])
    assert scope == {"autoFishing"}
