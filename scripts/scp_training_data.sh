#!/bin/bash
# ============================================================
# scp_training_data.sh — Push training data to ssh50902 (4x RTX 5090)
# Server: tangzh@10.19.138.149
# Source: vlm-training-data-cold-start-portable-20260608/
# ============================================================
set -e

SERVER="tangzh@10.19.138.149"
PASS="4dvlab123"
REMOTE_DIR="/home/tangzh/data"

# Source data directory (relative to project root)
DATA_DIR="vlm-training-data-cold-start-portable-20260608"

DRY_RUN=""

# Parse --dry-run flag
for arg in "$@"; do
    if [ "$arg" = "--dry-run" ]; then
        DRY_RUN="--dry-run"
        echo "=== DRY RUN MODE ==="
    fi
done

# Detect project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_PATH="$PROJECT_ROOT/$DATA_DIR"

if [ ! -d "$DATA_PATH" ]; then
    echo "ERROR: Data directory not found at $DATA_PATH"
    echo "Please run this script from the project root or set DATA_DIR correctly."
    exit 1
fi

# Count files for user awareness
echo "=== Pushing training data to $SERVER:$REMOTE_DIR ==="
echo "Source: $DATA_PATH"
FILE_COUNT=$(find "$DATA_PATH" -type f 2>/dev/null | wc -l)
echo "File count: ~$FILE_COUNT files"
echo "Size: $(du -sh "$DATA_PATH" 2>/dev/null | cut -f1)"
echo ""

# Ensure rsync is available (scp is impractical for ~53K files)
if ! command -v rsync &>/dev/null; then
    echo "ERROR: rsync is required for large data transfers (53K+ files)."
    echo "Install it with: apt install rsync  or  brew install rsync"
    exit 1
fi

# Ensure remote directory exists
if [ -z "$DRY_RUN" ]; then
    echo "Ensuring remote directory exists..."
    sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no "$SERVER" "mkdir -p '$REMOTE_DIR'"
fi

echo "Starting rsync transfer (this may take a while for large datasets)..."
echo ""

# rsync with progress for large transfers
# -a: archive mode (recursive, preserve symlinks/perms/times)
# -v: verbose
# -z: compress during transfer
# --progress: show per-file progress
sshpass -p "$PASS" rsync -avz --progress \
    ${DRY_RUN:+"$DRY_RUN"} \
    -e "ssh -o StrictHostKeyChecking=no" \
    "$DATA_PATH/" \
    "$SERVER:$REMOTE_DIR/$DATA_DIR/"

echo ""
echo "=== Data transfer complete ==="
echo "Target: $SERVER:$REMOTE_DIR/$DATA_DIR/"
echo "Use --dry-run to preview without transferring."
