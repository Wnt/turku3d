"""Append-only task inboxes and a shared board, serialized with the runner's locks."""

from __future__ import annotations

import contextlib
import json
import math
import os
import time
import uuid
from pathlib import Path

import sandbox as s

KINDS = ("note", "request", "interface", "warning")
PROMPT = """## Coordination
After each major step/commit and before finishing, check:
  scripts/dev/codex-task inbox --unread
  scripts/dev/codex-task board
Acknowledge what you act on: scripts/dev/codex-task ack <id> --note "..."
Send a message: scripts/dev/codex-task msg <name|all> --kind request --subject "..." "body"
Announce shared interface changes with --kind interface --ref <commit> to all.
Before changing another area's interface, request it from its running owner or the board.
Never edit another sandbox's files. Open inbox requests/warnings prevent landing.
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
                if not isinstance(row, dict) or row.get("kind") not in (*KINDS, "ack"):
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


def send(recipient, kind, subject, body, refs=()):
    sender = actor()
    path = board_path() if recipient == "all" else inbox_path(recipient)
    if recipient not in ("all", "main", "orchestrator") and not (
        (path.parent / "meta.json").exists() or (s.root() / recipient / "sandbox.env").exists()
    ):
        raise ValueError(f"no task or sandbox: {recipient}")
    row = entry(sender, recipient, kind, subject, body, refs)
    with log_lock(path):
        records(path)
        append(path, row)
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
    path = inbox_path(name)
    with log_lock(path):
        rows = messages(records(path), name)
    return [row for row in rows if not unread or row["ack"] is None]


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
            state = "acknowledged" if row["ack"] else "unread"
            print(
                f"{row['id']} {row['time']:.6f} {row['from']} -> {row['to']} "
                f"[{row['kind']}, {state}] {row['subject']}\n{row['body']}"
            )
            if row["refs"]:
                print("refs: " + ", ".join(row["refs"]))
            if row["ack"]:
                ack = row["ack"]
                print(f"ack by {ack['from']}: {ack['body']}")


@contextlib.contextmanager
def landing_guard(name, force=False):
    """Keep the final check and main update atomic with respect to inbox writers."""
    path = inbox_path(name)
    with log_lock(path):
        pending = [
            row
            for row in messages(records(path), name)
            if row["ack"] is None and row["kind"] in ("request", "warning")
        ]
        if pending and not force:
            ids = ", ".join(row["id"] for row in pending)
            raise ValueError(f"unacknowledged request/warning messages: {ids}; ack or use --force")
        yield


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
    else:
        return False
    return True
