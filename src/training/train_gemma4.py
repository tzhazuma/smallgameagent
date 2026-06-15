#!/usr/bin/env python3
"""
QLoRA fine-tuning for Gemma-4-E4B on the 7-task game-playing dataset.

Uses 4-bit NF4 quantization (bitsandbytes) + LoRA (rank=16, alpha=32) for
memory-efficient multimodal SFT.  Designed for 4× RTX 5090 32 GB with
DeepSpeed ZeRO-2 (optional; falls back to DDP when ``--no-deepspeed``).

Usage::

    python src/training/train_gemma4.py          \
        --dataset-root vlm-training-data-cold-start-portable-20260608/ \
        --model google/gemma-4-e4b-it            \
        --tasks next_probe_action,information_gain_judgment,pulse_response_grounding \
        --output-dir checkpoints/gemma4-e4b-gameplay \
        --epochs 3 --batch-size 2 --grad-accum 8 \
        --lr 2e-4 --lora-r 16 --lora-alpha 32
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import torch
from datasets import Dataset, concatenate_datasets
from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoProcessor,
    BitsAndBytesConfig,
    Gemma4ForConditionalGeneration,
    Trainer,
    TrainingArguments,
)

from src.training.data_loader import VALID_SPLITS, VALID_TASK_NAMES
from src.training.dataset_converter import VLMDatasetConverter

logger = logging.getLogger("train_gemma4")

# ---------------------------------------------------------------------------
# DeepSpeed ZeRO-2 configuration
# ---------------------------------------------------------------------------

DEEPSPEED_ZERO2: dict[str, Any] = {
    "train_batch_size": "auto",
    "train_micro_batch_size_per_gpu": "auto",
    "gradient_accumulation_steps": "auto",
    "zero_optimization": {
        "stage": 2,
        "offload_optimizer": {"device": "cpu"},
        "overlap_comm": True,
        "contiguous_gradients": True,
        "reduce_bucket_size": 2e8,
        "allgather_bucket_size": 2e8,
        "reduce_scatter": True,
    },
    "bf16": {"enabled": True},
    "gradient_clipping": "auto",
    "zero_allow_untested_optimizer": True,
}

# ---------------------------------------------------------------------------
# LoRA target modules for Gemma-4 attention + gated MLP
# ---------------------------------------------------------------------------

GEMMA4_LORA_TARGETS = [
    "q_proj",
    "v_proj",
    "k_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
]

# ---------------------------------------------------------------------------
# Multimodal data collator
# ---------------------------------------------------------------------------


class VLMDataCollator:
    """Tokenises Gemma-4 chat-format samples individually, then pads.

    Each sample carries a ``messages`` list (ShareGPT-style with ``"type":
    "image"`` / ``"type": "text"`` content blocks) and an optional
    ``images`` list of PIL Images.

    Variable image counts across samples are handled by tokenising one
    sample at a time and **concatenating** ``pixel_values`` across the
    batch.  The Gemma-4 model internally sub-divides the flat
    ``pixel_values`` tensor according to the number of ``<image_soft_token>``
    tokens in each sample's ``input_ids``.
    """

    def __init__(self, processor: AutoProcessor, max_length: int = 4096) -> None:
        self.processor = processor
        self.max_length = max_length

    def __call__(self, examples: list[dict[str, Any]]) -> dict[str, Any]:
        import json as _json
        from PIL import Image

        batch_ids: list[torch.Tensor] = []
        batch_masks: list[torch.Tensor] = []
        batch_labels: list[torch.Tensor] = []
        all_pixel_values: list[torch.Tensor] = []

        for ex in examples:
            # Parse messages from JSON string (PyArrow-safe storage)
            messages: list[dict[str, Any]] = (
                _json.loads(ex["messages_json"])
                if isinstance(ex.get("messages_json"), str)
                else ex.get("messages", [])
            )
            # Load images from paths (stored as strings for Arrow compat)
            img_paths: list[str] = ex.get("image_paths", []) or []
            images: list[Any] = [Image.open(p).convert("RGB") for p in img_paths]

            # Build raw prompt string via Gemma-4 chat template
            prompt: str = self.processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=False,
            )

            # Tokenise text + images — the processor inserts
            # <image_soft_token> placeholders and encodes pixel values.
            if images:
                inputs = self.processor(
                    text=prompt,
                    images=images,
                    return_tensors="pt",
                    truncation=True,
                    max_length=self.max_length,
                )
            else:
                inputs = self.processor(
                    text=prompt,
                    return_tensors="pt",
                    truncation=True,
                    max_length=self.max_length,
                )

            ids = inputs["input_ids"].squeeze(0)  # [seq_len]
            mask = inputs["attention_mask"].squeeze(0)

            batch_ids.append(ids)
            batch_masks.append(mask)
            batch_labels.append(ids.clone())

            # Collect per-sample pixel values for later concatenation.
            # Shape: [n_images_this_sample, C, H, W]
            if "pixel_values" in inputs:
                all_pixel_values.append(inputs["pixel_values"])

        # ---- Pad token tensors (manual, Gemma4Processor has no .pad()) ----
        from torch.nn.utils.rnn import pad_sequence

        padded_ids = pad_sequence(batch_ids, batch_first=True, padding_value=0)
        padded_masks = pad_sequence(batch_masks, batch_first=True, padding_value=0)
        max_len = padded_ids.shape[1]

        padded_labels: list[torch.Tensor] = []
        for lab in batch_labels:
            pad_len = max_len - lab.shape[0]
            if pad_len > 0:
                lab = torch.cat(
                    [lab, torch.full((pad_len,), -100, dtype=lab.dtype)]
                )
            padded_labels.append(lab)

        padded = {
            "input_ids": padded_ids,
            "attention_mask": padded_masks,
            "labels": torch.stack(padded_labels),
        }

        # Concatenate all pixel values into a single flat tensor.
        # The Gemma-4 model splits this internally by counting
        # <image_soft_token> tokens per sample.
        if all_pixel_values:
            padded["pixel_values"] = torch.cat(all_pixel_values, dim=0)

        return padded


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    epilog = (
        "Examples\n"
        "--------\n"
        "  # Single GPU, tiny test:\n"
        '  python src/training/train_gemma4.py --dataset-root DATA/ \\\n'
        "      --tasks next_probe_action --batch-size 1 --epochs 1 \\\n"
        "      --no-deepspeed --no-wandb\n\n"
        "  # 4× RTX 5090 production run:\n"
        '  accelerate launch src/training/train_gemma4.py \\\n'
        "      --dataset-root vlm-training-data-cold-start-portable-20260608/ \\\n"
        "      --tasks next_probe_action,information_gain_judgment,pulse_response_grounding \\\n"
        "      --output-dir checkpoints/gemma4-e4b-gameplay \\\n"
        "      --epochs 3 --batch-size 2 --grad-accum 8"
    )

    parser = argparse.ArgumentParser(
        description="QLoRA fine-tune Gemma-4-E4B on game-playing dataset",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ---- Data ----
    group_data = parser.add_argument_group("Dataset")
    group_data.add_argument(
        "--dataset-root",
        required=True,
        type=Path,
        help="Root of vlm-training-data-cold-start-portable-20260608/ "
        "(directory containing tasks/ and dataset-manifest.json)",
    )
    group_data.add_argument(
        "--tasks",
        type=str,
        default="next_probe_action,information_gain_judgment,pulse_response_grounding",
        help=(
            "Comma-separated task names.  Valid: %s"
            % ", ".join(sorted(VALID_TASK_NAMES))
        ),
    )
    group_data.add_argument(
        "--train-split",
        default="train",
        help="Dataset split for training (default: train)",
    )
    group_data.add_argument(
        "--val-split",
        default="val",
        help="Dataset split for validation (default: val; empty string to skip)",
    )

    # ---- Model ----
    group_model = parser.add_argument_group("Model")
    group_model.add_argument(
        "--model",
        default="google/gemma-4-e4b-it",
        help="HuggingFace model ID (default: google/gemma-4-e4b-it)",
    )

    # ---- LoRA ----
    group_lora = parser.add_argument_group("LoRA / QLoRA")
    group_lora.add_argument("--lora-r", type=int, default=16)
    group_lora.add_argument("--lora-alpha", type=int, default=32)
    group_lora.add_argument("--lora-dropout", type=float, default=0.05)
    group_lora.add_argument(
        "--lora-targets",
        type=str,
        default=",".join(GEMMA4_LORA_TARGETS),
        help="Comma-separated linear module names to inject LoRA into",
    )

    # ---- Training hyper-parameters ----
    group_train = parser.add_argument_group("Training")
    group_train.add_argument(
        "--output-dir",
        type=Path,
        default=Path("checkpoints/gemma4-e4b-gameplay"),
    )
    group_train.add_argument("--epochs", type=int, default=3)
    group_train.add_argument(
        "--batch-size", type=int, default=2, help="Per-GPU micro-batch size"
    )
    group_train.add_argument(
        "--grad-accum", type=int, default=8, help="Gradient accumulation steps"
    )
    group_train.add_argument("--lr", type=float, default=2e-4)
    group_train.add_argument("--warmup-ratio", type=float, default=0.03)
    group_train.add_argument("--max-length", type=int, default=4096)
    group_train.add_argument("--save-steps", type=int, default=500)
    group_train.add_argument("--save-total-limit", type=int, default=3)
    group_train.add_argument(
        "--eval-steps", type=int, default=500, help="Evaluation step interval"
    )
    group_train.add_argument("--logging-steps", type=int, default=10)

    # ---- Compute / hardware ----
    group_comp = parser.add_argument_group("Compute")
    group_comp.add_argument(
        "--no-deepspeed",
        action="store_true",
        help="Disable DeepSpeed ZeRO-2 (fall back to PyTorch DDP)",
    )
    group_comp.add_argument(
        "--no-flash-attn",
        action="store_true",
        help="Disable Flash Attention 2 (fall back to SDPA)",
    )
    group_comp.add_argument(
        "--no-wandb",
        action="store_true",
        help="Disable WandB logging (TensorBoard only)",
    )
    group_comp.add_argument("--seed", type=int, default=42)

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_datasets(
    args: argparse.Namespace,
) -> tuple[Dataset, Dataset | None]:
    """Load and concatenate task datasets via ``VLMDatasetConverter``.

    Returns
    -------
    (train_ds, eval_ds)
        eval_ds is ``None`` when ``--val-split`` is empty.
    """
    task_list: list[str] = [t.strip() for t in args.tasks.split(",") if t.strip()]
    if not task_list:
        raise ValueError("At least one task required via --tasks")

    # Validate early
    for task_name in task_list:
        if task_name not in VALID_TASK_NAMES:
            raise ValueError(
                f"Unknown task {task_name!r}. "
                f"Must be one of {sorted(VALID_TASK_NAMES)}."
            )
    if args.train_split not in VALID_SPLITS:
        raise ValueError(
            f"Unknown split {args.train_split!r}. "
            f"Must be one of {sorted(VALID_SPLITS)}."
        )
    if args.val_split and args.val_split not in VALID_SPLITS:
        raise ValueError(
            f"Unknown split {args.val_split!r}. "
            f"Must be one of {sorted(VALID_SPLITS)}."
        )

    converter = VLMDatasetConverter(args.dataset_root)

    train_parts: list[Dataset] = []
    eval_parts: list[Dataset] = []

    for task_name in task_list:
        # Training split
        raw_train = converter.to_hf_dataset(
            task_name, split=args.train_split, chat_format="gemma4"
        )
        if not isinstance(raw_train, Dataset):
            raw_train = Dataset.from_list(raw_train)
        train_parts.append(raw_train)
        logger.info(
            "Loaded %s/%s: %d samples",
            task_name,
            args.train_split,
            len(raw_train),
        )

        # Validation split
        if args.val_split:
            raw_val = converter.to_hf_dataset(
                task_name, split=args.val_split, chat_format="gemma4"
            )
            if not isinstance(raw_val, Dataset):
                raw_val = Dataset.from_list(raw_val)
            eval_parts.append(raw_val)
            logger.info(
                "Loaded %s/%s: %d samples",
                task_name,
                args.val_split,
                len(raw_val),
            )

    train_ds: Dataset = (
        concatenate_datasets(train_parts) if len(train_parts) > 1 else train_parts[0]
    )
    train_ds = train_ds.shuffle(seed=args.seed)

    eval_ds: Dataset | None = None
    if eval_parts:
        eval_ds = (
            concatenate_datasets(eval_parts)
            if len(eval_parts) > 1
            else eval_parts[0]
        )

    logger.info("Total training samples:   %d", len(train_ds))
    if eval_ds is not None:
        logger.info("Total validation samples: %d", len(eval_ds))

    return train_ds, eval_ds


# ---------------------------------------------------------------------------
# Model & LoRA builder
# ---------------------------------------------------------------------------


def build_model_and_processor(
    args: argparse.Namespace,
    use_deepspeed: bool,
) -> tuple[Any, AutoProcessor]:
    """Load Gemma-4-E4B with 4-bit NF4 QLoRA and inject LoRA adapters."""
    logger.info("Loading processor: %s", args.model)
    processor = AutoProcessor.from_pretrained(args.model, trust_remote_code=True)

    # BitsAndBytes 4-bit NF4 quantization
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    # Flash Attention 2 (optional)
    attn_impl = (
        "flash_attention_2"
        if not args.no_flash_attn and torch.cuda.is_available()
        else "sdpa"
    )

    logger.info(
        "Loading model: %s  [4-bit NF4, %s, deepspeed=%s]",
        args.model,
        attn_impl,
        use_deepspeed,
    )

    # When DeepSpeed is active, avoid device_map="auto" because DeepSpeed
    # manages its own device placement.  Load on cuda:0 and let DeepSpeed
    # replicate across GPUs (ZeRO-2 replicates parameters).
    # ---- Monkey-patch Gemma4VisionModel to handle bool pixel_position_ids ----
    # Bug in transformers 5.9.0: pixel_position_ids can be False/True (bool)
    # instead of a LongTensor. Convert it to a proper padding tensor.
    import transformers.models.gemma4.modeling_gemma4 as _gemma_model

    _orig_vision_model_forward = _gemma_model.Gemma4VisionModel.forward

    def _patched_vision_model_forward(self, pixel_values, pixel_position_ids=None, **kwargs):
        if isinstance(pixel_position_ids, bool) or pixel_position_ids is None or (isinstance(pixel_position_ids, torch.Tensor) and pixel_position_ids.dtype == torch.bool):
            if pixel_values is not None and isinstance(pixel_values, torch.Tensor):
                bsz = pixel_values.shape[0]
                num_patches = pixel_values.shape[1]
                # Find closest factor pair for num_patches (grid may be rectangular)
                side = int(num_patches ** 0.5)
                while side > 0:
                    if num_patches % side == 0:
                        break
                    side -= 1
                h, w = side, num_patches // side
                y_coords = torch.arange(h, device=pixel_values.device).repeat_interleave(w)
                x_coords = torch.arange(w, device=pixel_values.device).repeat(h)
                grid = torch.stack([x_coords, y_coords], dim=-1)
                pixel_position_ids = grid.unsqueeze(0).expand(bsz, -1, -1).contiguous()
            else:
                pixel_position_ids = None
        return _orig_vision_model_forward(self, pixel_values, pixel_position_ids=pixel_position_ids, **kwargs)

    _gemma_model.Gemma4VisionModel.forward = _patched_vision_model_forward

    # ---- Monkey-patch PEFT to support Gemma4ClippableLinear ----
    import peft.tuners.lora.model as _peft_model
    import peft.tuners.lora.bnb as _peft_bnb
    import bitsandbytes as _bnb

    _orig_create_new_module = _peft_model.LoraModel._create_new_module

    @staticmethod
    def _patched_create_new_module(lora_config, adapter_name, target, **kwargs):
        target_cls = type(target).__name__
        if target_cls == "Gemma4ClippableLinear" and hasattr(target, "linear"):
            inner = target.linear
            if isinstance(inner, _bnb.nn.Linear4bit):
                kwargs.pop("device_map", None)
                result = _peft_bnb.dispatch_bnb_4bit(inner, adapter_name, config=lora_config, **kwargs)
                if result is not None:
                    result.linear = inner
                    return result
        return _orig_create_new_module(lora_config, adapter_name, target, **kwargs)

    _peft_model.LoraModel._create_new_module = _patched_create_new_module

    # When DeepSpeed is active, avoid device_map="auto" because DeepSpeed
    # manages its own device placement.  Load on cuda:0.
    # For DDP (accelerate/torchrun), each rank loads the model on its own GPU.
    if use_deepspeed:
        device_map = {"": f"cuda:{torch.cuda.current_device()}"}
    elif "LOCAL_RANK" in os.environ:
        local_rank = int(os.environ["LOCAL_RANK"])
        device_map = {"": f"cuda:{local_rank}"}
    else:
        device_map = "auto"

    try:
        model = Gemma4ForConditionalGeneration.from_pretrained(
            args.model,
            quantization_config=bnb_config,
            device_map=device_map,
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
            attn_implementation=attn_impl,
        )
    except Exception:
        logger.warning(
            "Loading with %s failed; retrying with default attention", attn_impl
        )
        model = Gemma4ForConditionalGeneration.from_pretrained(
            args.model,
            quantization_config=bnb_config,
            device_map=device_map,
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
        )

    # ---- QLoRA prep ----
    model = prepare_model_for_kbit_training(model)
    model.gradient_checkpointing_enable()

    # ---- Inject LoRA ----
    target_modules: list[str] = [
        t.strip() for t in args.lora_targets.split(",") if t.strip()
    ]
    logger.info(
        "Applying LoRA: r=%d  alpha=%d  dropout=%.2f  targets=%s",
        args.lora_r,
        args.lora_alpha,
        args.lora_dropout,
        target_modules,
    )

    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=target_modules,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
    )

    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # Log processor details
    if hasattr(processor, "image_processor"):
        img_proc = processor.image_processor
        size_attr = getattr(img_proc, "size", "?")
        logger.info("Image processor size: %s", size_attr)

    return model, processor


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-5s] %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    gpu_count = torch.cuda.device_count()
    logger.info("CUDA devices: %d  (visible)", gpu_count)

    # ---- Output directory ----
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # ---- WandB init ----
    run_name = args.output_dir.name
    if not args.no_wandb:
        os.environ.setdefault("WANDB_PROJECT", "vlm-gameplay")
        os.environ.setdefault("WANDB_RUN_NAME", run_name)
        try:
            import wandb  # noqa: F401  — side-effect: import checks availability
        except ImportError:
            logger.warning("wandb not installed — falling back to TensorBoard")
            args.no_wandb = True

    # ---- DeepSpeed config file ----
    use_deepspeed = bool(
        not args.no_deepspeed and gpu_count > 1
    )
    deepspeed_config_path: str | None = None
    if use_deepspeed:
        ds_path = args.output_dir / "ds_zero2.json"
        with open(ds_path, "w", encoding="utf-8") as fh:
            json.dump(DEEPSPEED_ZERO2, fh, indent=2)
        deepspeed_config_path = str(ds_path)
        logger.info("DeepSpeed ZeRO-2 config written → %s", ds_path)
    else:
        logger.info("DeepSpeed disabled (%s)", "single GPU" if gpu_count <= 1 else "--no-deepspeed")

    # ---- Data ----
    train_ds, eval_ds = load_datasets(args)

    # ---- Model + processor ----
    model, processor = build_model_and_processor(args, use_deepspeed)

    # ---- Data collator ----
    collator = VLMDataCollator(processor, max_length=args.max_length)

    # ---- Training arguments ----
    report_to: list[str] = []
    if not args.no_wandb:
        report_to.append("wandb")

    # Use the user-specified gradient accumulation steps directly.
    # DeepSpeed ZeRO-2 already handles per-GPU scaling:
    # effective_batch = gpu_count × batch_size × grad_accum.

    training_args = TrainingArguments(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_ratio=args.warmup_ratio,
        lr_scheduler_type="cosine",
        # Logging
        logging_steps=args.logging_steps,
        logging_first_step=True,
        # Checkpointing — saves LoRA adapter + optimizer states every N steps
        save_steps=args.save_steps,
        save_total_limit=args.save_total_limit,
        save_strategy="steps",

        # Evaluation
        eval_strategy="steps" if eval_ds is not None else "no",
        eval_steps=args.eval_steps if eval_ds is not None else None,
        load_best_model_at_end=eval_ds is not None,
        metric_for_best_model="eval_loss" if eval_ds is not None else None,
        greater_is_better=False,
        # Precision
        bf16=True,
        bf16_full_eval=True,
        # Gradient checkpointing
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        # DeepSpeed
        deepspeed=deepspeed_config_path,
        # Data loading
        remove_unused_columns=False,  # keep "images" column for collator
        dataloader_num_workers=0,     # PIL images in dataset → single-process
        dataloader_pin_memory=True,
        # Reproducibility
        seed=args.seed,
        data_seed=args.seed + 1,
        # Reporting
        report_to=report_to if report_to else [],
        run_name=run_name,
        # Multi-GPU fallback
        ddp_find_unused_parameters=True,
        ddp_backend="nccl" if gpu_count > 1 else None,
        # Misc
        label_names=["labels"],
        include_for_metrics=["inputs", "loss"],
    )

    logger.info("Effective batch size: %d × %d × %d = %d",
                gpu_count or 1, args.batch_size, args.grad_accum,
                (gpu_count or 1) * args.batch_size * args.grad_accum)

    # ---- Trainer ----
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        data_collator=collator,
    )

    # ---- Train ----
    logger.info("=" * 60)
    logger.info("Starting training on %d sample(s)", len(train_ds))
    logger.info("Output directory: %s", args.output_dir)
    logger.info("=" * 60)

    trainer.train()

    # ---- Save final LoRA adapter ----
    final_dir = args.output_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(final_dir))
    logger.info("Final LoRA adapter saved → %s", final_dir)

    # ---- Save command-line args for reproducibility ----
    args_path = args.output_dir / "training_args.json"
    with open(args_path, "w", encoding="utf-8") as fh:
        json.dump(vars(args), fh, indent=2, default=str)
    logger.info("Training args saved → %s", args_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
