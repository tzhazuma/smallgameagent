#!/usr/bin/env python3
"""
QLoRA fine-tuning for Qwen3.5-4B on the 7-task VLM game-playing dataset.

Trains with 4-bit NF4 quantization, LoRA adapters, and DeepSpeed ZeRO-2
across 4× RTX 5090 GPUs (32 GB each).  Uses TRL SFTTrainer with the
Qwen3.5 multimodal processor for native vision-language chat templates.

Usage::

    python src/training/train_qwen35.py \\
        --dataset-root vlm-training-data-cold-start-portable-20260608/ \\
        --model Qwen/Qwen3.5-4B \\
        --tasks next_probe_action,information_gain_judgment,pulse_response_grounding \\
        --output-dir checkpoints/qwen35-4b-gameplay \\
        --epochs 3 --batch-size 2 --grad-accum 8 \\
        --lr 2e-4 --lora-r 16 --lora-alpha 32 \\
        --use-wandb --wandb-project smallgameagent
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Early path setup – make src/ importable from project root
# ---------------------------------------------------------------------------
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

ALL_TASK_NAMES: List[str] = [
    "next_probe_action",
    "probe_action_effect",
    "field_grounding",
    "information_gain_judgment",
    "pulse_response_grounding",
    "progression_grounding",
    "failure_recovery",
]


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Build the argument parser and return parsed CLI arguments."""

    parser = argparse.ArgumentParser(
        description="QLoRA fine-tune Qwen3.5-4B on VLM game-playing dataset",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── Paths ──────────────────────────────────────────────────────────
    parser.add_argument(
        "--dataset-root",
        type=str,
        default="vlm-training-data-cold-start-portable-20260608/",
        help="Root of the portable dataset (contains 'tasks/' and 'dataset-manifest.json')",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="Qwen/Qwen3.5-4B",
        help="HuggingFace model ID (default: Qwen/Qwen3.5-4B)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="checkpoints/qwen35-4b-gameplay",
        help="Directory to save LoRA adapter weights and processor",
    )

    # ── Dataset selection ──────────────────────────────────────────────
    parser.add_argument(
        "--tasks",
        type=str,
        default=",".join(ALL_TASK_NAMES),
        help=f"Comma-separated task names. Available: {', '.join(ALL_TASK_NAMES)}",
    )
    parser.add_argument(
        "--val-split",
        type=float,
        default=0.0,
        help=(
            "Fraction of training data to hold out for validation. "
            "When 0 (default), uses the pre-split val.jsonl files from each task."
        ),
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=4096,
        help="Max sequence length for tokenisation (default: 4096)",
    )
    parser.add_argument(
        "--image-max-pixels",
        type=int,
        default=1_003_520,
        help="Max pixels per image fed to the vision encoder (default: 1003520 ≈ 980×1024)",
    )

    # ── Training hyper-parameters ──────────────────────────────────────
    parser.add_argument("--epochs", type=int, default=3, help="Number of training epochs")
    parser.add_argument(
        "--batch-size", type=int, default=2, help="Per-GPU micro batch size"
    )
    parser.add_argument(
        "--grad-accum", type=int, default=8, help="Gradient accumulation steps"
    )
    parser.add_argument("--lr", type=float, default=2e-4, help="Peak learning rate")
    parser.add_argument(
        "--warmup-ratio", type=float, default=0.03, help="Fraction of steps for LR warmup"
    )
    parser.add_argument(
        "--weight-decay", type=float, default=0.0, help="Weight decay (default: 0)"
    )
    parser.add_argument(
        "--max-grad-norm", type=float, default=1.0, help="Gradient clipping"
    )

    # ── LoRA ───────────────────────────────────────────────────────────
    parser.add_argument("--lora-r", type=int, default=16, help="LoRA rank")
    parser.add_argument("--lora-alpha", type=int, default=32, help="LoRA alpha scaling")
    parser.add_argument(
        "--lora-dropout", type=float, default=0.05, help="LoRA dropout"
    )
    parser.add_argument(
        "--lora-target-modules",
        type=str,
        default="q_proj,v_proj,k_proj,o_proj",
        help="Comma-separated LoRA target module name suffixes",
    )
    parser.add_argument(
        "--lora-vision-modules",
        type=str,
        default="",
        help=(
            "Additional vision-encoder LoRA target suffixes, e.g. "
            "'merger.ln_q,merger.mlp.0'. Leave empty to skip vision LoRA."
        ),
    )

    # ── Quantisation ───────────────────────────────────────────────────
    parser.add_argument(
        "--quant-bits",
        type=int,
        default=4,
        choices=[4, 8],
        help="BitsAndBytes quantisation bits (default: 4 for NF4)",
    )
    parser.add_argument(
        "--no-double-quant",
        action="store_true",
        help="Disable double quantisation (enabled by default for 4-bit)",
    )

    # ── Multi-GPU / DeepSpeed ──────────────────────────────────────────
    parser.add_argument(
        "--deepspeed",
        type=str,
        default="",
        help="Path to a DeepSpeed JSON config. Auto-generated ZeRO-2 config when empty.",
    )
    parser.add_argument(
        "--no-deepspeed",
        action="store_true",
        help="Disable DeepSpeed entirely (use plain DDP)",
    )

    # ── Checkpointing ──────────────────────────────────────────────────
    parser.add_argument(
        "--save-steps", type=int, default=500, help="Save checkpoint every N steps"
    )
    parser.add_argument(
        "--save-total-limit",
        type=int,
        default=3,
        help="Maximum number of checkpoints to keep",
    )
    parser.add_argument(
        "--resume-from",
        type=str,
        default="",
        help="Resume training from a checkpoint directory",
    )

    # ── Logging ────────────────────────────────────────────────────────
    parser.add_argument(
        "--use-wandb", action="store_true", help="Log to Weights & Biases"
    )
    parser.add_argument(
        "--wandb-project", type=str, default="smallgameagent", help="W&B project name"
    )
    parser.add_argument(
        "--wandb-run-name", type=str, default="", help="W&B run name (auto-generated if empty)"
    )
    parser.add_argument(
        "--log-steps", type=int, default=10, help="Log every N steps"
    )

    # ── Misc ───────────────────────────────────────────────────────────
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed for reproducibility"
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=4,
        help="DataLoader worker processes per GPU",
    )

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# DeepSpeed config builder
# ---------------------------------------------------------------------------


def _build_deepspeed_config(output_dir: Union[str, Path]) -> Path:
    """Create a ZeRO-2 DeepSpeed config file compatible with QLoRA 4-bit."""

    ds_config: Dict[str, Any] = {
        "zero_optimization": {
            "stage": 2,
            "contiguous_gradients": True,
            "overlap_comm": True,
            "reduce_scatter": True,
            "reduce_bucket_size": 5e8,
            "allgather_bucket_size": 5e8,
        },
        "bf16": {"enabled": True},
        "train_micro_batch_size_per_gpu": "auto",
        "train_batch_size": "auto",
        "gradient_accumulation_steps": "auto",
        "gradient_clipping": "auto",
    }

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ds_path = output_dir / "ds_config_zero2.json"

    with open(ds_path, "w", encoding="utf-8") as fh:
        json.dump(ds_config, fh, indent=2)

    logger.info("Wrote DeepSpeed ZeRO-2 config → %s", ds_path)
    return ds_path


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def _task_list(args: argparse.Namespace) -> List[str]:
    """Parse the --tasks comma-separated string into a validated list."""
    raw = [t.strip() for t in args.tasks.split(",") if t.strip()]
    if not raw:
        return ALL_TASK_NAMES

    for t in raw:
        if t not in ALL_TASK_NAMES:
            logger.warning(
                "Task %r is not in the known task set %s. Training will skip it.",
                t,
                sorted(ALL_TASK_NAMES),
            )
    return raw


def load_datasets(
    args: argparse.Namespace,
    task_names: List[str],
) -> Tuple[Any, Optional[Any]]:
    """Load and combine training (and validation) datasets for the given tasks.

    Uses :class:`VLMDatasetConverter` to convert JSONL → HF Dataset with
    Qwen3.5 chat-format messages and PIL images.

    Returns
    -------
    (train_ds, val_ds) : tuple
        ``train_ds`` is a concatenated ``datasets.Dataset``; ``val_ds`` may
        be ``None`` when every task's val split is empty.
    """
    from src.training.dataset_converter import VLMDatasetConverter

    converter = VLMDatasetConverter(args.dataset_root)

    train_parts: List[Any] = []
    val_parts: List[Any] = []

    for task_name in task_names:
        task_dir = converter._task_dir(task_name)
        train_path = task_dir / "train.jsonl"
        val_path = task_dir / "val.jsonl"

        if not train_path.is_file():
            logger.warning("Skipping task %r – no train.jsonl found", task_name)
            continue

        logger.info("Loading task %r (train) …", task_name)
        train_ds = converter.to_hf_dataset(task_name, split="train", chat_format="qwen35")
        train_parts.append(train_ds)

        if val_path.is_file():
            logger.info("Loading task %r (val) …", task_name)
            val_ds = converter.to_hf_dataset(task_name, split="val", chat_format="qwen35")
            val_parts.append(val_ds)

    if not train_parts:
        raise RuntimeError(
            f"No training data found for tasks {task_names} under {args.dataset_root}"
        )

    # Concatenate all task splits into a single dataset
    from datasets import concatenate_datasets as _concat

    train_dataset: Any = _concat(train_parts) if len(train_parts) > 1 else train_parts[0]
    val_dataset: Optional[Any] = (
        _concat(val_parts) if len(val_parts) > 1 else (val_parts[0] if val_parts else None)
    )

    # Optional: hold-out val split from training data when --val-split > 0
    if args.val_split > 0 and args.val_split < 1.0:
        split_dict = train_dataset.train_test_split(
            test_size=args.val_split, seed=args.seed
        )
        train_dataset = split_dict["train"]
        val_dataset = split_dict["test"]
        logger.info(
            "Held out %.1f%% of training data for validation (%d samples)",
            args.val_split * 100,
            len(val_dataset),
        )

    logger.info("Training samples: %d", len(train_dataset))
    if val_dataset is not None:
        logger.info("Validation samples: %d", len(val_dataset))

    return train_dataset, val_dataset


# ---------------------------------------------------------------------------
# Model & LoRA setup
# ---------------------------------------------------------------------------


def setup_model_and_processor(
    model_name: str,
    lora_r: int,
    lora_alpha: int,
    lora_dropout: float,
    target_modules: List[str],
    quant_bits: int,
    double_quant: bool,
    image_max_pixels: int,
) -> Tuple[Any, Any]:
    """Load Qwen3.5-4B with QLoRA 4-bit quantisation and LoRA adapters.

    Returns ``(model, processor)`` – the model is already wrapped with
    PEFT LoRA and the processor is configured with the correct image
    resolution cap.
    """
    import torch
    from transformers import (
        AutoProcessor,
        BitsAndBytesConfig,
        Qwen3_5ForConditionalGeneration,
    )
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    # ── Quantisation config ────────────────────────────────────────────
    if quant_bits == 4:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=double_quant,
        )
    else:
        bnb_config = BitsAndBytesConfig(
            load_in_8bit=True,
            llm_int8_threshold=6.0,
        )

    # ── Load base model ────────────────────────────────────────────────
    logger.info("Loading model %s (quant: %d-bit) …", model_name, quant_bits)
    try:
        model = Qwen3_5ForConditionalGeneration.from_pretrained(
            model_name,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
            torch_dtype=torch.bfloat16,
            attn_implementation="flash_attention_2",
        )
    except (ValueError, ImportError, OSError):
        logger.warning(
            "Qwen3_5ForConditionalGeneration not available; falling back "
            "to AutoModelForVision2Seq."
        )
        from transformers import AutoModelForVision2Seq

        model = AutoModelForVision2Seq.from_pretrained(
            model_name,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
            torch_dtype=torch.bfloat16,
            attn_implementation="flash_attention_2",
        )

    # ── Prepare for k-bit (QLoRA) training ─────────────────────────────
    model = prepare_model_for_kbit_training(model)
    model.config.use_cache = False
    model.gradient_checkpointing_enable()

    # ── LoRA config ────────────────────────────────────────────────────
    lora_config = LoraConfig(
        r=lora_r,
        lora_alpha=lora_alpha,
        lora_dropout=lora_dropout,
        target_modules=target_modules,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # ── Processor ──────────────────────────────────────────────────────
    processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=True)

    # Clamp image resolution to ~1M pixels
    if hasattr(processor, "image_processor") and hasattr(
        processor.image_processor, "max_pixels"
    ):
        processor.image_processor.max_pixels = image_max_pixels
    if hasattr(processor, "image_processor") and hasattr(
        processor.image_processor, "min_pixels"
    ):
        # Qwen recommends min_pixels = max_pixels // 4
        processor.image_processor.min_pixels = max(256 * 256, image_max_pixels // 4)

    logger.info(
        "Processor image max_pixels=%d, min_pixels=%d",
        getattr(processor.image_processor, "max_pixels", None),
        getattr(processor.image_processor, "min_pixels", None),
    )

    return model, processor


# ---------------------------------------------------------------------------
# Custom data collator for multimodal SFT
# ---------------------------------------------------------------------------


class MultimodalDataCollator:
    """Batch-collate multimodal samples using the Qwen3.5 processor.

    Each sample in the batch should be a dict with:

    - ``messages``: list[dict] — Qwen3.5 chat-format messages
    - ``images``:   list[PIL.Image] — images referenced in messages

    The collator applies ``apply_chat_template`` to each sample's messages,
    then tokenises the whole batch via the processor, producing
    ``input_ids``, ``attention_mask``, ``pixel_values``,
    ``image_grid_thw``, and ``labels``.
    """

    def __init__(self, processor: Any, max_length: int = 4096) -> None:
        self.processor = processor
        self.max_length = max_length

    def __call__(self, examples: List[Dict[str, Any]]) -> Dict[str, Any]:

        texts: List[str] = []
        all_images: List[Any] = []

        for ex in examples:
            messages = ex["messages"]
            images = ex.get("images", [])

            # Build text via chat template (tokenize=False → string)
            text = self.processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=False,
            )
            texts.append(text)
            all_images.append(images)

        # Flatten images for batch processing; track which belong to each sample
        flat_images: List[Any] = []
        for img_list in all_images:
            flat_images.extend(img_list)

        if flat_images:
            batch = self.processor(
                text=texts,
                images=flat_images,
                return_tensors="pt",
                padding=True,
                max_length=self.max_length,
                truncation=True,
            )
        else:
            batch = self.processor(
                text=texts,
                return_tensors="pt",
                padding=True,
                max_length=self.max_length,
                truncation=True,
            )

        # Labels = input_ids (causal LM – shift happens inside model)
        batch["labels"] = batch["input_ids"].clone()

        # Mask padding tokens in labels so loss ignores them
        if "attention_mask" in batch:
            batch["labels"][batch["attention_mask"] == 0] = -100

        return batch


# ---------------------------------------------------------------------------
# Main training entrypoint
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    # ── Seed ───────────────────────────────────────────────────────────
    import random

    import numpy as np
    import torch

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    # ── Resolve task list ──────────────────────────────────────────────
    task_names = _task_list(args)
    logger.info("Training tasks: %s", task_names)

    # ── Resolve LoRA target modules ────────────────────────────────────
    target_modules = [
        m.strip() for m in args.lora_target_modules.split(",") if m.strip()
    ]
    if args.lora_vision_modules:
        vision_modules = [
            m.strip() for m in args.lora_vision_modules.split(",") if m.strip()
        ]
        target_modules.extend(vision_modules)
    logger.info("LoRA target modules: %s", target_modules)

    # ── Load datasets ──────────────────────────────────────────────────
    train_dataset, val_dataset = load_datasets(args, task_names)

    # ── Model & processor ──────────────────────────────────────────────
    model, processor = setup_model_and_processor(
        model_name=args.model,
        lora_r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=target_modules,
        quant_bits=args.quant_bits,
        double_quant=not args.no_double_quant,
        image_max_pixels=args.image_max_pixels,
    )

    # ── DeepSpeed config ──────────────────────────────────────────────
    deepspeed_config: Optional[str] = None
    if not args.no_deepspeed:
        if args.deepspeed:
            deepspeed_config = args.deepspeed
        else:
            deepspeed_config = str(_build_deepspeed_config(args.output_dir))

    # ── Training args ──────────────────────────────────────────────────
    output_dir = Path(args.output_dir)

    # Calculate total training steps for the scheduler
    num_gpus: int = torch.cuda.device_count() if torch.cuda.is_available() else 1
    if num_gpus == 0:
        num_gpus = 1

    from transformers import TrainingArguments

    training_args = TrainingArguments(
        # Output
        output_dir=str(output_dir),
        overwrite_output_dir=False,
        # Batch & steps
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        num_train_epochs=args.epochs,
        # Precision
        bf16=True,
        bf16_full_eval=True,
        # Optimiser & scheduler
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=args.warmup_ratio,
        weight_decay=args.weight_decay,
        max_grad_norm=args.max_grad_norm,
        optim="adamw_torch_fused",
        # Evaluation
        eval_strategy="steps" if val_dataset is not None else "no",
        eval_steps=args.save_steps,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=args.save_total_limit,
        # DeepSpeed
        deepspeed=deepspeed_config,
        # Logging
        logging_steps=args.log_steps,
        logging_first_step=True,
        report_to=["wandb"] if args.use_wandb else ["none"],
        # Misc
        seed=args.seed,
        dataloader_num_workers=args.num_workers,
        remove_unused_columns=False,  # keep messages/images columns for collator
        ddp_find_unused_parameters=False,
        gradient_checkpointing=True,
    )

    # ── W&B run name ───────────────────────────────────────────────────
    if args.use_wandb:
        run_name = args.wandb_run_name or (
            f"qwen35-4b-r{args.lora_r}-lr{args.lr:.0e}-ep{args.epochs}"
        )
        os.environ.setdefault("WANDB_PROJECT", args.wandb_project)
        os.environ.setdefault("WANDB_NAME", run_name)
        logger.info("W&B project: %s  run: %s", args.wandb_project, run_name)

    # ── SFTTrainer ─────────────────────────────────────────────────────
    from trl import SFTTrainer

    data_collator = MultimodalDataCollator(processor, max_length=args.max_length)

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        data_collator=data_collator,
        processing_class=None,  # we collate ourselves
    )

    # ── Resume from checkpoint ─────────────────────────────────────────
    resume_from_checkpoint: Optional[str] = None
    if args.resume_from:
        resume_from_checkpoint = args.resume_from
        logger.info("Resuming from checkpoint: %s", resume_from_checkpoint)

    # ── Train ──────────────────────────────────────────────────────────
    logger.info("Starting training …")
    trainer.train(resume_from_checkpoint=resume_from_checkpoint)

    # ── Save final adapters & processor ────────────────────────────────
    final_dir = output_dir / "final"
    logger.info("Saving final LoRA adapter to %s", final_dir)
    trainer.save_model(str(final_dir))

    processor.save_pretrained(str(final_dir))
    logger.info("Processor saved to %s", final_dir)

    # Write a small metadata file so downstream inference knows the base model
    meta: Dict[str, Any] = {
        "base_model": args.model,
        "lora_r": args.lora_r,
        "lora_alpha": args.lora_alpha,
        "image_max_pixels": args.image_max_pixels,
        "tasks": task_names,
        "training_samples": len(train_dataset),
    }
    with open(final_dir / "training_metadata.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)

    logger.info("Training complete.  Adapter saved → %s", final_dir)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
