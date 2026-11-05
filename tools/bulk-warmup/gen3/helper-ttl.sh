#!/bin/bash
# Bulk host as a build helper: power off 55 minutes after the last activity (Cloud Native plans bill CPU only while on).
#   helper-ttl.sh extend     deadline = max(deadline, now + TTL_MIN); helper builds call it when they start and while
#                            they run (/run/helper is owned by wnt), so a busy helper stays on and an idle one goes off by itself
#   helper-ttl.sh check      (helper-ttl.timer, every minute) extend while a build holds a slot; power off past the
#                            deadline. The deadline lives in /run: a boot starts a fresh one (boot + TTL_MIN)
#   helper-ttl.sh status     the deadline as JSON (osgallery-dev's helper-guard.py reads it)
# On 32 or more CPUs the window's own in-server TTL (bulk-ttl, boot + 55 min, never extended) applies as well.
set -u
TTL_MIN=${TTL_MIN:-55}
D=/run/helper/deadline
SLOTS=/run/helper/slots
now=$(date +%s)
deadline() { cat "$D" 2>/dev/null || echo 0; }
extend() {
  local want=$(( now + TTL_MIN * 60 ))
  [ "$(deadline)" -ge "$want" ] || { echo "$want" > "$D.tmp" && mv "$D.tmp" "$D"; }
}
busy() { mkdir -p "$SLOTS"; for f in "$SLOTS"/*; do [ -e "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null && return 0; done; return 1; }
case "${1:-}" in
  extend) extend ;;
  check)
    [ -e "$D" ] || extend
    if busy; then extend
    elif [ "$now" -gt "$(deadline)" ]; then
      logger -t helper-ttl "idle past deadline $(deadline): poweroff"; systemctl poweroff
    fi ;;
  status) printf '{"deadline":%d,"now":%d,"busy":%s}\n' "$(deadline)" "$now" "$(busy && echo true || echo false)" ;;
  *) sed -n 2,8p "$0"; exit 2 ;;
esac
