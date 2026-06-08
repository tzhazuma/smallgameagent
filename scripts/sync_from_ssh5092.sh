#!/bin/bash
# ============================================================
# sync_from_ssh5092.sh — Pull trained checkpoints/outputs back from ssh50902
# Server: tangzh@10.19.138.149
# ============================================================
set -e

SERVER="tangzh@10.19.138.149"
PASS="4dvlab123"
REMOTE_BASE="/home/tangzh/delivery"
LOCAL_BASE="."  # relative to project root, resolved below

# Directories to pull from server
PULL_DIRS=(
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

# Detect project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Pulling trained artifacts from $SERVER ==="
echo "Local target: $PROJECT_ROOT"
echo ""

# Check rsync
if ! command -v rsync &>/dev/null; then
    echo "WARNING: rsync not found — falling back to scp (slower)."
fi

for dir in "${PULL_DIRS[@]}"; do
    remote_path="$REMOTE_BASE/$dir"
    local_path="$PROJECT_ROOT/$dir"

    # Check if remote directory exists
    if [ -z "$DRY_RUN" ]; then
        dir_exists=$(sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no "$SERVER" \
            "[ -d '$remote_path' ] && echo 'yes' || echo 'no'")
    else
        dir_exists="yes"  # assume exists for dry-run
        echo "  Would check if $remote_path exists on server"
    fi

    if [ "$dir_exists" = "no" ]; then
        echo "  WARNING: $remote_path does not exist on server — skipping"
        continue
    fi

    # Create local directory
    if [ -z "$DRY_RUN" ]; then
        mkdir -p "$local_path"
    fi

    echo "  Syncing $dir ..."

    if command -v rsync &>/dev/null; then
        # rsync: pull from remote to local
        sshpass -p "$PASS" rsync -avz --progress \
            ${DRY_RUN:+"$DRY_RUN"} \
            -e "ssh -o StrictHostKeyChecking=no" \
            "$SERVER:$remote_path" \
            "$local_path"
    else
        # scp fallback
        if [ -z "$DRY_RUN" ]; then
            sshpass -p "$PASS" scp -r \
                -o StrictHostKeyChecking=no \
                "$SERVER:$remote_path" \
                "$local_path"
        else
            echo "    (scp would pull $SERVER:$remote_path → $local_path)"
        fi
    fi
done

echo ""
echo "=== Sync complete ==="
echo "Pulled to: $PROJECT_ROOT"
echo ""
echo "Contents:"
for dir in "${PULL_DIRS[@]}"; do
    if [ -d "$PROJECT_ROOT/$dir" ]; then
        echo "  $dir: $(find "$PROJECT_ROOT/$dir" -type f 2>/dev/null | wc -l) files"
    fi
done
