"""
Data loader for VLM cold-start training dataset.

Loads JSONL task samples with image/assets, producing HuggingFace-compatible
sample dicts for SFT training. All asset paths in JSONL are relative to each
task directory and resolved lazily at access time.
"""

import json
import os
from pathlib import Path
from typing import Any

from PIL import Image

VALID_TASK_NAMES = frozenset(
    {
        "next_probe_action",
        "probe_action_effect",
        "field_grounding",
        "information_gain_judgment",
        "pulse_response_grounding",
        "progression_grounding",
        "failure_recovery",
    }
)

VALID_SPLITS = frozenset({"train", "val", "smoke", "all"})


class VLMColdStartDataset:
    """Lazy-loading dataset for VLM cold-start JSONL training data.

    Constructor:
        dataset_root : str or Path
            Path to ``vlm-training-data-cold-start-portable-20260608/``.
        task_name : str
            One of the seven task names (e.g. ``"next_probe_action"``).
        split : str
            ``"train"``, ``"val"``, ``"smoke"``, or ``"all"`` (default ``"train"``).

    Each item returned by ``__getitem__`` is a dict with:
        - ``sample_id``  (str)
        - ``task_type``  (str)
        - ``images``     (list[PIL.Image.Image])
        - ``messages``   (list[dict] with ``role`` and ``content`` keys)
        - ``target_raw`` (dict – parsed from the JSONL ``target`` field)
        - ``input_raw``  (dict – parsed from the JSONL ``input`` field)
    """

    def __init__(
        self,
        dataset_root: str | Path,
        task_name: str,
        split: str = "train",
    ) -> None:
        if task_name not in VALID_TASK_NAMES:
            raise ValueError(
                f"Unknown task_name {task_name!r}. "
                f"Must be one of {sorted(VALID_TASK_NAMES)}."
            )
        if split not in VALID_SPLITS:
            raise ValueError(
                f"Unknown split {split!r}. Must be one of {sorted(VALID_SPLITS)}."
            )

        self._dataset_root = Path(dataset_root)
        self._task_name = task_name
        self._split = split
        self._task_dir = self._dataset_root / "tasks" / task_name

        self._jsonl_path = self._task_dir / f"{split}.jsonl"
        if not self._jsonl_path.is_file():
            raise FileNotFoundError(
                f"JSONL file not found: {self._jsonl_path}"
            )

        # Load all sample line offsets for random access via __getitem__.
        # We store *raw* line strings (the JSON text) so that repeated
        # access to the same index re-parses quickly without file I/O,
        # while still keeping images lazy (loaded on-demand).
        self._samples: list[str] = []
        self._cached_items: dict[int, dict[str, Any]] = {}
        with open(self._jsonl_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    self._samples.append(line)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._samples)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        if idx < 0:
            idx += len(self._samples)
        if idx < 0 or idx >= len(self._samples):
            raise IndexError(f"Index {idx} out of range [0, {len(self._samples)})")

        # Return from item cache if already built (e.g. after parsing raw
        # JSON once), but re-load images each time to keep memory low.
        if idx in self._cached_items:
            item = self._cached_items[idx]
            images = self._load_images(item.get("_input", {}))
            return {
                "sample_id": item["sample_id"],
                "task_type": item["task_type"],
                "images": images,
                "messages": item["_messages"],
                "target_raw": item["_target"],
                "input_raw": item["_input"],
            }

        raw = self._samples[idx]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON at line {idx + 1} of {self._jsonl_path}"
            ) from exc

        sample_id: str = data["sample_id"]
        task_type: str = data["task_type"]

        input_raw: dict[str, Any] = data.get("input", {})
        target_raw: dict[str, Any] = data.get("target", {})
        messages: list[dict[str, str]] = data.get("messages", [])

        # Cache the parsed structures (without images) for future accesses.
        self._cached_items[idx] = {
            "sample_id": sample_id,
            "task_type": task_type,
            "_input": input_raw,
            "_target": target_raw,
            "_messages": messages,
        }

        images = self._load_images(input_raw)

        return {
            "sample_id": sample_id,
            "task_type": task_type,
            "images": images,
            "messages": messages,
            "target_raw": target_raw,
            "input_raw": input_raw,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_asset_path(self, relative_path: str) -> Path:
        """Resolve a JSONL-relative path to an absolute filesystem path.

        All paths inside the JSONL are relative to the **task** directory,
        e.g. ``"assets/images/xxx.png"`` resolves to
        ``<dataset_root>/tasks/<task_name>/assets/images/xxx.png``.
        """
        return (self._task_dir / relative_path).resolve()

    def _load_images(self, input_raw: dict[str, Any]) -> list[Image.Image]:
        """Load every image referenced in ``input_raw["images"]``."""
        image_entries: list[dict[str, Any]] = input_raw.get("images", [])
        loaded: list[Image.Image] = []
        for entry in image_entries:
            path_str: str | None = entry.get("path")
            if not path_str:
                continue
            resolved = self._resolve_asset_path(path_str)
            if resolved.is_file():
                loaded.append(Image.open(resolved).convert("RGB"))
            else:
                # Warn and skip – production training should not hit this
                # because all samples pass quality_gate validation.
                import warnings

                warnings.warn(
                    f"Image not found: {resolved} (sample may be corrupted)",
                    RuntimeWarning,
                    stacklevel=3,
                )
        return loaded
