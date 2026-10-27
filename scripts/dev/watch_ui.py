"""Curses and plain drivers for the read-only Turku3D task monitor."""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import sandbox as s
from watch_model import Monitor

KEYMAP_NAMES = {
    "KEY_LEFT": "left",
    "KEY_RIGHT": "right",
    "KEY_PPAGE": "pgup",
    "KEY_NPAGE": "pgdn",
    "KEY_END": "end",
}


def channel_bar(monitor, width):
    """Keep the selected channel visible even with dozens of workers."""
    text = monitor.status_line()
    marker = f"[{monitor.active}:"
    start = text.index(marker)
    return text[max(0, start - max(0, width // 3)) :][:width]


def run_plain(monitor, interval, once):
    print(f"watch: following {monitor.root} (Ctrl-C to quit)", flush=True)
    previous = {}
    while True:
        for line in monitor.poll():
            print(line.render(with_channel=True), flush=True)
        # Status changes are useful in pipes too. Elapsed ticks alone do not spam them.
        for channel in monitor.channels[1:]:
            status = (
                channel.phase,
                channel.live,
                channel.ahead,
                channel.unread,
                channel.tokens,
                channel.tokens_unknown,
                channel.token_source,
                channel.overtime(),
            )
            if previous.get(channel.name) != status:
                print(f"STATUS {channel.title()}", flush=True)
                previous[channel.name] = status
        if once:
            return 0
        time.sleep(interval)


def run_tui(monitor: Monitor, interval: float) -> int:  # pragma: no cover - needs a terminal
    import curses

    def key_name(key: Any) -> str:
        if isinstance(key, str):
            return "q" if key in ("q", "Q") else key
        return KEYMAP_NAMES.get(curses.keyname(key).decode("ascii", "replace").upper(), "")

    def main(stdscr: Any) -> int:
        stdscr.keypad(True)
        stdscr.timeout(120)
        with contextlib.suppress(curses.error):
            curses.curs_set(0)
        colors = curses.has_colors()
        if colors:
            with contextlib.suppress(curses.error):
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
                for n, c in enumerate(
                    (
                        curses.COLOR_CYAN,
                        curses.COLOR_YELLOW,
                        curses.COLOR_GREEN,
                        curses.COLOR_RED,
                        curses.COLOR_MAGENTA,
                        curses.COLOR_WHITE,
                    ),
                    2,
                ):
                    curses.init_pair(n, c, -1)
        pairs = {
            "msg": 2,
            "cmd": 3,
            "file": 4,
            "fail": 5,
            "notice": 6,
            "gate": 6,
            "ok": 7,
            "tool": 7,
            "think": 7,
        }

        next_poll = 0.0
        while True:
            if time.monotonic() >= next_poll:
                monitor.poll()
                next_poll = time.monotonic() + interval
            height, width = stdscr.getmaxyx()
            monitor.height = height
            stdscr.erase()
            body = max(0, height - 2)
            for row, line in enumerate(monitor.visible(body, width - 1)):
                attr = curses.color_pair(pairs.get(line.kind, 0)) if colors else 0
                if line.kind == "fail":
                    attr |= curses.A_BOLD
                with contextlib.suppress(curses.error):
                    stdscr.addstr(row, 0, line.text[: width - 1], attr)
            channel = monitor.channels[monitor.active]
            overdue = channel.overtime() or (
                monitor.active == 0 and any(c.overtime() for c in monitor.channels)
            )
            bar = curses.color_pair(5 if overdue else 1) if colors else curses.A_REVERSE
            for row, text in (
                (height - 2, monitor.title()),
                (height - 1, channel_bar(monitor, width - 1)),
            ):
                with contextlib.suppress(curses.error):
                    stdscr.addstr(row, 0, " " * (width - 1), bar)
                    stdscr.addstr(row, 0, text[: width - 1], bar | curses.A_BOLD)
            with contextlib.suppress(curses.error):
                stdscr.refresh()
            try:
                key = stdscr.get_wch()
            except curses.error:
                continue
            if key == curses.KEY_RESIZE:
                with contextlib.suppress(curses.error):
                    curses.update_lines_cols()
                continue
            name = key_name(key)
            if name and not monitor.key(name):
                return 0

    return int(curses.wrapper(main) or 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("tasks", nargs="*", help="only these task names")
    parser.add_argument("--plain", action="store_true", help="stream events to stdout")
    parser.add_argument("--once", action="store_true", help="replay existing history and exit")
    parser.add_argument(
        "--replay", type=int, help="startup sentences per file (live default 8; once all)"
    )
    parser.add_argument("--root", type=Path, default=s.tasks_root(), help="task directory root")
    parser.add_argument("--interval", type=float, default=1.0, help="poll seconds (default 1)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval must be positive and finite")
    if args.replay is not None and args.replay < 0:
        parser.error("--replay must be non-negative")
    if not args.root.is_dir():
        print(f"watch: no task root at {args.root}", file=sys.stderr)
        return 2
    replay = args.replay if args.replay is not None else (None if args.once else 8)
    monitor = Monitor(args.root, args.tasks, replay)
    try:
        if args.plain or args.once or not (sys.stdout.isatty() and sys.stdin.isatty()):
            return run_plain(monitor, args.interval, args.once)
        return run_tui(monitor, args.interval)
    except (KeyboardInterrupt, BrokenPipeError):
        # Avoid another BrokenPipeError during interpreter shutdown.
        if not sys.stdout.isatty():
            with contextlib.suppress(OSError):
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
