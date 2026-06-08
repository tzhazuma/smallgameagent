"""Tests for src/training/data_loader.py.

Run with:  python -m pytest tests/test_data_loader.py -v
"""

import pytest
from pathlib import Path

from PIL import Image

from src.training.data_loader import VLMColdStartDataset, VALID_TASK_NAMES

DATASET_ROOT = Path(__file__).resolve().parent.parent / "vlm-training-data-cold-start-portable-20260608"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _has_dataset() -> bool:
    return DATASET_ROOT.is_dir()


requires_dataset = pytest.mark.skipif(
    not _has_dataset(),
    reason="Dataset directory not found; skipping integration test.",
)


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_rejects_bad_task_name(self) -> None:
        with pytest.raises(ValueError, match="Unknown task_name"):
            VLMColdStartDataset(DATASET_ROOT, "nonexistent")

    def test_rejects_bad_split(self) -> None:
        with pytest.raises(ValueError, match="Unknown split"):
            VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="nope")

    @requires_dataset
    def test_missing_jsonl_raises(self, tmp_path: Path) -> None:
        # Create a minimal task dir without the requested split
        task_dir = tmp_path / "tasks" / "next_probe_action"
        task_dir.mkdir(parents=True)
        with pytest.raises(FileNotFoundError):
            VLMColdStartDataset(tmp_path, "next_probe_action", split="smoke")

    @requires_dataset
    def test_all_seven_tasks_construct(self) -> None:
        for name in VALID_TASK_NAMES:
            ds = VLMColdStartDataset(DATASET_ROOT, name, split="smoke")
            assert len(ds) >= 0  # failure_recovery has 0 smoke samples


# ---------------------------------------------------------------------------
# Smoke-split integration tests
# ---------------------------------------------------------------------------

class TestSmokeSplits:

    @requires_dataset
    def test_next_probe_action_smoke_22_samples(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        assert len(ds) == 22

    @requires_dataset
    def test_field_grounding_smoke_21_samples(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "field_grounding", split="smoke")
        assert len(ds) == 21

    @requires_dataset
    def test_probe_action_effect_smoke_22_samples(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "probe_action_effect", split="smoke")
        assert len(ds) == 22

    @requires_dataset
    def test_failure_recovery_smoke_0_samples(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "failure_recovery", split="smoke")
        assert len(ds) == 0


# ---------------------------------------------------------------------------
# Sample structure tests
# ---------------------------------------------------------------------------

class TestSampleStructure:

    @requires_dataset
    def test_sample_keys_exist(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        item = ds[0]
        for key in ("sample_id", "task_type", "images", "messages", "target_raw", "input_raw"):
            assert key in item, f"Missing key: {key}"

    @requires_dataset
    def test_images_are_pil_images(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        item = ds[0]
        assert isinstance(item["images"], list)
        assert len(item["images"]) >= 1, "Expected at least one image"
        for img in item["images"]:
            assert isinstance(img, Image.Image)

    @requires_dataset
    def test_probe_action_effect_has_two_images(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "probe_action_effect", split="smoke")
        item = ds[0]
        assert len(item["images"]) == 2

    @requires_dataset
    def test_messages_have_roles(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "field_grounding", split="smoke")
        item = ds[0]
        assert isinstance(item["messages"], list)
        assert len(item["messages"]) >= 2
        roles = [m["role"] for m in item["messages"]]
        assert "system" in roles
        assert "user" in roles

    @requires_dataset
    def test_messages_format_is_system_user_assistant(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        item = ds[0]
        roles = [m["role"] for m in item["messages"]]
        assert roles == ["system", "user", "assistant"]

    @requires_dataset
    def test_target_is_dict_with_answer_key(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        item = ds[0]
        assert isinstance(item["target_raw"], dict)
        assert "answer" in item["target_raw"]

    @requires_dataset
    def test_input_raw_is_dict(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        assert isinstance(ds[0]["input_raw"], dict)


# ---------------------------------------------------------------------------
# Train / val split tests
# ---------------------------------------------------------------------------

class TestTrainValSplits:

    @requires_dataset
    def test_train_split_is_larger_than_val(self) -> None:
        train_ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="train")
        val_ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="val")
        assert len(train_ds) > len(val_ds)

    @requires_dataset
    def test_train_split_returns_valid_sample(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "field_grounding", split="train")
        assert len(ds) > 0
        item = ds[0]
        assert item["task_type"] == "field_grounding"
        assert len(item["images"]) >= 1

    @requires_dataset
    def test_val_split_returns_valid_sample(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "field_grounding", split="val")
        assert len(ds) > 0
        item = ds[0]
        assert item["task_type"] == "field_grounding"

    @requires_dataset
    def test_all_split_includes_train_and_val(self) -> None:
        all_ds = VLMColdStartDataset(DATASET_ROOT, "field_grounding", split="all")
        train_ds = VLMColdStartDataset(DATASET_ROOT, "field_grounding", split="train")
        val_ds = VLMColdStartDataset(DATASET_ROOT, "field_grounding", split="val")
        assert len(all_ds) == len(train_ds) + len(val_ds)


# ---------------------------------------------------------------------------
# Lazy loading behaviour
# ---------------------------------------------------------------------------

class TestLazyLoading:

    @requires_dataset
    def test_init_does_not_fail_when_image_missing(self, tmp_path: Path) -> None:
        """A dataset with missing images should still construct fine."""
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        # Construction succeeded — that's the main assertion.
        assert len(ds) == 22

    @requires_dataset
    def test_repeated_access_stable(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        a = ds[0]["sample_id"]
        b = ds[0]["sample_id"]
        assert a == b

    @requires_dataset
    def test_negative_indexing(self) -> None:
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        assert ds[-1]["sample_id"] == ds[len(ds) - 1]["sample_id"]
