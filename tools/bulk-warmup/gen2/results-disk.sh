#!/bin/bash
# turku3d-bulk host: format (first time only) and mount the turku3d-bulk-results storage at
# /srv/results (nofail), then keep the farm's products there. Idempotent.
set -euo pipefail
ROOT=${1:-/root/turku3d-bulk-gen2}; MOUNT=/srv/results
dev=$(lsblk -dpno NAME,SIZE,TYPE | awk '$3=="disk" && $2=="100G" {print $1}' | head -1)
[ -n "$dev" ] || { echo "results disk (100G) not attached"; exit 1; }
blkid "$dev" >/dev/null 2>&1 || mkfs.ext4 -q -L turku3d-results "$dev"
uuid=$(blkid -s UUID -o value "$dev")
grep -q "$uuid" /etc/fstab || echo "UUID=$uuid $MOUNT ext4 defaults,noatime,nofail,x-systemd.device-timeout=30s 0 2" >> /etc/fstab
mkdir -p "$MOUNT"; mountpoint -q "$MOUNT" || mount "$MOUNT"
mkdir -p "$MOUNT/products"
if [ ! -L "$ROOT/farm/products" ]; then
  [ -d "$ROOT/farm/products" ] && cp -a "$ROOT/farm/products/." "$MOUNT/products/" && rm -rf "$ROOT/farm/products"
  ln -s "$MOUNT/products" "$ROOT/farm/products"
fi
df -h "$MOUNT" | tail -1; ls -la "$ROOT/farm/products"
