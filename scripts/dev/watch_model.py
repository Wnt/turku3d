"""Read-only file following and terminal-independent task monitor."""

from __future__ import annotations

import contextlib
import os
import subprocess
import textwrap
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import coordination as coord
import host_slots
import sandbox as s
import task_runner
from watch_events import (
    Line,
    format_gate,
    format_message,
    format_record,
    now_hms,
    oneline,
    parse_json_line,
    rec_time,
)

SCROLLBACK = 4000
TIMEBOX = 30 * 60


class Tail:
    """Follow complete UTF-8 lines by byte offset; retain torn trailing bytes."""

    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.buf = b""
        self.identity = None
        self.generation = 0
        self.primed = False

    def read(self):
        try:
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if self.identity != identity or stat.st_size < self.offset:
                    self.offset, self.buf = 0, b""
                    self.generation += 1
                self.identity = identity
                stream.seek(self.offset)
                data = self.buf + stream.read()
                self.offset = stream.tell()
        except OSError:
            return []
        parts = data.split(b"\n")
        self.buf = parts.pop()
        self.primed = True
        return [part.decode("utf-8", "replace") for part in parts]


def token_count(usage):
    if not isinstance(usage, dict):
        return 0
    # Cached input and reasoning output are subsets, not extra billable tokens.
    return sum(
        max(0, n)
        for key in ("input_tokens", "output_tokens")
        if isinstance(n := usage.get(key), int)
    )


@dataclass
class Channel:
    name: str
    path: Path | None = None
    role: str = "task"
    lines: deque = field(default_factory=lambda: deque(maxlen=SCROLLBACK))
    state: dict = field(default_factory=dict)
    live: bool = False
    unseen: bool = False
    scroll: int = 0
    unread: int = 0
    ahead: str = "?"
    tokens: int = 0
    token_source: str = "events"
    tokens_unknown: bool = False
    show_last: bool = False
    last: list = field(default_factory=list)
    last_stamp: tuple | None = None
    git_checked: float = -float("inf")

    @property
    def phase(self):
        if self.state.get("landed"):
            return "landed"
        state = self.state.get("state", "inbox only")
        if kind := host_slots.waiting(self.name):
            return f"waiting for {kind} slot"
        return "interrupted" if state in ("starting", "running") and not self.live else state

    def seconds(self, now=None):
        started = self.state.get("started")
        if not isinstance(started, (float, int)):
            return None
        end = self.state.get("finished") or (time.time() if now is None else now)
        return max(0, int(end - started))

    def overtime(self, now=None):
        return (
            self.live
            and self.phase in ("starting", "running")
            and (self.seconds(now) or 0) > TIMEBOX
        )

    def title(self, now=None):
        if self.role != "task":
            return f"{self.name} | unread={self.unread}"
        seconds = self.seconds(now)
        elapsed = "--" if seconds is None else f"{seconds // 60}m{seconds % 60:02d}s"
        warning = " | OVER 30m" if self.overtime(now) else ""
        unknown = " + unknown" if self.tokens_unknown else ""
        return (
            f"{self.name} | {self.phase} | {elapsed}{warning} | +{self.ahead} commits"
            f" | unread={self.unread} | {self.tokens:,}{unknown} tok ({self.token_source})"
        )


class Monitor:
    """No locks, writes, agent signals or terminal dependencies."""

    def __init__(
        self, root=None, tasks=(), replay=8, *, board=None, landed=None, ledger=None, reader=None
    ):
        self.root = Path(root) if root is not None else s.tasks_root()
        self.wanted = set(tasks)
        self.replay = replay  # None replays all existing records.
        self.board = Path(board) if board is not None else coord.board_path()
        self.landed = Path(landed) if landed is not None else s.root() / ".registry/landed.jsonl"
        self.ledger = (
            Path(ledger)
            if ledger is not None
            else Path(
                os.environ.get("TURKU3D_CODEX_LEDGER", "~/turku3d/ledger/codex-calls.jsonl")
            ).expanduser()
        )
        self.reader = reader or os.environ.get("TURKU3D_SANDBOX", "orchestrator")
        self.channels = [Channel("all", role="all")]
        self.by_name = {"all": self.channels[0]}
        self.tails = {}
        self.file_tokens = {}
        self.event_keys = set()
        self.messages = {}
        self.ledger_rows = {}
        self.ledger_voids = set()
        self.active = 0
        self.height = 24

    def join(self, name, path=None, role="task"):
        if name not in self.by_name:
            channel = Channel(name, path=path, role=role)
            self.channels.append(channel)
            self.by_name[name] = channel
        return self.by_name[name]

    def emit(self, channel, line):
        line = Line(line.ts, line.text, line.kind, channel.name)
        for target in (self.channels[0], channel):
            if target.scroll and not target.show_last:
                target.scroll += len(self.wrap(line, target, self.width))
            target.lines.append(line)
            if target is not self.channels[self.active]:
                target.unseen = True
        return line

    width = 100

    def read(self, path):
        """Transfer offsets on rename so archived event files are not counted twice."""
        path = Path(path)
        tail = self.tails.get(path)
        try:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
        except OSError:
            return None, [], False
        if tail is None or tail.identity not in (None, identity):
            tail = next((t for t in self.tails.values() if t.identity == identity), None)
            tail = tail or Tail(path)
            tail.path = path
            self.tails[path] = tail
        first = not tail.primed
        return tail, tail.read(), first

    def refresh(self, channel):
        old = (channel.phase, channel.live)
        with contextlib.suppress(OSError, ValueError):
            state = s.read_json(channel.path / "meta.json", {})
            if isinstance(state, dict):
                channel.state = state
        channel.live = bool(channel.state.get("identity")) and task_runner.running(channel.state)
        now = time.monotonic()
        if now - channel.git_checked >= 5:
            channel.git_checked = now
            channel.ahead = "?"
            repo = channel.state.get("repo")
            if repo and Path(repo).is_dir():
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    result = s.run(
                        "git",
                        "--no-optional-locks",
                        "rev-list",
                        "--count",
                        "main..HEAD",
                        cwd=repo,
                        check=False,
                        timeout=1,
                    )
                    if result.returncode == 0:
                        channel.ahead = result.stdout.strip()
        if old != (channel.phase, channel.live):
            return Line(now_hms(), f"{channel.name} is {channel.phase}", "notice")
        return None

    def poll_file(self, channel, path, kind):
        tail, raw_lines, first = self.read(path)
        if tail is None:
            return []
        key = (tail.identity, tail.generation)
        lines = []
        if kind == "events":
            self.event_keys.add(key)
            self.file_tokens.setdefault(key, [channel.name, 0, False])
        if kind == "messages":
            rows = self.messages.setdefault(path, {})
            if tail.generation != rows.get("generation"):
                rows.clear()
                rows.update(generation=tail.generation, records=[])
        for raw in raw_lines:
            rec = parse_json_line(raw)
            line = None
            if kind == "events" and rec:
                if rec.get("type") == "turn.completed":
                    self.file_tokens[key][1] += token_count(rec.get("usage"))
                    usage = rec.get("usage") or {}
                    self.file_tokens[key][2] |= any(
                        not isinstance(usage.get(field), int)
                        for field in ("input_tokens", "output_tokens")
                    )
                line = format_record(rec)
            elif kind == "messages" and rec and rec.get("id") and rec.get("kind"):
                self.messages[path]["records"].append(rec)
                line = format_message(rec)
            elif kind == "landed" and rec:
                line = Line(
                    rec_time(rec),
                    f"{rec.get('name', '?')} landed "
                    f"{str(rec.get('commit', '?'))[:12]} "
                    f"(gate {rec.get('gate_seconds', '?')}s)",
                    "notice",
                )
            elif kind == "gate":
                line = format_gate(raw)
            elif kind == "runner" and raw.strip():
                line = Line(now_hms(), f"runner: {oneline(raw)}", "notice")
            if line:
                lines.append(line)
        if first and self.replay is not None:
            lines = lines[-self.replay :] if self.replay else []
        return [self.emit(channel, line) for line in lines]

    def poll(self):
        produced = []
        self.event_keys.clear()
        paths = sorted(
            {
                p.parent
                for pattern in ("*/meta.json", "*/inbox.jsonl")
                for p in self.root.glob(pattern)
            }
        )
        for path in paths:
            if self.wanted and path.name not in self.wanted:
                continue
            channel = self.join(path.name, path)
            notice = self.refresh(channel)
            if notice:
                produced.append(self.emit(channel, notice))
            for event in sorted(path.glob("events*.jsonl")):
                produced += self.poll_file(channel, event, "events")
            for filename, kind in (("gate.log", "gate"), ("runner.log", "runner")):
                produced += self.poll_file(channel, path / filename, kind)
            inbox = path / "inbox.jsonl"
            if inbox.exists():
                messages = self.join(f"inbox:{path.name}", inbox, "inbox")
                produced += self.poll_file(messages, inbox, "messages")
                rows = self.messages.get(inbox, {}).get("records", [])
                messages.unread = sum(row["ack"] is None for row in coord.messages(rows, path.name))
                channel.unread = messages.unread
        board = self.join("board", self.board, "board")
        produced += self.poll_file(board, self.board, "messages")
        rows = self.messages.get(self.board, {}).get("records", [])
        board.unread = sum(row["ack"] is None for row in coord.messages(rows, self.reader))
        produced += self.poll_file(
            self.join("landings", self.landed, "landings"), self.landed, "landed"
        )
        self.refresh_tokens()
        return produced

    def refresh_tokens(self):
        tail, lines, _ = self.read(self.ledger)
        if tail:
            if getattr(self, "ledger_generation", None) != tail.generation:
                self.ledger_rows.clear()
                self.ledger_voids.clear()
                self.ledger_generation = tail.generation
            for raw in lines:
                row = parse_json_line(raw)
                if row and row.get("record_type") == "void":
                    self.ledger_voids.add(row.get("voids"))
                    continue
                if row and row.get("job_type") == "task" and row.get("call_id"):
                    self.ledger_rows[row["call_id"]] = row
        for channel in self.channels:
            if channel.role != "task":
                continue
            counts = [
                self.file_tokens[key]
                for key in self.event_keys
                if self.file_tokens[key][0] == channel.name
            ]
            events = sum(count[1] for count in counts)
            rows = [
                row
                for call_id, row in self.ledger_rows.items()
                if row.get("task") == channel.name and call_id not in self.ledger_voids
            ]
            ledger = sum(token_count(row.get("tokens")) for row in rows)
            channel.tokens = max(events, ledger)
            channel.token_source = "ledger" if rows and ledger >= events else "events"
            channel.tokens_unknown = any(count[2] for count in counts) or any(
                not row.get("usage_known") for row in rows
            )

    def switch(self, index):
        if 0 <= index < len(self.channels):
            self.active = index
            self.channels[index].unseen = False

    def load_last(self, channel):
        if channel.role != "task":
            return
        path = channel.path / "last.md"
        try:
            stat = path.stat()
            stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if stamp == channel.last_stamp:
                return
            channel.last = [
                Line("", oneline(line, 10000), "msg")
                for line in path.read_text(errors="replace").splitlines()
            ]
            channel.last_stamp = stamp
        except OSError:
            channel.last = [Line("", "last.md is not available yet.")]
            channel.last_stamp = None

    def key(self, key):
        channel = self.channels[self.active]
        if key == "q":
            return False
        if key in ("left", "right"):
            self.switch((self.active + (1 if key == "right" else -1)) % len(self.channels))
        elif key.isdigit():
            self.switch(int(key))
        elif key == "l" and channel.role == "task":
            channel.show_last = not channel.show_last
            channel.scroll = 0
            self.load_last(channel)
        elif key == "end":
            channel.scroll = 0
        elif key in ("pgup", "pgdn"):
            lines = self.rendered(channel, self.width)
            page = max(1, self.height - 2)
            delta = page if key == "pgup" else -page
            channel.scroll = max(0, min(max(0, len(lines) - page), channel.scroll + delta))
        return True

    def wrap(self, line, channel, width):
        text = line.text if channel.show_last else line.render(with_channel=channel.role == "all")
        return [
            Line(line.ts, part, line.kind, line.source)
            for part in (textwrap.wrap(text, max(1, width)) or [""])
        ]

    def rendered(self, channel, width):
        if channel.show_last:
            self.load_last(channel)
        source = channel.last if channel.show_last else channel.lines
        return [part for line in source for part in self.wrap(line, channel, width)]

    def visible(self, height, width):
        self.width = max(1, width)
        channel = self.channels[self.active]
        if channel.show_last:
            self.load_last(channel)
        source = channel.last if channel.show_last else channel.lines
        # The live frame normally needs only its last few sentences, not all
        # 4,000 scrollback entries wrapped again on every keyboard tick.
        chunks = []
        count = 0
        for line in reversed(source):
            chunk = self.wrap(line, channel, self.width)
            chunks.append(chunk)
            count += len(chunk)
            if count >= height + channel.scroll:
                break
        lines = [part for chunk in reversed(chunks) for part in chunk]
        channel.scroll = min(channel.scroll, max(0, len(lines) - height))
        end = max(0, len(lines) - channel.scroll)
        return lines[max(0, end - height) : end]

    def status_line(self):
        return " ".join(
            f"[{i}:{c.name}{'*' if c.unseen else ''}]" for i, c in enumerate(self.channels)
        )

    def title(self):
        channel = self.channels[self.active]
        if self.active:
            return channel.title() + (" | last.md (l: events)" if channel.show_last else "")
        tasks = [c for c in self.channels if c.role == "task"]
        overdue = sum(c.overtime() for c in tasks)
        queue = s.read_json(s.root() / ".registry/merge-queue.json", {"tasks": {}})
        pending = sum(
            not any(key in row for key in ("landed", "returned"))
            for row in queue.get("tasks", {}).values()
        )
        latencies = sorted(
            row["landed"] - row["done"]
            for row in queue.get("tasks", {}).values()
            if row.get("landed") and row.get("done")
        )
        median = f"{latencies[len(latencies) // 2] / 60:.1f}m" if latencies else "-"
        maximum = f"{latencies[-1] / 60:.1f}m" if latencies else "-"
        merge_times = sorted(
            row["merge_wall_seconds"]
            for row in queue.get("tasks", {}).values()
            if row.get("merge_wall_seconds") is not None
        )
        merge_median = f"{merge_times[len(merge_times) // 2]:.1f}s" if merge_times else "-"
        merge_max = f"{merge_times[-1]:.1f}s" if merge_times else "-"
        return (
            f"[all] {len(tasks)} tasks, {sum(c.live for c in tasks)} live"
            f" | queue {pending} ready, done-main {median}/{maximum}"
            f" merge {merge_median}/{merge_max}"
            f" | {overdue} OVER 30m | {sum(c.tokens for c in tasks):,} tok"
            " | arrows 0-9 PgUp/PgDn End l q"
        )
