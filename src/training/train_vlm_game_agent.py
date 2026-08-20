"""QLoRA/bf16 LoRA 微调 VLM 用于游戏 agent 动作预测。

支持模型: Qwen3-VL-8B, Gemma-4-12B, Gemma-4-E4B
支持模式: QLoRA (4bit) / bf16 LoRA (全精度)

用法 (4×5090 服务器):
    # bf16 LoRA (推荐，精度更好)
    accelerate launch --multi_gpu src/training/train_vlm_game_agent.py \
        --model Qwen/Qwen3-VL-8B-Instruct \
        --data training_data.jsonl \
        --output checkpoints/game-agent-vlm

    # QLoRA (省显存)
    accelerate launch --multi_gpu src/training/train_vlm_game_agent.py \
        --model google/gemma-4-12B-it \
        --data training_data.jsonl \
        --qlora \
        --output checkpoints/game-agent-vlm

    # 单卡 QLoRA (小模型)
    python src/training/train_vlm_game_agent.py \
        --model Qwen/Qwen3-VL-4B-Instruct \
        --data training_data.jsonl \
        --qlora \
        --output checkpoints/game-agent-vlm
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("train_vlm")


def parse_args():
    ap = argparse.ArgumentParser(description="VLM Game Agent QLoRA/LoRA 微调")
    ap.add_argument("--model", default="Qwen/Qwen3-VL-8B-Instruct",
                    help="模型名称或路径")
    ap.add_argument("--data", required=True, help="训练数据 JSONL 文件路径")
    ap.add_argument("--output", default="checkpoints/game-agent-vlm", help="输出目录")
    ap.add_argument("--qlora", action="store_true", help="使用 QLoRA (4bit 量化)")
    ap.add_argument("--epochs", type=int, default=3, help="训练轮数")
    ap.add_argument("--batch-size", type=int, default=2, help="每 GPU batch size")
    ap.add_argument("--grad-accum", type=int, default=8, help="梯度累积步数")
    ap.add_argument("--lr", type=float, default=2e-4, help="学习率")
    ap.add_argument("--max-seq-len", type=int, default=2048, help="最大序列长度")
    ap.add_argument("--lora-r", type=int, default=16, help="LoRA rank")
    ap.add_argument("--lora-alpha", type=int, default=32, help="LoRA alpha")
    ap.add_argument("--warmup-ratio", type=float, default=0.05, help="warmup 比例")
    ap.add_argument("--logging-steps", type=int, default=10, help="日志间隔")
    ap.add_argument("--save-steps", type=int, default=200, help="保存间隔")
    ap.add_argument("--eval-split", type=float, default=0.1, help="验证集比例")
    ap.add_argument("--seed", type=int, default=42)
    return ap.parse_args()


def load_dataset_jsonl(path: str) -> list[dict]:
    """加载 JSONL 格式的训练数据。"""
    samples = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            samples.append(json.loads(line))
    logger.info("加载 %d 条训练样本 from %s", len(samples), path)
    return samples


def create_hf_dataset(samples: list[dict], processor, max_length: int):
    """将训练样本转换为 HuggingFace Dataset，使用 processor 处理多模态输入。"""
    from datasets import Dataset

    def process_sample(sample):
        messages = sample["messages"]
        # 使用 processor 的 chat template 处理多模态消息
        text = processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=False
        )
        # 处理图像
        images = []
        for msg in messages:
            if isinstance(msg.get("content"), list):
                for part in msg["content"]:
                    if isinstance(part, dict) and part.get("type") == "image":
                        img_data = part.get("image", "")
                        if img_data.startswith("data:image"):
                            import base64, io
                            from PIL import Image
                            b64 = img_data.split(",", 1)[1]
                            img = Image.open(io.BytesIO(base64.b64decode(b64)))
                            images.append(img)

        return {"text": text, "images": images}

    processed = [process_sample(s) for s in samples]
    dataset = Dataset.from_list(processed)
    return dataset


def train(args):
    import torch
    from transformers import (
        AutoModelForCausalLM,
        AutoProcessor,
        BitsAndBytesConfig,
    )
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    logger.info("加载模型: %s (QLoRA=%s)", args.model, args.qlora)

    # 量化配置
    bnb_config = None
    if args.qlora:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    # 加载模型
    model_kwargs = {
        "trust_remote_code": True,
        "torch_dtype": torch.bfloat16,
    }
    if bnb_config:
        model_kwargs["quantization_config"] = bnb_config
        model_kwargs["device_map"] = "auto"
    else:
        model_kwargs["device_map"] = "auto"

    model = AutoModelForCausalLM.from_pretrained(args.model, **model_kwargs)
    processor = AutoProcessor.from_pretrained(args.model, trust_remote_code=True)

    # LoRA 配置
    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules="all-linear",
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )

    if args.qlora:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)

    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # 加载数据
    samples = load_dataset_jsonl(args.data)
    dataset = create_hf_dataset(samples, processor, args.max_seq_len)

    # 训练配置
    from trl import SFTConfig, SFTTrainer

    training_args = SFTConfig(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=args.warmup_ratio,
        bf16=True,
        gradient_checkpointing=True,
        max_seq_length=args.max_seq_len,
        logging_steps=args.logging_steps,
        save_strategy="steps",
        save_steps=args.save_steps,
        eval_strategy="steps",
        eval_steps=args.save_steps,
        optim="paged_adamw_8bit" if args.qlora else "adamw_bf16",
        seed=args.seed,
        remove_unused_columns=False,
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        processing_class=processor,
    )

    logger.info("开始训练: %d 样本, %d epochs, bs=%d×%d grad_accum=%d",
                len(dataset), args.epochs, args.batch_size,
                torch.cuda.device_count(), args.grad_accum)

    trainer.train()

    # 保存
    trainer.save_model(args.output)
    processor.save_pretrained(args.output)
    logger.info("模型已保存到: %s", args.output)

    # 导出 GGUF (可选)
    logger.info("训练完成！如需部署为 llama.cpp 格式，使用:")
    logger.info("  python convert_hf_to_gguf.py %s --outfile model.gguf --outtype q4_k_m", args.output)


if __name__ == "__main__":
    train(parse_args())
