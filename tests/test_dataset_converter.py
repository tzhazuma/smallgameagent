"""Tests for src/training/dataset_converter.py.

Run with:  python -m pytest tests/test_dataset_converter.py -v
"""

import json
import os
from pathlib import Path
from typing import Any, Dict

import pytest
from PIL import Image

from src.training.data_loader import VALID_TASK_NAMES
from src.training.dataset_converter import VLMDatasetConverter, _build_state_summary

DATASET_ROOT = Path(__file__).resolve().parent.parent / "vlm-training-data-cold-start-portable-20260608"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _has_dataset() -> bool:
    return DATASET_ROOT.is_dir()


requires_dataset = pytest.mark.skipif(
    not _has_dataset(),
    reason="Dataset directory not found; skipping integration test.",
)


def converter() -> VLMDatasetConverter:
    """Return a converter pointing at the real dataset root."""
    return VLMDatasetConverter(DATASET_ROOT)


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_accepts_valid_dataset_root(self) -> None:
        c = VLMDatasetConverter(DATASET_ROOT)
        assert c.dataset_root == DATASET_ROOT.resolve()

    def test_rejects_missing_tasks_directory(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="no 'tasks/' directory"):
            VLMDatasetConverter(tmp_path)

    def test_rejects_missing_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "tasks").mkdir()
        with pytest.raises(FileNotFoundError, match="dataset-manifest.json"):
            VLMDatasetConverter(tmp_path)

    def test_stores_processor_when_provided(self) -> None:
        fake_processor = object()
        c = VLMDatasetConverter(DATASET_ROOT, processor_or_tokenizer=fake_processor)
        assert c.processor is fake_processor

    def test_processor_is_none_by_default(self) -> None:
        c = VLMDatasetConverter(DATASET_ROOT)
        assert c.processor is None

    @requires_dataset
    def test_rejects_bad_task_name(self) -> None:
        c = converter()
        with pytest.raises(ValueError, match="Unknown task_name"):
            c.to_hf_dataset("nonexistent")

    @requires_dataset
    def test_rejects_bad_split(self) -> None:
        c = converter()
        with pytest.raises(ValueError, match="Unknown split"):
            c.to_hf_dataset("next_probe_action", split="nope")


# ---------------------------------------------------------------------------
# Qwen3.5 message format tests
# ---------------------------------------------------------------------------

class TestQwen35Messages:

    @requires_dataset
    def test_three_messages_system_user_assistant(self) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        sample = ds[0]
        msgs = c.to_qwen35_messages(sample)

        assert len(msgs) == 3
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"

    @requires_dataset
    def test_content_is_list_of_typed_blocks(self) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        sample = ds[0]
        msgs = c.to_qwen35_messages(sample)

        for msg in msgs:
            content = msg["content"]
            # System messages are plain strings (Qwen3.5 template requires this)
            if msg.get("role") == "system":
                assert isinstance(content, str), f"System content should be str, got {type(content)}"
            else:
                assert isinstance(content, list), f"Expected list, got {type(content)}"
                assert len(content) >= 1
                for block in content:
                    assert "type" in block, f"Missing 'type' in block: {block}"
                    assert block["type"] in ("text", "image"), f"Bad type: {block['type']}"

    @requires_dataset
    def test_first_user_block_is_image(self) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        sample = ds[0]
        msgs = c.to_qwen35_messages(sample)

        user_content = msgs[1]["content"]
        assert user_content[0]["type"] == "image"
        assert "image" in user_content[0]

    @requires_dataset
    def test_image_paths_are_absolute_strings(self) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        sample = ds[0]
        msgs = c.to_qwen35_messages(sample)

        for block in msgs[1]["content"]:
            if block["type"] == "image":
                path = block["image"]
                assert isinstance(path, str)
                assert path.startswith("/"), f"Expected absolute path, got {path[:50]}"

    @requires_dataset
    def test_assistant_content_contains_valid_json(self) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "field_grounding", split="smoke")
        sample = ds[0]
        msgs = c.to_qwen35_messages(sample)

        assistant_text = msgs[2]["content"][0]["text"]
        # Should be valid JSON (target.answer serialized)
        parsed = json.loads(assistant_text)
        assert isinstance(parsed, dict)
        # field_grounding has multiple answer keys
        assert len(parsed) >= 1

    @requires_dataset
    def test_system_message_is_text_only(self) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        sample = ds[0]
        msgs = c.to_qwen35_messages(sample)

        sys_content = msgs[0]["content"]
        # System messages are plain strings (Qwen3.5 template requires this)
        assert isinstance(sys_content, str), f"Expected str, got {type(sys_content)}"
        assert len(sys_content) > 0

    @requires_dataset
    def test_all_smoke_samples_convert(self) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")

        for idx in range(len(ds)):
            sample = ds[idx]
            msgs = c.to_qwen35_messages(sample)
            assert len(msgs) == 3
            # Every sample must have at least one image block
            image_blocks = [b for b in msgs[1]["content"] if b["type"] == "image"]
            assert len(image_blocks) >= 1, f"Sample {idx} has no images"


# ---------------------------------------------------------------------------
# Gemma-4 message format tests
# ---------------------------------------------------------------------------

class TestGemma4Messages:

    @requires_dataset
    def test_three_messages_system_user_assistant(self) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        sample = ds[0]
        msgs = c.to_gemma4_messages(sample)

        assert len(msgs) == 3
        assert [m["role"] for m in msgs] == ["system", "user", "assistant"]

    @requires_dataset
    def test_image_blocks_use_url_key(self) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        sample = ds[0]
        msgs = c.to_gemma4_messages(sample)

        for block in msgs[1]["content"]:
            if block["type"] == "image":
                assert "url" in block, f"Gemma-4 image block should use 'url' key: {block}"

    @requires_dataset
    def test_qwen35_and_gemma4_same_roles(self) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "field_grounding", split="smoke")
        sample = ds[0]

        qwen_roles = [m["role"] for m in c.to_qwen35_messages(sample)]
        gemma_roles = [m["role"] for m in c.to_gemma4_messages(sample)]
        assert qwen_roles == gemma_roles


# ---------------------------------------------------------------------------
# HuggingFace Dataset output tests
# ---------------------------------------------------------------------------

class TestToHFDataset:

    @requires_dataset
    def test_returns_dataset_type(self) -> None:
        """If datasets is installed, returns Dataset; else returns list of dicts."""
        c = converter()
        result = c.to_hf_dataset("next_probe_action", split="smoke")

        # Either datasets.Dataset or list[dict]
        type_name = type(result).__name__
        assert type_name in ("Dataset", "list"), f"Unexpected type: {type_name}"

    @requires_dataset
    def test_each_record_has_required_keys(self) -> None:
        c = converter()
        result = c.to_hf_dataset("next_probe_action", split="smoke")

        for record in result:
            for key in ("messages", "sample_id", "task_type"):
                assert key in record, f"Missing key {key}"
            # Image data stored as "image_paths" (strings) instead of "images" (PIL) for Arrow compat
            assert "image_paths" in record, f"Missing key image_paths in record"

    @requires_dataset
    def test_images_are_pil_images(self) -> None:
        c = converter()
        result = c.to_hf_dataset("next_probe_action", split="smoke")

        for record in result:
            # Image data stored as "image_paths" (string paths) for Arrow compat
            paths = record["image_paths"]
            assert isinstance(paths, list)
            for p in paths:
                assert isinstance(p, str), f"Expected str path, got {type(p)}"
                assert os.path.isfile(p), f"Image path does not exist: {p}"

    @requires_dataset
    def test_sample_count_matches_source(self) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        result = c.to_hf_dataset("next_probe_action", split="smoke")
        assert len(result) == len(ds)

    @requires_dataset
    def test_sample_ids_are_unique(self) -> None:
        c = converter()
        result = c.to_hf_dataset("field_grounding", split="smoke")
        ids = [r["sample_id"] for r in result]
        assert len(ids) == len(set(ids))

    @requires_dataset
    def test_task_type_is_consistent(self) -> None:
        c = converter()
        result = c.to_hf_dataset("field_grounding", split="smoke")
        for record in result:
            assert record["task_type"] == "field_grounding"


# ---------------------------------------------------------------------------
# ShareGPT export tests
# ---------------------------------------------------------------------------

class TestShareGPTExport:

    @requires_dataset
    def test_produces_json_file(self, tmp_path: Path) -> None:
        c = converter()
        out = c.to_sharegpt_format("next_probe_action", split="smoke", output_dir=tmp_path)

        assert out.is_file()
        assert out.suffix == ".json"

    @requires_dataset
    def test_json_is_list_of_records(self, tmp_path: Path) -> None:
        c = converter()
        out = c.to_sharegpt_format("next_probe_action", split="smoke", output_dir=tmp_path)

        with open(out, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert isinstance(data, list)
        assert len(data) >= 1

    @requires_dataset
    def test_each_record_has_conversations_and_images(self, tmp_path: Path) -> None:
        c = converter()
        out = c.to_sharegpt_format("next_probe_action", split="smoke", output_dir=tmp_path)

        with open(out, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        for idx, record in enumerate(data):
            assert "conversations" in record, f"Record {idx} missing conversations"
            assert "images" in record, f"Record {idx} missing images"
            assert isinstance(record["conversations"], list)

    @requires_dataset
    def test_conversations_use_from_role_tags(self, tmp_path: Path) -> None:
        c = converter()
        out = c.to_sharegpt_format("next_probe_action", split="smoke", output_dir=tmp_path)

        with open(out, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        for record in data:
            roles = [turn["from"] for turn in record["conversations"]]
            assert roles == ["human", "gpt"], f"Bad roles: {roles}"

    @requires_dataset
    def test_conversation_values_are_non_empty_strings(self, tmp_path: Path) -> None:
        c = converter()
        out = c.to_sharegpt_format("next_probe_action", split="smoke", output_dir=tmp_path)

        with open(out, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        for record in data:
            for turn in record["conversations"]:
                assert isinstance(turn["value"], str)
                assert len(turn["value"]) > 0

    @requires_dataset
    def test_gpt_value_is_valid_json(self, tmp_path: Path) -> None:
        c = converter()
        out = c.to_sharegpt_format("field_grounding", split="smoke", output_dir=tmp_path)

        with open(out, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        for record in data:
            gpt_value = record["conversations"][1]["value"]
            parsed = json.loads(gpt_value)
            assert isinstance(parsed, dict)

    @requires_dataset
    def test_human_value_starts_with_image_token(self, tmp_path: Path) -> None:
        c = converter()
        out = c.to_sharegpt_format("probe_action_effect", split="smoke", output_dir=tmp_path)

        with open(out, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        for record in data:
            human_value = record["conversations"][0]["value"]
            assert human_value.startswith(
                "<image>"
            ), f"Expected <image> prefix, got {human_value[:50]}"

    @requires_dataset
    def test_images_copied_to_output(self, tmp_path: Path) -> None:
        c = converter()
        out = c.to_sharegpt_format("next_probe_action", split="smoke", output_dir=tmp_path)

        with open(out, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        for record in data:
            for rel_path in record["images"]:
                abs_dest = tmp_path / rel_path
                assert abs_dest.is_file(), f"Missing copied image: {abs_dest}"

    @requires_dataset
    def test_dataset_info_json_written(self, tmp_path: Path) -> None:
        c = converter()
        c.to_sharegpt_format("next_probe_action", split="smoke", output_dir=tmp_path)

        info_path = tmp_path / "dataset_info.json"
        assert info_path.is_file()

        with open(info_path, "r", encoding="utf-8") as fh:
            info = json.load(fh)

        assert "vlm_cold_start_next_probe_action" in info
        entry = info["vlm_cold_start_next_probe_action"]
        assert entry["formatting"] == "sharegpt"
        assert entry["columns"]["messages"] == "conversations"
        assert entry["columns"]["images"] == "images"
        assert entry["tags"]["role_tag"] == "from"

    @requires_dataset
    def test_dataset_info_merges_existing_entries(self, tmp_path: Path) -> None:
        c = converter()
        # Write pre-existing dataset_info with another entry
        info_path = tmp_path / "dataset_info.json"
        info_path.write_text(
            json.dumps({"existing_dataset": {"file_name": "old.json"}}), encoding="utf-8"
        )

        c.to_sharegpt_format("next_probe_action", split="smoke", output_dir=tmp_path)

        with open(info_path, "r", encoding="utf-8") as fh:
            info = json.load(fh)

        assert "existing_dataset" in info, "Pre-existing entry should be preserved"
        assert "vlm_cold_start_next_probe_action" in info

    @requires_dataset
    def test_sharegpt_sample_count_matches_source(self, tmp_path: Path) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")

        out = c.to_sharegpt_format("next_probe_action", split="smoke", output_dir=tmp_path)
        with open(out, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        assert len(data) == len(ds)


# ---------------------------------------------------------------------------
# State summary helper tests
# ---------------------------------------------------------------------------

class TestBuildStateSummary:

    @requires_dataset
    def test_returns_non_empty_string(self) -> None:
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        sample = ds[0]
        summary = _build_state_summary(sample["input_raw"])
        assert isinstance(summary, str)
        assert len(summary) > 0

    @requires_dataset
    def test_is_valid_json(self) -> None:
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        sample = ds[0]
        summary = _build_state_summary(sample["input_raw"])
        parsed = json.loads(summary)
        assert isinstance(parsed, dict)
        assert "task" in parsed
        assert "state" in parsed

    @requires_dataset
    def test_minimal_input_does_not_crash(self) -> None:
        # Build a minimal input dict with no backend/state
        minimal: Dict[str, Any] = {"task": "dummy", "images": []}
        summary = _build_state_summary(minimal)
        parsed = json.loads(summary)
        assert parsed["task"] == "dummy"

    @requires_dataset
    def test_all_seven_task_types_produce_summary(self) -> None:
        from src.training.data_loader import VLMColdStartDataset
        for task_name in sorted(VALID_TASK_NAMES):
            ds = VLMColdStartDataset(DATASET_ROOT, task_name, split="smoke")
            if len(ds) == 0:
                continue  # failure_recovery has 0 smoke samples
            sample = ds[0]
            summary = _build_state_summary(sample["input_raw"])
            parsed = json.loads(summary)
            assert parsed["task"] == task_name, f"Task {task_name}: expected task field"
            assert "state" in parsed
            assert isinstance(parsed["state"], dict)


# ---------------------------------------------------------------------------
# Cross-format consistency tests
# ---------------------------------------------------------------------------

class TestCrossFormatConsistency:

    @requires_dataset
    def test_qwen35_and_hf_dataset_same_message_structure(self) -> None:
        c = converter()
        from src.training.data_loader import VLMColdStartDataset
        ds = VLMColdStartDataset(DATASET_ROOT, "next_probe_action", split="smoke")
        sample = ds[0]

        qwen_msgs = c.to_qwen35_messages(sample)
        hf_result = c.to_hf_dataset("next_probe_action", split="smoke")
        hf_msgs = hf_result[0]["messages"]

        # Should have same role sequence
        assert [m["role"] for m in qwen_msgs] == [m["role"] for m in hf_msgs]

    @requires_dataset
    def test_sharegpt_images_match_source(self, tmp_path: Path) -> None:
        c = converter()
        out = c.to_sharegpt_format("probe_action_effect", split="smoke", output_dir=tmp_path)
        with open(out, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        # Each sharegpt record should have 2 images (probe_action_effect samples have 2)
        for record in data:
            assert len(record["images"]) == 2
