#!/bin/bash
# Deploy Hugo static site to local NAS (also mounted by evconduit.com)
# NAS is at 192.168.1.235, mounted on both Mac and evconduit.com

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PUBLIC_DIR="$SCRIPT_DIR/output/public"

# NAS mount point (use home dir to avoid needing sudo)
NAS_MOUNT="${NAS_MOUNT:-$HOME/.nas-evconduit}"
NAS_PATH="${NAS_PATH:-$NAS_MOUNT/evconduit-news}"
NAS_USER="${NAS_USER:-}"
NAS_PASS="${NAS_PASS:-}"

# Mount NAS if not already mounted
if [ ! -d "$NAS_MOUNT/evconduit-news" ]; then
    echo "Mounting NAS..."
    mkdir -p "$NAS_MOUNT"
    if [ -n "$NAS_USER" ] && [ -n "$NAS_PASS" ]; then
        mount_smbfs "//${NAS_USER}:${NAS_PASS}@192.168.1.235/evconduit" "$NAS_MOUNT" 2>/dev/null || {
            echo "Mount failed. Check credentials or mount manually."
            exit 1
        }
    else
        mount_smbfs //192.168.1.235/evconduit "$NAS_MOUNT" 2>/dev/null || {
            echo "Mount failed. Set NAS_USER and NAS_PASS, or mount manually."
            exit 1
        }
    fi
fi

# Build Hugo site if needed
if [ ! -f "$PUBLIC_DIR/index.html" ]; then
    echo "Building Hugo site..."
    cd "$SCRIPT_DIR/output"
    hugo --minify
    cd "$SCRIPT_DIR"
fi

# Create target directory on NAS
mkdir -p "$NAS_PATH"

# Sync to NAS
echo "Syncing $PUBLIC_DIR/ → $NAS_PATH/"
rsync -av --delete "$PUBLIC_DIR/" "$NAS_PATH/"

# Cleanup old images (keep 90 days)
find "$SCRIPT_DIR/output/static/images" -type f -mtime +90 -delete 2>/dev/null || true

FILE_COUNT=$(find "$NAS_PATH" -type f | wc -l | tr -d ' ')
SIZE=$(du -sh "$NAS_PATH" | awk '{print $1}')
echo "Deployed $FILE_COUNT files ($SIZE) → evconduit.com"
