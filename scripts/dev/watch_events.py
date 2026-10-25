"""Short human sentences for Codex events, adapted from the gta6 watcher."""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sandbox as s

MARKERS = {
    "msg": "<>",
    "think": "..",
    "cmd": " $",
    "ok": "  ",
    "fail": "!!",
    "file": " +",
    "tool": " .",
    "notice": "**",
    "gate": " #",
}


@dataclass(frozen=True)
class Line:
    """One rendered line: when, from where, what kind of thing happened."""

    ts: str
    text: str
    kind: str = "notice"
    source: str = ""

    def render(self, with_channel: bool = False, width: int | None = None) -> str:
        chan = f"#{self.source:<8} " if with_channel and self.source else ""
        body = f"{self.ts} {chan}{MARKERS.get(self.kind, '  ')} {self.text}"
        return body if width is None else body[:width]


def now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def rec_time(rec: dict[str, Any]) -> str:
    """Wall-clock stamp for a record.

    Coordination and landing records carry Unix seconds; Codex records usually
    carry nothing, so those get read time (approximate on replay).
    """
    raw = rec.get("timestamp") or rec.get("ts") or rec.get("time")
    if isinstance(raw, (int, float)):
        with contextlib.suppress(ValueError, OverflowError, OSError):
            return datetime.fromtimestamp(raw).astimezone().strftime("%H:%M:%S")
    if isinstance(raw, str):
        with contextlib.suppress(ValueError):
            return (
                datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
            )
    return now_hms()


def oneline(text: Any, limit: int = 300) -> str:
    flat = " ".join("".join(c for c in str(text or "") if c.isprintable() or c.isspace()).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def short_path(path: Any) -> str:
    """Absolute worker paths all start with the same 40 useless characters."""
    text = str(path or "")
    if "/wt/" in text:
        tail = text.split("/wt/", 1)[1]
        return tail.split("/repo/", 1)[-1]
    prefix = str(s.REPO) + "/"
    return text[len(prefix) :] if text.startswith(prefix) else text


def strip_shell(command: str) -> str:
    """`/bin/bash -lc 'git status'` is the wrapper, not the command."""
    text = re.sub(r"^/bin/(?:ba|z)?sh\s+-l?c\s+", "", command.strip())
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    return text


# ---------------------------------------------------------------- formatting


def _codex_command(item: dict[str, Any], started: bool) -> Line | None:
    cmd = strip_shell(str(item.get("command", "")))
    if started:
        return Line(now_hms(), f"{oneline(cmd)}", "cmd")
    code = item.get("exit_code")
    tail = (str(item.get("aggregated_output") or "").strip().splitlines() or [""])[-1]
    out = oneline(tail, 120)
    if code in (0, None):
        return Line(now_hms(), f"ok   {oneline(cmd, 60)}  {out}".rstrip(), "ok")
    return Line(now_hms(), f"rc={code}  {oneline(cmd, 80)}  {out}".rstrip(), "fail")


def _codex_file_change(item: dict[str, Any]) -> Line | None:
    changes = item.get("changes") or []
    parts = [
        f"{c.get('kind', '?')} {short_path(c.get('path'))}" for c in changes if isinstance(c, dict)
    ]
    return Line(now_hms(), oneline(", ".join(parts)), "file") if parts else None


def format_codex(rec: dict[str, Any]) -> Line | None:
    """One line for a `codex exec --json` record, or None to drop it."""
    kind = rec.get("type", "")
    if kind in ("item.started", "item.completed"):
        item = rec.get("item") or {}
        if not isinstance(item, dict):
            return None
        itype = item.get("type")
        started = kind == "item.started"
        if itype == "command_execution":
            return _codex_command(item, started)
        if itype == "file_change":
            return _codex_file_change(item) if not started else None
        if itype == "reasoning" and not started:
            text = oneline(item.get("text"), 160).strip("*")
            return Line(now_hms(), text, "think") if text else None
        if itype == "agent_message" and not started:
            text = oneline(item.get("text"), 600)
            return Line(now_hms(), text, "msg") if text else None
        if itype == "mcp_tool_call":
            label = f"{item.get('server', '?')}.{item.get('tool', '?')}"
            if started:
                return Line(now_hms(), f"{label} {oneline(item.get('arguments'), 100)}", "tool")
            if item.get("error"):
                return Line(now_hms(), f"{label}: {oneline(item.get('error'), 200)}", "fail")
            return None
        return None
    if kind == "turn.completed":
        usage = rec.get("usage") or {}
        return Line(
            now_hms(),
            f"turn complete - in {usage.get('input_tokens', '?')} / out {usage.get('output_tokens', '?')} tok",
            "notice",
        )
    if kind == "turn.failed":
        err = rec.get("error") or {}
        detail = err.get("message") if isinstance(err, dict) else err
        return Line(now_hms(), f"turn failed: {oneline(detail or rec, 300)}", "fail")
    if kind == "error":
        return Line(now_hms(), f"error: {oneline(rec.get('message') or rec, 300)}", "fail")
    if kind == "thread.started":
        return Line(now_hms(), f"codex thread {rec.get('thread_id', '?')}", "notice")
    return None


def parse_json_line(raw: str) -> dict | None:
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def format_record(rec: dict) -> Line | None:
    line = format_codex(rec)
    return Line(rec_time(rec), line.text, line.kind) if line else None


def format_gate(raw: str) -> Line | None:
    text = oneline(raw)
    if re.match(r"^(PASS|FAIL|TOTAL|GATE:|---)\b", text):
        bad = bool(re.search(r"\b(FAIL|RED)\b", text))
        return Line(now_hms(), f"gate: {text}", "fail" if bad else "gate")
    return None


def format_message(rec: dict) -> Line | None:
    kind = rec.get("kind")
    if kind not in ("note", "request", "interface", "warning", "ack"):
        return None
    action = "acknowledged" if kind == "ack" else kind
    text = f"{rec.get('from', '?')} -> {rec.get('to', '?')} {action}: {rec.get('subject', '')}"
    if rec.get("body"):
        text += f" - {rec['body']}"
    return Line(rec_time(rec), oneline(text, 600), "fail" if kind == "warning" else "notice")
