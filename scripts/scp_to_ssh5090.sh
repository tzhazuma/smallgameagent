#!/bin/bash
# ============================================================
# scp_to_ssh5090.sh — Push project code to ssh5090 (4x RTX 5090)
# Server: tangzh@10.19.138.148
# ============================================================
set -e

SERVER="tangzh@10.19.138.148"
PASS="4dvlab123"
REMOTE_DIR="/home/tangzh/delivery"

# Source paths to sync (relative to project root)
SYNC_PATHS=(
    "src/"
    "configs/"
    "scripts/"
    "tests/"
    "pyproject.toml"
    ".gitignore"
)

# Exclude patterns (rsync-style)
EXCLUDES=(
    "node_modules"
    ".git"
    "__pycache__"
    "*.pyc"
    ".pytest_cache"
    ".mypy_cache"
    ".eggs"
    "*.egg-info"
    ".tox"
    ".coverage"
    "coverage/"
    "htmlcov/"
    ".vscode"
    ".idea"
    "wandb/"
    "runs/"
    ".DS_Store"
    "checkpoints/"
    "outputs/"
)

DRY_RUN=""

# Parse --dry-run flag
for arg in "$@"; do
    if [ "$arg" = "--dry-run" ]; then
        DRY_RUN="--dry-run"
        echo "=== DRY RUN MODE ==="
    fi
done

# Detect project root (where this script lives relative to project)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Syncing project code to $SERVER:$REMOTE_DIR ==="
echo "Project root: $PROJECT_ROOT"
echo ""

# Build exclude flags
RSYNC_EXCLUDES=()
for pattern in "${EXCLUDES[@]}"; do
    RSYNC_EXCLUDES+=(--exclude="$pattern")
done

# Try rsync first
if command -v rsync &>/dev/null; then
    echo "Using rsync..."
    sshpass -p "$PASS" rsync -avz --progress \
        "${RSYNC_EXCLUDES[@]}" \
        ${DRY_RUN:+"$DRY_RUN"} \
        -e "ssh -o StrictHostKeyChecking=no" \
        "${SYNC_PATHS[@]}" \
        "$SERVER:$REMOTE_DIR/"
else
    echo "rsync not found, falling back to scp..."
    # For scp, we do a recursive copy per path (less efficient)
    for path in "${SYNC_PATHS[@]}"; do
        if [ -e "$PROJECT_ROOT/$path" ]; then
            echo "  Sending $path..."
            if [ -z "$DRY_RUN" ]; then
                if [ -d "$PROJECT_ROOT/$path" ]; then
                    sshpass -p "$PASS" scp -r \
                        -o StrictHostKeyChecking=no \
                        "$PROJECT_ROOT/$path" \
                        "$SERVER:$REMOTE_DIR/${path%/}/"
                else
                    # Create remote dir and copy file
                    remote_parent="$REMOTE_DIR/$(dirname "$path")"
                    sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no "$SERVER" "mkdir -p '$remote_parent'"
                    sshpass -p "$PASS" scp \
                        -o StrictHostKeyChecking=no \
                        "$PROJECT_ROOT/$path" \
                        "$SERVER:$REMOTE_DIR/$path"
                fi
            else
                echo "    (scp would copy $PROJECT_ROOT/$path)"
            fi
        fi
    done
fi

echo ""
echo "=== Sync complete ==="
