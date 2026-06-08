# Training Guide

QLoRA fine-tuning for the VLM game-playing dataset on 4x RTX 5090 GPUs.

## GPU Requirements

| Item | Requirement |
|------|-------------|
| GPU | 4x RTX 5090 32 GB (or 4x A100 40 GB) |
| RAM | 128 GB+ |
| Storage | 50 GB for dataset + checkpoints |
| Python | 3.14 (matching project target) |
| CUDA | 12.4+ |
| Disk | SSD recommended for dataset access |

The training scripts use 4-bit NF4 quantization + LoRA (rank 16) to fit the model across 4 GPUs with DeepSpeed ZeRO-2. Each GPU holds a micro-batch of 2 samples with 8 gradient accumulation steps, giving an effective batch size of 64 (4 GPUs * 2 micro * 8 accum).

## Server Setup

### ssh5090 (Compute Server)

| Field | Value |
|-------|-------|
| Host | `10.19.138.148` |
| User | `tangzh` |
| Password | `4dvlab123` |
| GPU | 4x RTX 5090 32 GB |
| Python | 3.14.5 |
| ML Stack | Pre-installed (PyTorch, CUDA, Transformers, PEFT, TRL) |

### ssh50902 (Data Server)

| Field | Value |
|-------|-------|
| Host | `10.19.138.149` |
| User | `tangzh` |
| Password | `4dvlab123` |
| GPU | 4x RTX 5090 32 GB |
| Free Storage | 191 GB |

## Step-by-Step Training

### Step 1: Sync Code to ssh5090

From your local machine:

```bash
cd /home/azuma/delivery/delivery
bash scripts/scp_to_ssh5090.sh
```

This uses rsync (or scp fallback) to send `src/`, `configs/`, `scripts/`, `tests/`, `pyproject.toml`, and `.gitignore` to `/home/tangzh/delivery/` on the server. Excludes `__pycache__`, checkpoints, wandb logs, and other transient files.

### Step 2: Sync Training Data

The dataset is ~53K files (game HTMLs, screenshots, JSONL). Use the dedicated data transfer script:

```bash
bash scripts/scp_training_data.sh
```

This rsyncs `vlm-training-data-cold-start-portable-20260608/` to `/home/tangzh/data/` on ssh50902 (data server). The compute server expects the dataset at `/home/tangzh/data/vlm-training-data-cold-start-portable-20260608/`.

If you need the dataset on ssh5090 directly, copy it from ssh50902:

```bash
ssh tangzh@10.19.138.148
# Then from ssh5090:
rsync -avz tangzh@10.19.138.149:/home/tangzh/data/vlm-training-data-cold-start-portable-20260608/ /home/tangzh/data/vlm-training-data-cold-start-portable-20260608/
```

### Step 3: SSH into the Server

```bash
ssh tangzh@10.19.138.148
# Password: 4dvlab123
```

### Step 4: Install Training Dependencies (one-time)

```bash
cd /home/tangzh/delivery
pip install -e .[training]
```

If bitsandbytes fails, install it separately:

```bash
pip install bitsandbytes --no-build-isolation
```

### Step 5: Run Training

#### Qwen3.5-4B

Default training: all 7 tasks, 3 epochs, batch size 2, grad accum 8, LoRA r=16 alpha=32.

```bash
python src/training/train_qwen35.py \
    --dataset-root ../data/vlm-training-data-cold-start-portable-20260608/ \
    --model Qwen/Qwen3.5-4B \
    --tasks next_probe_action,probe_action_effect,field_grounding,information_gain_judgment,pulse_response_grounding,progression_grounding,failure_recovery \
    --output-dir checkpoints/qwen35-4b-gameplay \
    --epochs 3 --batch-size 2 --grad-accum 8 \
    --lr 2e-4 --lora-r 16 --lora-alpha 32 \
    --use-wandb --wandb-project smallgameagent
```

Testing with a single task on one GPU:

```bash
python src/training/train_qwen35.py \
    --dataset-root ../data/vlm-training-data-cold-start-portable-20260608/ \
    --tasks next_probe_action \
    --output-dir checkpoints/qwen35-4b-test \
    --epochs 1 --batch-size 1 --grad-accum 2 \
    --no-deepspeed --max-length 1024
```

#### Gemma-4-E4B

```bash
python src/training/train_gemma4.py \
    --dataset-root ../data/vlm-training-data-cold-start-portable-20260608/ \
    --model google/gemma-4-e4b-it \
    --tasks next_probe_action,information_gain_judgment,pulse_response_grounding \
    --output-dir checkpoints/gemma4-e4b-gameplay \
    --epochs 3 --batch-size 2 --grad-accum 8 \
    --lr 2e-4 --lora-r 16 --lora-alpha 32
```

### Step 6: Monitor Training

**WandB**: Add `--use-wandb --wandb-project smallgameagent` to log metrics (loss, learning rate, gradient norms). The project name is `smallgameagent`.

**Console logs**: Training scripts log at INFO level every 10 steps, showing loss, learning rate, and epoch progress.

**Checkpoints**: Saved every 500 steps to the `--output-dir` (max 3 checkpoints kept). The final adapter goes to `<output-dir>/final/`.

## Model Comparison

| Aspect | Qwen3.5-4B | Gemma-4-E4B |
|--------|------------|-------------|
| Parameters | 4B | ~4B (E4B) |
| Vision encoder | Native Qwen3VLProcessor | AutoModelForVision2Seq |
| LoRA targets | q_proj,v_proj,k_proj,o_proj | q_proj,v_proj,k_proj,o_proj,gate_proj,up_proj,down_proj |
| Chat template | Qwen3.5-VL (image key: `"image"`) | Gemma-4 (image key: `"url"`) |
| Training framework | TRL SFTTrainer | HuggingFace Trainer |
| Data collator | Custom MultimodalDataCollator | Custom VLMDataCollator |
| Quantization | 4-bit NF4 (default) | 4-bit NF4 (default) |
| Flash Attention | Flash Attention 2 | Flash Attention 2 (optional) |
| Recommended tasks | All 7 tasks | Subset (3 tasks tested) |

Both models use:
- DeepSpeed ZeRO-2 for multi-GPU
- Gradient checkpointing
- BF16 mixed precision
- Cosine LR schedule with 3% warmup

The Gemma-4 script targets additional linear modules (gate_proj, up_proj, down_proj) beyond the attention projection layers, giving it more trainable parameters per LoRA rank.

## Estimated Training Time

| Model | Samples | Epochs | GPUs | Batch/GPU | Accum | Time |
|-------|---------|--------|------|-----------|-------|------|
| Qwen3.5-4B | 9,378 | 3 | 4 | 2 | 8 | ~2-3 hours |
| Qwen3.5-4B | 1,647 (1 task) | 3 | 1 | 2 | 8 | ~30-45 min |
| Gemma-4-E4B | ~3,500 (3 tasks) | 3 | 4 | 2 | 8 | ~1.5-2 hours |

Actual times depend on GPU model, disk I/O, and whether the dataset is fully cached in RAM. The ~2-3 hour estimate assumes all 9,378 training samples across 7 tasks on 4x RTX 5090 with the default batch size and accumulation.

## Exporting to ShareGPT Format

For training with LLaMA-Factory or other frameworks:

```python
from src.training.dataset_converter import VLMDatasetConverter

converter = VLMDatasetConverter(
    "vlm-training-data-cold-start-portable-20260608/"
)
converter.to_sharegpt_format(
    task_name="next_probe_action",
    split="train",
    output_dir="sharegpt_export/",
    copy_images=True,
)
```

This writes a mult-turn ShareGPT JSON file plus `dataset_info.json` for LLaMA-Factory registration.

## Evaluating a Trained Model

After training completes, the LoRA adapter is saved in `<output-dir>/final/`. The directory contains:

```
final/
├── adapter_model.safetensors    # LoRA weights
├── adapter_config.json           # LoRA config
├── processor/                    # Processor files
│   ├── preprocessor_config.json
│   └── tokenizer.json
└── training_metadata.json        # Hyperparameters
```

To load the adapter for inference:

```python
import torch
from peft import PeftModel
from transformers import AutoProcessor, AutoModelForVision2Seq

base_model = "Qwen/Qwen3.5-4B"
adapter_path = "checkpoints/qwen35-4b-gameplay/final"

processor = AutoProcessor.from_pretrained(adapter_path)
model = AutoModelForVision2Seq.from_pretrained(
    base_model,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    attn_implementation="flash_attention_2",
)
model = PeftModel.from_pretrained(model, adapter_path)
```

For Gemma-4:

```python
from transformers import AutoModelForVision2Seq, AutoProcessor
from peft import PeftModel

base_model = "google/gemma-4-e4b-it"
adapter_path = "checkpoints/gemma4-e4b-gameplay/final"

processor = AutoProcessor.from_pretrained(adapter_path)
model = AutoModelForVision2Seq.from_pretrained(
    base_model,
    torch_dtype=torch.bfloat16,
    device_map="auto",
)
model = PeftModel.from_pretrained(model, adapter_path)
```

## Sync Results Back

To copy training checkpoints from ssh5090 to your local machine:

```bash
sshpass -p '4dvlab123' scp -r \
    tangzh@10.19.138.148:/home/tangzh/delivery/checkpoints/ \
    /home/azuma/delivery/delivery/checkpoints/
```

Or use the sync script (if modified for your use case):

```bash
bash scripts/sync_from_ssh5092.sh
```

## Troubleshooting

### CUDA Out of Memory

Reduce batch size or increase gradient accumulation:

```bash
--batch-size 1 --grad-accum 16
```

If using DeepSpeed, check that ZeRO-2 is active (it offloads optimizer states to CPU).

### bitsandbytes Import Error

The 4-bit quantization requires bitsandbytes. On RTX 5090 (Blackwell), ensure you have a compatible version:

```bash
pip install --upgrade bitsandbytes
```

If compilation fails, try installing from source:

```bash
pip install bitsandbytes --no-build-isolation
```

### Flash Attention 2 Not Available

The training script falls back to SDPA automatically. You can also force-disable:

```bash
--no-flash-attn
```

Flash Attention 2 requires a compatible GPU (Ampere+). RTX 5090 (Blackwell) supports it.

### DeepSpeed ZeRO-2 Errors

If DeepSpeed fails to initialize (e.g., NCCL timeout), try without it:

```bash
--no-deepspeed
```

This falls back to PyTorch DDP. Effective batch size will be smaller because gradient accumulation is no longer divided by GPU count.

### Dataset Path Not Found

The dataset root argument must point to a directory containing `tasks/` and `dataset-manifest.json`. Verify the path:

```bash
ls /home/tangzh/data/vlm-training-data-cold-start-portable-20260608/
# Should show: tasks/  dataset-manifest.json  dataset-stats.json
```

### Training Loss Spikes or NaN

- Reduce learning rate: `--lr 1e-4`
- Increase warmup: `--warmup-ratio 0.1`
- Check for corrupted image files in the dataset
- Ensure gradient clipping is active (default max_grad_norm=1.0)

### Missing Tokenizer File on Adapter Load

The `train_qwen35.py` script saves the processor alongside the adapter. If you get a missing tokenizer error, ensure `processor.save_pretrained()` ran (check for `preprocessor_config.json` and `tokenizer.json` in the final directory).

### WandB Connection Timeout

If the server has no internet access, disable WandB:

```bash
--no-wandb
# or for train_qwen35: omit --use-wandb
```

Metrics are still logged to console and TensorBoard in the output directory.

### Dataset Conversion Fails on Missing Images

The dataset converter warns on missing images but does not crash. If many images are missing, re-sync the full dataset:

```bash
bash scripts/scp_training_data.sh
```
