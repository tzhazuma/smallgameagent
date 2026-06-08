#!/usr/bin/env bash
# Sync training results from ssh5090 (compute server, 10.19.138.148).
# Usage: bash scripts/sync_from_ssh5090.sh [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REMOTE_USER="tangzh"
REMOTE_HOST="10.19.138.148"
REMOTE_PASS="4dvlab123"
REMOTE_DIR="/home/tangzh/checkpoints"
LOCAL_DIR="$PROJECT_ROOT/checkpoints"

DRY_RUN=""
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN="--dry-run"
    echo "[DRY RUN] No files will be transferred."
fi

echo "=== Syncing checkpoints from $REMOTE_USER@$REMOTE_HOST ==="
echo "Remote: $REMOTE_DIR"
echo "Local:  $LOCAL_DIR"
echo ""

# Sync the full checkpoints directory
sshpass -p "$REMOTE_PASS" rsync -avz --no-links $DRY_RUN \
    -e "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10" \
    "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/" \
    "$LOCAL_DIR/"

echo ""
echo "=== Syncing training logs ==="

sshpass -p "$REMOTE_PASS" rsync -avz $DRY_RUN \
    -e "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10" \
    "$REMOTE_USER@$REMOTE_HOST:~/train_qwen35_v2.log" \
    "$PROJECT_ROOT/train_qwen35_v2.log"

sshpass -p "$REMOTE_PASS" rsync -avz $DRY_RUN \
    -e "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10" \
    "$REMOTE_USER@$REMOTE_HOST:~/train_gemma4.log" \
    "$PROJECT_ROOT/train_gemma4.log" 2>/dev/null || true

echo ""
echo "=== Done ==="
echo "Checkpoints → $LOCAL_DIR"
echo "Logs       → $PROJECT_ROOT/train_qwen35_v2.log"
