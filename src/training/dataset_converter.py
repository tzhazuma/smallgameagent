#!/usr/bin/env python3
"""
Dataset converter for VLM cold-start training data.

Converts JSONL task samples produced by ``VLMColdStartDataset`` to
HuggingFace-compatible multimodal SFT formats, supporting Qwen3.5-VL and
Gemma-4 chat templates, plus ShareGPT JSON export for LLaMA-Factory.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.training.data_loader import VLMColdStartDataset, VALID_TASK_NAMES, VALID_SPLITS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state_summary(input_raw: Dict[str, Any]) -> str:
    """Build a concise JSON text summary of the game state for VLM context.

    Extracts the most relevant fields from ``state_summary_before``,
    exploration plan, unknowns, and candidate actions — enough for the VLM
    to reason about probe selection without including the full raw input.
    """
    backend: Dict[str, Any] = input_raw.get("backend", {})
    state: Dict[str, Any] = backend.get("state_summary_before", {})
    unknowns: List[str] = backend.get("unknowns_before", [])
    known_facts: Dict[str, Any] = backend.get("known_facts_before", {})
    plan: Dict[str, Any] = input_raw.get("exploration_plan", {})
    candidates: List[Dict[str, Any]] = input_raw.get("candidate_actions", [])

    # Distill candidate actions to type + label
    slim_candidates: List[Dict[str, Any]] = []
    for ca in candidates:
        action: Dict[str, Any] = ca.get("action", {})
        slim_candidates.append(
            {
                "label": ca.get("label"),
                "type": action.get("type"),
                "reason": ca.get("reason"),
            }
        )

    slim_state: Dict[str, Any] = {}
    if state:
        player: Dict[str, Any] = state.get("player", {})
        slim_state["ready"] = state.get("ready")
        slim_state["done"] = state.get("done")
        slim_state["win"] = state.get("win")
        if player:
            slim_state["player_screen"] = player.get("screenPosition")
            slim_state["player_active"] = player.get("active")
        slim_state["num_guide_candidates"] = len(state.get("guide_or_target_candidates", []))
        slim_state["num_resource_candidates"] = len(state.get("resource_candidates", []))
        slim_state["num_obstacle_candidates"] = len(state.get("obstacle_candidates", []))

    summary: Dict[str, Any] = {
        "task": input_raw.get("task", ""),
        "exploration_plan": {"name": plan.get("name"), "action_count": plan.get("action_count")}
        if plan
        else None,
        "state": slim_state,
        "unknowns": unknowns[:12],  # cap to keep summary compact
        "candidate_actions": slim_candidates[:8],
    }

    # Include which known-fact fields are confirmed
    if known_facts:
        confirmed_fields = [k for k, v in known_facts.items() if v.get("confirmed")]
        if confirmed_fields:
            summary["confirmed_fields"] = confirmed_fields

    return json.dumps(summary, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main converter class
# ---------------------------------------------------------------------------


class VLMDatasetConverter:
    """Convert VLM cold-start data to chat-model training formats.

    Constructor parameters
    ----------------------
    dataset_root : str or Path
        Root of the portable dataset (the directory containing
        ``tasks/`` and ``dataset-manifest.json``).
    processor_or_tokenizer : optional
        A HuggingFace processor or tokenizer whose ``apply_chat_template``
        method will be used when available (e.g. Qwen3VLProcessor or
        Gemma3ForConditionalGeneration processor).  Kept ``None`` for
        purely offline format conversion (no model weights needed).

    Chosen format names
    --------------------
    ``"qwen35"`` — Qwen3.5-VL chat template (content blocks with
      ``"type": "image"`` / ``"type": "text"``).
    ``"gemma4"`` — Gemma-4 chat format (identical block structure; the
      processor-specifc tokenisation differentiates at ``apply_chat_template``
      time).
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        dataset_root: Union[str, Path],
        processor_or_tokenizer: Any = None,
    ) -> None:
        self.dataset_root = Path(dataset_root).resolve()
        self.processor = processor_or_tokenizer

        # Quick sanity check
        if not (self.dataset_root / "tasks").is_dir():
            raise FileNotFoundError(
                f"Dataset root has no 'tasks/' directory: {self.dataset_root}"
            )
        if not (self.dataset_root / "dataset-manifest.json").is_file():
            raise FileNotFoundError(
                f"Missing dataset-manifest.json in: {self.dataset_root}"
            )

    # ------------------------------------------------------------------
    # Image path resolution
    # ------------------------------------------------------------------

    def _task_dir(self, task_name: str) -> Path:
        return self.dataset_root / "tasks" / task_name

    def _resolve_image_path(self, task_name: str, relative_path: str) -> str:
        """Resolve a JSONL-relative asset path to an absolute filesystem path."""
        resolved = (self._task_dir(task_name) / relative_path).resolve()
        return str(resolved)

    def _get_image_paths(self, sample: Dict[str, Any]) -> List[str]:
        """Return absolute paths for every image referenced in *sample*."""
        task_name: str = sample["task_type"]
        image_entries: List[Dict[str, Any]] = sample.get("input_raw", {}).get("images", [])
        paths: List[str] = []
        for entry in image_entries:
            rel: Optional[str] = entry.get("path")
            if rel:
                paths.append(self._resolve_image_path(task_name, rel))
        return paths

    def _get_image_filenames(self, sample: Dict[str, Any]) -> List[str]:
        """Return bare filenames (no directory) for every image in *sample*."""
        image_entries: List[Dict[str, Any]] = sample.get("input_raw", {}).get("images", [])
        names: List[str] = []
        for entry in image_entries:
            rel: Optional[str] = entry.get("path")
            if rel:
                names.append(Path(rel).name)
        return names

    # ------------------------------------------------------------------
    # Message builders
    # ------------------------------------------------------------------

    # — shared internal helper —

    def _base_messages(
        self,
        sample: Dict[str, Any],
        image_key: str,
    ) -> List[Dict[str, Any]]:
        """Build multimodal messages from *sample* using *image_key* for
        image content blocks (``"image"`` for Qwen3.5; ``"url"`` for Gemma-4
        in certain usage)."""
        raw_msgs: List[Dict[str, str]] = sample["messages"]

        system_text = ""
        assistant_text = ""

        for m in raw_msgs:
            if m["role"] == "system":
                system_text = m["content"]
            elif m["role"] == "assistant":
                # The original assistant content is the target JSON string.
                assistant_text = m["content"]

        # Use structured target.answer if available, else keep raw text
        target = sample.get("target_raw", {})
        if "answer" in target:
            assistant_text = json.dumps(target["answer"], ensure_ascii=False)

        image_paths = self._get_image_paths(sample)
        state_summary = _build_state_summary(sample.get("input_raw", {}))

        # Build user content blocks: images first, then text
        user_content: List[Dict[str, Any]] = []
        for img_path in image_paths:
            user_content.append({"type": "image", image_key: img_path})
        user_content.append({"type": "text", "text": state_summary})

        messages: List[Dict[str, Any]] = []

        if system_text:
            # Qwen3.5 template rejects list-format content for system role;
            # use plain string instead of [{"type": "text", "text": ...}].
            messages.append({"role": "system", "content": system_text})

        messages.append({"role": "user", "content": user_content})
        messages.append(
            {"role": "assistant", "content": [{"type": "text", "text": assistant_text}]}
        )

        return messages

    # — public per-model converters —

    def to_qwen35_messages(self, sample: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Convert a single dataset sample to Qwen3.5-VL chat format.

        Returns
        -------
        messages : list[dict]
            Chat messages where ``content`` is a list of content blocks::

                [
                  {"role": "system", "content": [{"type": "text", "text": "..."}]},
                  {"role": "user", "content": [
                      {"type": "image",  "image": "/abs/path/to/img.png"},
                      {"type": "text",   "text":  "{...state summary...}"},
                  ]},
                  {"role": "assistant", "content": [
                      {"type": "text", "text": "{...target answer JSON...}"},
                  ]},
                ]
        """
        return self._base_messages(sample, image_key="image")

    def to_gemma4_messages(self, sample: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Convert a single dataset sample to Gemma-4 chat format.

        The message structure mirrors Qwen3.5-VL.  The key difference is
        that Gemma processors typically use ``"url"`` as the image-block
        key (rather than ``"image"``), but either form is handled by
        ``apply_chat_template`` at tokenization time.
        """
        return self._base_messages(sample, image_key="url")

    # ------------------------------------------------------------------
    # HuggingFace Dataset export
    # ------------------------------------------------------------------

    def to_hf_dataset(
        self,
        task_name: str,
        split: str = "train",
        chat_format: str = "qwen35",
    ) -> Any:
        """Convert a task split to a structure compatible with
        ``datasets.Dataset.from_list``.

        Parameters
        ----------
        task_name : str
            One of the seven valid task names.
        split : str
            ``"train"``, ``"val"``, ``"smoke"``, or ``"all"``.
        chat_format : str
            ``"qwen35"`` or ``"gemma4"``.

        Returns
        -------
        dataset : datasets.Dataset
            HuggingFace Dataset with columns ``messages``, ``images``
            (PIL Image objects), ``sample_id``, and ``task_type``.

            If ``datasets`` is not installed a plain list of dicts is
            returned instead (suitable for feeding into SFTTrainer via a
            custom dataloader).
        """
        if task_name not in VALID_TASK_NAMES:
            raise ValueError(
                f"Unknown task_name {task_name!r}. "
                f"Must be one of {sorted(VALID_TASK_NAMES)}."
            )
        if split not in VALID_SPLITS:
            raise ValueError(
                f"Unknown split {split!r}. Must be one of {sorted(VALID_SPLITS)}."
            )

        ds = VLMColdStartDataset(self.dataset_root, task_name, split=split)
        records: List[Dict[str, Any]] = []

        for idx in range(len(ds)):
            sample = ds[idx]
            if chat_format == "qwen35":
                messages = self.to_qwen35_messages(sample)
            elif chat_format == "gemma4":
                messages = self.to_gemma4_messages(sample)
            else:
                raise ValueError(
                    f"Unknown chat_format {chat_format!r}. "
                    f"Use 'qwen35' or 'gemma4'."
                )

            # Store image paths (strings) instead of PIL Images so PyArrow
            # can serialise the records into a Dataset.
            image_paths = self._get_image_paths(sample)
            records.append(
                {
                    "messages": messages,
                    "image_paths": image_paths,
                    "sample_id": sample["sample_id"],
                    "task_type": sample["task_type"],
                }
            )

        # Build a Dataset with Arrow-safe columns only.  The "messages"
        # column is stored as JSON strings so PyArrow can handle it.
        # The collator parses messages back and loads images from paths.
        try:
            from datasets import Dataset, Features, Value  # type: ignore[import-untyped]

            simple_records = [
                {
                    "messages_json": json.dumps(r["messages"], ensure_ascii=False),
                    "image_paths": r["image_paths"],
                    "sample_id": r["sample_id"],
                    "task_type": r["task_type"],
                }
                for r in records
            ]

            # Features: messages_json=string, image_paths=sequence of strings,
            # sample_id/task_type=string
            features = Features({
                "messages_json": Value("string"),
                "image_paths": [Value("string")],
                "sample_id": Value("string"),
                "task_type": Value("string"),
            })
            return Dataset.from_list(simple_records, features=features)
        except ImportError:
            return records

    # ------------------------------------------------------------------
    # ShareGPT / LLaMA-Factory export
    # ------------------------------------------------------------------

    def to_sharegpt_format(
        self,
        task_name: str,
        split: str = "train",
        output_dir: Union[str, Path] = "sharegpt_export",
        copy_images: bool = True,
    ) -> Path:
        """Export a task split as ShareGPT-format JSON for LLaMA-Factory.

        Writes two files under *output_dir*:

        * ``<task_name>_<split>.json`` — one ShareGPT record per sample
        * ``dataset_info.json`` — LLaMA-Factory dataset registration entry

        Parameters
        ----------
        task_name : str
        split : str
        output_dir : str or Path
            Destination directory (created if needed).
        copy_images : bool
            If True (default), copy every referenced image into
            ``<output_dir>/<task_name>/images/``.  If False the original
            absolute paths are used.

        Returns
        -------
        output_path : Path
            Path to the written JSON file.

        ShareGPT record format
        ----------------------
        .. code-block:: json

           {
             "conversations": [
               {"from": "human", "value": "<image>state summary text..."},
               {"from": "gpt",   "value": "target answer JSON..."}
             ],
             "images": ["next_probe_action/images/img001.png"]
           }
        """
        if task_name not in VALID_TASK_NAMES:
            raise ValueError(
                f"Unknown task_name {task_name!r}. "
                f"Must be one of {sorted(VALID_TASK_NAMES)}."
            )
        if split not in VALID_SPLITS:
            raise ValueError(
                f"Unknown split {split!r}. Must be one of {sorted(VALID_SPLITS)}."
            )

        output_dir = Path(output_dir)
        task_image_dir = output_dir / task_name / "images"
        task_image_dir.mkdir(parents=True, exist_ok=True)

        ds = VLMColdStartDataset(self.dataset_root, task_name, split=split)
        records: List[Dict[str, Any]] = []

        # Track copied images to avoid duplicating files
        copied: Dict[str, str] = {}  # abs_path -> relative output path

        for idx in range(len(ds)):
            sample = ds[idx]
            raw_msgs: List[Dict[str, str]] = sample["messages"]

            # Extract text content from original messages
            assistant_text = ""
            for m in raw_msgs:
                if m["role"] == "user":
                    pass  # user content replaced by structured summary
                elif m["role"] == "assistant":
                    assistant_text = m["content"]

            # Prefer structured answer from target_raw
            target = sample.get("target_raw", {})
            if "answer" in target:
                assistant_text = json.dumps(target["answer"], ensure_ascii=False)

            # Build image prefix for the human value
            image_names = self._get_image_filenames(sample)
            image_prefix = " ".join("<image>" for _ in image_names)

            # Build ShareGPT human value: <image> tokens + state summary
            state_summary = _build_state_summary(sample.get("input_raw", {}))
            human_value = f"{image_prefix}\n{state_summary}" if image_prefix else state_summary

            # Determine relative image paths for the "images" array
            relative_image_paths: List[str] = []
            abs_image_paths = self._get_image_paths(sample)
            for abs_path in abs_image_paths:
                if copy_images:
                    if abs_path not in copied:
                        filename = Path(abs_path).name
                        rel = f"{task_name}/images/{filename}"
                        dest = output_dir / rel
                        shutil.copy2(abs_path, dest)
                        copied[abs_path] = rel
                    relative_image_paths.append(copied[abs_path])
                else:
                    # Use the original absolute path
                    relative_image_paths.append(abs_path)

            records.append(
                {
                    "conversations": [
                        {"from": "human", "value": human_value},
                        {"from": "gpt", "value": assistant_text},
                    ],
                    "images": relative_image_paths,
                }
            )

        # Write ShareGPT JSON
        json_filename = f"{task_name}_{split}.json"
        json_path = output_dir / json_filename
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False, indent=2)

        # Write / update dataset_info.json
        info_path = output_dir / "dataset_info.json"
        info: Dict[str, Any] = {}
        if info_path.is_file():
            with open(info_path, "r", encoding="utf-8") as fh:
                info = json.load(fh)

        dataset_key = f"vlm_cold_start_{task_name}"
        info[dataset_key] = {
            "file_name": json_filename,
            "formatting": "sharegpt",
            "columns": {"messages": "conversations", "images": "images"},
            "tags": {
                "role_tag": "from",
                "content_tag": "value",
                "user_tag": "human",
                "assistant_tag": "gpt",
            },
        }

        with open(info_path, "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False, indent=2)

        return json_path
