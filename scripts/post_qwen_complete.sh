#!/bin/bash
set -e
export HF_ENDPOINT=https://hf-mirror.com
export HF_HOME=$HOME/.cache/huggingface
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
source ~/venv314/bin/activate

echo "=== Post-Qwen Pipeline ==="
echo "Date: $(date)"

echo "=== Step 2: Freeing disk space ==="
df -h / | tail -1
rm -rf ~/.cache/huggingface/hub/models--Qwen--* ~/.cache/pip/
rm -rf ~/checkpoints/qwen35-4b-all7/checkpoint-*/ 2>/dev/null || true
echo "After cleanup:"
df -h / | tail -1

echo "=== Step 3: Download Gemma-4-E4B ==="
if [ -d ~/.cache/huggingface/hub/models--google--gemma-4-e4b-it ]; then
    echo "Already cached."
else
    python3 -c "
import os
os.environ[HF_ENDPOINT] = https://hf-mirror.com
from huggingface_hub import snapshot_download
import sys
try:
    p = snapshot_download(google/gemma-4-e4b-it, max_workers=8)
    print(fDownloaded to: {p})
except Exception as e:
    print(fFAILED: {e})
    sys.exit(1)
" 2>&1 && echo "Download OK" || echo "Download FAILED (continuing)"
fi
df -h / | tail -1

echo "=== Step 4: Launch Gemma training ==="
if [ -d ~/.cache/huggingface/hub/models--google--gemma-4-e4b-it ]; then
    cd ~/delivery
    nohup bash ~/run_gemma4.sh > ~/train_gemma4.log 2>&1 &
    echo "Gemma PID: $!"
    echo "Log: ~/train_gemma4.log"
else
    echo "SKIPPED: Gemma model not available"
fi

echo "=== Done ==="
