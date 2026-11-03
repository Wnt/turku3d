#!/bin/bash
# Install the in-server TTL on turku3d-bulk (run as root on the host; idempotent).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
install -m 0755 "$here/bulk-ttl.sh" /usr/local/sbin/bulk-ttl
install -m 0644 "$here/bulk-ttl.service" "$here/bulk-ttl.timer" /etc/systemd/system/
install -d /etc/systemd/system/poweroff.target.d
install -m 0644 "$here/poweroff-timeout.conf" /etc/systemd/system/poweroff.target.d/bulk-ttl.conf
systemctl daemon-reload
systemctl enable --now bulk-ttl.service bulk-ttl.timer
systemctl is-enabled bulk-ttl.service bulk-ttl.timer
/usr/local/sbin/bulk-ttl
