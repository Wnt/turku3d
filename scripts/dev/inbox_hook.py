"""Fast, fail-open Codex hooks for a runner task's append-only inbox."""

from __future__ import annotations

import fcntl
import json
import sys
import time
from pathlib import Path

REPEAT_SECONDS = 15 * 60
CONTEXT_LIMIT = 4000


def unread(path: Path):
    """Read complete inbox records without waiting on a sender's fsync lock."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_bytes().splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        rows.append(json.loads(line))
    acknowledged = {row["ack"] for row in rows if row["kind"] == "ack"}
    return [row for row in rows if row["kind"] != "ack" and row["id"] not in acknowledged]


def announcements(rows, seen, now, limit=CONTEXT_LIMIT):
    """Return bounded context and the announcement times to persist."""
    parts = []
    updates = {}
    remaining = limit
    for row in rows:
        kind = row["kind"]
        previous = seen.get(row["id"])
        if previous is not None and (
            kind not in ("request", "warning") or now - previous < REPEAT_SECONDS
        ):
            continue
        if kind in ("request", "warning"):
            message = (
                f"Inbox {kind} {row['id']} from {row['from']}: {row['subject']}\n"
                f"{row['body']}\nHandle it and run scripts/dev/codex-task ack {row['id']}."
            )
        elif kind == "landed":
            message = f"Inbox landed {row['id']}: main moved; rebase before the next gate."
        elif kind in ("note", "interface"):
            message = f"Inbox {kind} {row['id']}: {row['subject']}"
        else:
            continue
        if remaining < 100:
            break
        if len(message) > remaining:
            message = message[: remaining - 1] + "…"
        parts.append(message)
        updates[row["id"]] = now
        remaining -= len(message) + 2
    return "\n\n".join(parts), updates


def stop_decision(rows, stop_hook_active):
    if stop_hook_active:
        return {}
    pending = [row for row in rows if row["kind"] in ("request", "warning")]
    if not pending:
        return {}
    labels = ", ".join(f"{row['kind']} {row['id']} ({row['subject']})" for row in pending)
    return {
        "decision": "block",
        "reason": f"Unread inbox messages: {labels}. Handle and acknowledge them before stopping.",
    }


def run(task_dir: Path, event: dict):
    rows = unread(task_dir / "inbox.jsonl")
    if event.get("hook_event_name") == "Stop":
        return stop_decision(rows, event.get("stop_hook_active", False))
    if event.get("hook_event_name") != "PostToolUse":
        return {}
    state_path = task_dir / "inbox-hook-seen.json"
    with (task_dir / "inbox-hook.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        seen = json.loads(state_path.read_text()) if state_path.exists() else {}
        context, updates = announcements(rows, seen, time.time())
        if updates:
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({**seen, **updates}))
            temporary.replace(state_path)
    if context:
        return {
            "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}
        }
    return None


def main():
    task_dir = Path(sys.argv[1])
    try:
        result = run(task_dir, json.load(sys.stdin))
        if result is not None:
            print(json.dumps(result))
    except Exception as exc:
        try:
            with (task_dir / "inbox-hook-errors.log").open("a") as log:
                log.write(f"{time.time()}: {type(exc).__name__}: {exc}\n")
        except OSError:
            pass
        print("{}")


if __name__ == "__main__":
    main()
