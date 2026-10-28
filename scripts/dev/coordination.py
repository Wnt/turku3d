"""Append-only task inboxes, a shared board and turn timing, serialized with the runner's locks."""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from pathlib import Path

import sandbox as s

KINDS = ("note", "request", "interface", "warning")
# Written by the lander only (broadcast); informational, never acknowledged or holding anything.
LANDED = "landed"
# Paths a `landed` message lists before summarising the rest.
LANDED_PATHS = 20
RESUME_REASONS = ("unblock", "review", "main-moved", "quota", "follow-up", "other")
PROMPT = """## Coordination
Use codex-task agents for the one active-agent/branch register and git for details.
Commit often. Give a short high-level shared-change heads-up when needed. Finish
rebases, runs your selected affected tests within 30 seconds, merges into main and
deploys Pages immediately; five-second curl smoke and your selective ten-minute
async run follow. No deployment queue or rollback. See AGENTS.md for exact commands.
Runner hooks announce new inbox messages. Handle requests/warnings and acknowledge
them with scripts/dev/codex-task ack <id>. Check scripts/dev/codex-task board
after commits and before finishing. Run long commands through
scripts/dev/codex-task wait-cmd -- <command> or poll at 30-second intervals. A merge notice means rebase before selected checks.
"""


def actor():
    return name_ok(os.environ.get("TURKU3D_SANDBOX") or "orchestrator")


def name_ok(name):
    # main is a valid sender/mailbox even though it cannot be a worker sandbox.
    if name != "main":
        s.name_ok(name)
    if name == "all":
        raise ValueError("all is reserved for the shared board")
    return name


def board_path():
    # An overridden tasks root also isolates the board unless explicitly overridden.
    return (
        Path(os.environ.get("TURKU3D_COORD_ROOT", s.tasks_root().parent / "coord"))
        .expanduser()
        .resolve()
        / "board.jsonl"
    )


def inbox_path(name):
    return s.tasks_root() / name_ok(name) / "inbox.jsonl"


def log_lock(path):
    return s.lock(path.with_suffix(".lock"))


def records(path):
    """Caller holds log_lock; malformed/truncated logs fail closed, including at land."""
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("kind") not in (*KINDS, LANDED, "ack"):
                    raise ValueError("invalid record kind")
                if not line.endswith("\n"):
                    raise ValueError("incomplete record")
                rows.append(row)
            except ValueError as exc:
                raise ValueError(f"invalid coordination log {path}:{number}: {exc}") from exc
    return rows


def append(path, row):
    """Caller holds log_lock across read/append; readers never see a partial write."""
    data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    with path.open("ab", buffering=0) as stream:
        view = memoryview(data)
        while view:
            written = stream.write(view)
            if not written:
                raise OSError(f"short write to {path}")
            view = view[written:]
        os.fsync(stream.fileno())


def entry(sender, recipient, kind, subject, body, refs=(), ack=None):
    return {
        "id": uuid.uuid4().hex,
        "time": time.time(),
        "from": sender,
        "to": recipient,
        "kind": kind,
        "subject": subject,
        "body": body,
        "refs": list(refs),
        "ack": ack,
    }


def post(sender, recipient, kind, subject, body, refs=()):
    """Append one message to a task inbox or the board; the stored row."""
    path = board_path() if recipient == "all" else inbox_path(recipient)
    if recipient not in ("all", "main", "orchestrator") and not (
        (path.parent / "meta.json").exists() or (s.root() / recipient / "sandbox.env").exists()
    ):
        raise ValueError(f"no task or sandbox: {recipient}")
    row = entry(sender, recipient, kind, subject, body, refs)
    with log_lock(path):
        records(path)
        append(path, row)
    return row


def send(recipient, kind, subject, body, refs=()):
    sender = actor()
    row = post(sender, recipient, kind, subject, body, refs)
    if recipient not in ("all", "main", "orchestrator") and kind in ("request", "warning"):
        meta = s.read_json(s.tasks_root() / recipient / "meta.json", {})
        from task_runner import running

        finished = bool(meta) and (
            meta.get("state") not in ("starting", "running") or not running(meta)
        )
        if finished:
            notice = f"recipient finished; addressed by orchestrator. Original message: {row['id']}"
            for target in dict.fromkeys(("orchestrator", sender)):
                notice_path = inbox_path(target)
                with log_lock(notice_path):
                    records(notice_path)
                    append(
                        notice_path,
                        entry("orchestrator", target, "note", subject, notice, (row["id"],)),
                    )
    print(row["id"])


def messages(rows, reader):
    """Resolve acknowledgements without changing the persisted message records."""
    acks = {}
    for row in rows:
        if row["kind"] == "ack" and (row["to"] != "all" or row["from"] == reader):
            acks[row["ack"]] = row
    return [{**row, "ack": acks.get(row["id"])} for row in rows if row["kind"] != "ack"]


def inbox(name, unread=False):
    """A task's messages; with `unread`, those not acknowledged. A `landed` message also counts
    as read once the task's checkout contains its commit (the task rebased past it)."""
    path = inbox_path(name)
    with log_lock(path):
        rows = messages(records(path), name)
    if not unread:
        return rows
    rows = [row for row in rows if row["ack"] is None]
    if any(row["kind"] == LANDED for row in rows):
        pending = not_rebased(name)
        rows = [row for row in rows if row["kind"] != LANDED or row["refs"][0] in pending]
    return rows


def not_rebased(name):
    """Main commits the task's checkout does not contain yet; empty without a sandbox."""
    repo = s.root() / name / "repo"
    if not (repo / ".git").exists():
        return set()
    return set(s.git("rev-list", "HEAD..main", cwd=repo, check=False).splitlines())


def active_tasks():
    """The one active-agent register; no separate overlap/ownership database."""
    import active_agents

    return [row["name"] for row in active_agents.refresh()]


def broadcast_landed(name, row, subject, since, note=None):
    """Historical wire kind, with a high-level merge notice; details stay in git."""
    recipients = [task for task in active_tasks() if task != name]
    body = f"{name} merged {row['commit']} into main: {subject}. Rebase before your selected checks; details are in git."
    if note:
        body += "\n" + note
    for recipient in recipients:
        post(
            name,
            recipient,
            LANDED,
            f"merged {name} {row['commit'][:12]}: {subject}",
            body,
            [row["commit"]],
        )
    return recipients


def open_requests(name):
    """Unacknowledged requests and warnings in a task's inbox (reported when it lands)."""
    return [row for row in inbox(name, unread=True) if row["kind"] in ("request", "warning")]


def board(since=0):
    if not math.isfinite(since) or since < 0:
        raise ValueError("--since must be a non-negative Unix timestamp in seconds")
    with log_lock(board_path()):
        rows = messages(records(board_path()), actor())
    return [row for row in rows if row["time"] > since]


def acknowledge(message_id, note=""):
    sender = actor()
    paths = [inbox_path(sender), board_path()]
    if sender in ("orchestrator", "main"):
        paths += sorted(s.tasks_root().glob("*/inbox.jsonl"))
    for path in dict.fromkeys(paths):
        with log_lock(path):
            rows = messages(records(path), sender)
            message = next((row for row in rows if row["id"] == message_id), None)
            if message is None:
                continue
            if message["ack"] is not None:
                print(f"{message_id}: already acknowledged")
                return
            append(
                path,
                entry(sender, message["to"], "ack", message["subject"], note, ack=message_id),
            )
            print(f"{message_id}: acknowledged")
            return
    raise ValueError(f"no message {message_id} in your inbox or board")


def display(rows, as_json=False):
    if as_json:
        print(json.dumps(rows, ensure_ascii=False))
    elif not rows:
        print("No messages.")
    else:
        for row in rows:
            # A landed message needs no ack; `inbox --unread` lists it until the task rebases.
            state = "" if row["kind"] == LANDED else ", acknowledged" if row["ack"] else ", unread"
            print(
                f"{row['id']} {row['time']:.6f} {row['from']} -> {row['to']} "
                f"[{row['kind']}{state}] {row['subject']}\n{row['body']}"
            )
            if row["refs"]:
                print("refs: " + ", ".join(row["refs"]))
            if row["ack"]:
                ack = row["ack"]
                print(f"ack by {ack['from']}: {ack['body']}")


def turn_ended(path, meta):
    """Unix time the last turn completed; unstamped legacy events fall back to the runner exit."""
    from task_runner import read_events

    ends = [
        e["time"] for e in read_events(path) if e.get("type") == "turn.completed" and "time" in e
    ]
    return ends[-1] if ends else meta.get("finished")


def record_resume(path, meta, reason):
    """Attribute the time between turns: why a stopped task was resumed and after how long."""
    if reason not in RESUME_REASONS:
        raise ValueError(f"unknown resume reason: {reason}")
    ended = turn_ended(path, meta)
    now = time.time()
    gap = round(now - ended, 3) if ended else None
    row = {"type": "task.resumed", "reason": reason, "gap_seconds": gap, "time": now}
    with (path / "events.jsonl").open("a") as events:
        events.write(json.dumps(row) + "\n")


def idle_minutes(path, meta):
    """Whole minutes since a stopped task's last turn ended; None while it runs or never ran."""
    from task_runner import running

    if meta.get("state") in ("starting", "running") and running(meta):
        return None
    ended = turn_ended(path, meta)
    return int((time.time() - ended) // 60) if ended else None


def idle_tasks():
    """Stopped, unlanded tasks whose sandbox still exists: work waiting on the orchestrator."""
    rows = []
    for meta_path in sorted(s.tasks_root().glob("*/meta.json")):
        meta = s.read_json(meta_path)
        name = meta_path.parent.name
        if meta.get("landed") or not (s.root() / name / "sandbox.env").exists():
            continue
        if (idle := idle_minutes(meta_path.parent, meta)) is not None:
            rows.append(f"{name}: {meta.get('state')}, idle {idle} min")
    return rows


def add_commands(subs):
    p = subs.add_parser("msg", help="append to a task inbox or the shared board")
    p.add_argument("name")
    p.add_argument("--kind", required=True, choices=KINDS)
    p.add_argument("--subject", required=True)
    p.add_argument("--ref", action="append", default=[])
    p.add_argument("body")
    p = subs.add_parser("inbox", help="read a task inbox (defaults to this sandbox)")
    p.add_argument("name", nargs="?")
    p.add_argument("--unread", action="store_true")
    p.add_argument("--json", action="store_true")
    p = subs.add_parser("ack", help="acknowledge an inbox or board message")
    p.add_argument("id")
    p.add_argument("--note", default="")
    p = subs.add_parser("board", help="read the shared board")
    p.add_argument("--since", type=float, default=0, metavar="N")
    p.add_argument("--json", action="store_true")


def dispatch(args):
    if args.cmd == "msg":
        send(args.name, args.kind, args.subject, args.body, args.ref)
    elif args.cmd == "inbox":
        display(inbox(args.name or actor(), args.unread), args.json)
    elif args.cmd == "ack":
        acknowledge(args.id, args.note)
    elif args.cmd == "board":
        display(board(args.since), args.json)
        if not args.json and (idle := idle_tasks()):
            print("Idle tasks (minutes since the last turn ended):\n  " + "\n  ".join(idle))
    else:
        return False
    return True
