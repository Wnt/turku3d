#!/bin/bash
# turku3d-bulk in-server TTL: on a large plan (nproc >= MIN_CPUS) power off TTL_MIN minutes after
# boot; Cloud Native plans bill only while powered on. No credentials, no API. On the small plan
# it disarms and does nothing. Arms itself at boot (bulk-ttl.service) and every minute after
# (bulk-ttl.timer, for a hot resize). Writes the deadline to /run/bulk-ttl for bulk.py.
set -u
CONF=/etc/bulk-ttl.conf
# Test override in /run: gone after the next boot, so a restarted small plan is never re-armed.
OVERRIDE=/run/bulk-ttl.override
TTL_MIN=55 FORCE_MIN=57 ADMIT_MIN=52 MIN_CPUS=32 FROM=boot
[ -r "$CONF" ] && . "$CONF"
[ -r "$OVERRIDE" ] && . "$OVERRIDE"
cpus=$(grep -c ^processor /proc/cpuinfo)   # online CPUs; `nproc --all` counts 128 hot-plug slots
if [ "$cpus" -lt "$MIN_CPUS" ]; then
  if [ -e /run/bulk-ttl ]; then
    systemctl stop bulk-ttl-poweroff.timer bulk-ttl-force.timer 2>/dev/null
    rm -f /run/bulk-ttl
  fi
  echo "bulk-ttl: $cpus CPUs < $MIN_CPUS: small plan, nothing armed"; exit 0
fi
[ -e /run/bulk-ttl ] && { echo "bulk-ttl: already armed: $(cat /run/bulk-ttl)"; exit 0; }
now=$(date +%s); uptime_s=$(cut -d. -f1 /proc/uptime)
# A plan seen long after boot (hot resize) counts from now, never from a boot hours ago.
if [ "$FROM" = now ] || [ "$uptime_s" -gt 600 ]; then base=$now; else base=$(( now - uptime_s )); fi
poweroff_at=$(( base + TTL_MIN * 60 )); force_at=$(( base + FORCE_MIN * 60 )); admit_at=$(( base + ADMIT_MIN * 60 ))
after() { local d=$(( $1 - now )); [ "$d" -lt 1 ] && d=1; echo "${d}s"; }
systemctl stop bulk-ttl-poweroff.timer bulk-ttl-force.timer 2>/dev/null
systemd-run --quiet --unit=bulk-ttl-poweroff --on-active="$(after $poweroff_at)" --timer-property=AccuracySec=1s \
  /usr/bin/systemctl poweroff || exit 1
systemd-run --quiet --unit=bulk-ttl-force --on-active="$(after $force_at)" --timer-property=AccuracySec=1s \
  /usr/bin/systemctl poweroff --force || exit 1
printf '{"cpus":%d,"from":"%s","base":%d,"stop_admission_at":%d,"poweroff_at":%d,"force_at":%d,"armed_at":%d}\n' \
  "$cpus" "$FROM" "$base" "$admit_at" "$poweroff_at" "$force_at" "$now" > /run/bulk-ttl.tmp && mv /run/bulk-ttl.tmp /run/bulk-ttl
echo "bulk-ttl: armed $(cat /run/bulk-ttl)"
