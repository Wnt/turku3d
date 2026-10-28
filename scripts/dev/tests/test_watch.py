"""Synthetic task fixtures exercise the reader without curses or model calls."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import task_runner
import task_view
import watch_events as events
import watch_ui
from watch_model import Monitor, Tail

SERIAL = True
DEV = Path(__file__).resolve().parents[1]


def append(path, *rows):
    with path.open("a") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def message(text):
    return {"type": "item.completed", "item": {"type": "agent_message", "text": text}}


def usage(n=100):
    return {
        "type": "turn.completed",
        "usage": {"input_tokens": n, "cached_input_tokens": 80, "output_tokens": 10},
    }


def note(id="m1", **kwargs):
    return {
        "id": id,
        "kind": "request",
        "from": "sender",
        "to": "alpha",
        "subject": "Check seams",
        "body": "Please verify",
        "time": 1700000000,
        "ack": None,
        **kwargs,
    }


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tasks = self.root / "tasks"
        self.tasks.mkdir()
        self.board = self.root / "board.jsonl"
        self.landings = self.root / "landed.jsonl"
        self.ledger = self.root / "ledger.jsonl"

    def task(self, name="alpha", **meta):
        path = self.tasks / name
        path.mkdir(exist_ok=True)
        (path / "meta.json").write_text(
            json.dumps({"name": name, "state": "done", "started": 100, "finished": 200, **meta})
        )
        return path

    def monitor(self, **kwargs):
        return Monitor(
            self.tasks,
            board=self.board,
            landed=self.landings,
            ledger=self.ledger,
            reader="alpha",
            **kwargs,
        )


class Formatting(unittest.TestCase):
    def test_commands_and_failed_output_tail(self):
        item = {"type": "command_execution", "command": "/bin/zsh -lc 'git status'"}
        start = events.format_record({"type": "item.started", "item": item})
        self.assertEqual((start.kind, start.text), ("cmd", "git status"))
        done = events.format_record(
            {
                "type": "item.completed",
                "item": {**item, "exit_code": 1, "aggregated_output": "first\nboom"},
            }
        )
        self.assertEqual(done.kind, "fail")
        self.assertIn("rc=1", done.text)
        self.assertIn("boom", done.text)

    def test_codex_completed_only_edits_and_unknown_events(self):
        item = {
            "type": "file_change",
            "changes": [
                {"kind": "update", "path": "/Users/wnt/turku3d/wt/alpha/repo/game/src/main.ts"}
            ],
        }
        self.assertIsNone(events.format_record({"type": "item.started", "item": item}))
        self.assertEqual(
            events.format_record({"type": "item.completed", "item": item}).text,
            "update game/src/main.ts",
        )
        self.assertIsNone(events.format_record({"type": "future.event"}))
        self.assertIsNone(events.format_record({"type": "item.completed", "item": []}))
        self.assertIsNone(events.parse_json_line("{torn"))
        self.assertIsNone(events.parse_json_line("[]"))

    def test_messages_tools_usage_errors_and_control_characters(self):
        self.assertEqual(events.format_record(message("hi\x1b[2J\nthere")).text, "hi[2J there")
        self.assertIn("tok", events.format_record(usage()).text)
        self.assertEqual(
            events.format_record({"type": "turn.failed", "error": {"message": "capacity"}}).kind,
            "fail",
        )
        tool = events.format_record(
            {"type": "item.started", "item": {"type": "mcp_tool_call", "server": "s", "tool": "t"}}
        )
        self.assertIn("s.t", tool.text)

    def test_project_gate_and_coordination_sentences(self):
        for text in ("PASS policy 0.2s", "TOTAL GREEN 1.234s"):
            self.assertEqual(events.format_gate(text).kind, "gate")
        self.assertEqual(events.format_gate("TOTAL RED 2s").kind, "fail")
        self.assertIsNone(events.format_gate("npm chatter"))
        self.assertIn("sender -> alpha request: Check seams", events.format_message(note()).text)
        self.assertRegex(events.format_message(note()).ts, r"\d\d:\d\d:\d\d")


class Following(Fixture):
    def test_incremental_bytes_partial_utf8_truncation_and_replacement(self):
        path = self.root / "events"
        tail = Tail(path)
        self.assertEqual(tail.read(), [])
        data = "kääk\n".encode()
        path.write_bytes(data[:3])
        self.assertEqual(tail.read(), [])
        with path.open("ab") as stream:
            stream.write(data[3:])
        self.assertEqual(tail.read(), ["kääk"])
        self.assertEqual(tail.read(), [])
        self.assertEqual(tail.offset, len(data))
        path.write_text("x\n")
        self.assertEqual(tail.read(), ["x"])
        path.rename(path.with_suffix(".old"))
        path.write_text("new\n")
        self.assertEqual(tail.read(), ["new"])

    def test_resume_discovery_replay_and_accounting_independent_of_context(self):
        path = self.task()
        append(path / "events.jsonl", usage(), message("last"))
        mon = self.monitor(replay=1)
        lines = mon.poll()
        self.assertEqual(mon.by_name["alpha"].tokens, 110)
        self.assertIn("last", [line.text for line in lines])
        self.assertFalse(any("turn complete" in line.text for line in lines))
        self.assertEqual(mon.poll(), [])
        append(path / "events-2.jsonl", usage(200), message("resume"))
        self.assertIn("resume", [line.text for line in mon.poll()])
        self.assertEqual(mon.by_name["alpha"].tokens, 320)
        self.task("beta")
        mon.poll()
        self.assertIn("beta", mon.by_name)

    def test_renamed_events_not_replayed_or_counted_twice(self):
        path = self.task()
        append(path / "events.jsonl", usage())
        mon = self.monitor()
        mon.poll()
        (path / "events.jsonl").rename(path / "events-1.jsonl")
        append(path / "events.jsonl", usage(200))
        new = mon.poll()
        self.assertEqual(sum("turn complete" in line.text for line in new), 1)
        self.assertEqual(mon.by_name["alpha"].tokens, 320)
        self.assertEqual(mon.poll(), [])

    def test_truncated_event_totals_reset(self):
        path = self.task()
        append(path / "events.jsonl", message("long padding" * 100), usage())
        mon = self.monitor()
        mon.poll()
        (path / "events.jsonl").write_text(json.dumps(usage(20)) + "\n")
        mon.poll()
        self.assertEqual(mon.by_name["alpha"].tokens, 30)

    def test_partial_and_malformed_events_do_not_break_monitor(self):
        path = self.task()
        event = path / "events.jsonl"
        event.write_text('not json\n[]\n{"type": "item.completed", "item": ')
        mon = self.monitor()
        mon.poll()
        with event.open("a") as stream:
            stream.write('{"type": "agent_message", "text": "later"}}\n')
        self.assertEqual([line.text for line in mon.poll()], ["later"])
        (path / "meta.json").write_text("{")
        mon.poll()
        self.assertEqual(mon.by_name["alpha"].phase, "done")

    def test_coordination_acknowledgements_are_per_reader(self):
        path = self.task()
        append(path / "inbox.jsonl", note())
        append(
            self.board,
            note("b1", to="all"),
            note("a1", kind="ack", to="all", ack="b1", **{"from": "beta"}),
        )
        mon = self.monitor()
        mon.poll()
        self.assertEqual(mon.by_name["alpha"].unread, 1)
        self.assertEqual(mon.by_name["board"].unread, 1)
        append(path / "inbox.jsonl", note("a2", kind="ack", ack="m1"))
        append(self.board, note("a3", kind="ack", to="all", ack="b1", **{"from": "alpha"}))
        mon.poll()
        self.assertEqual(mon.by_name["alpha"].unread, 0)
        self.assertEqual(mon.by_name["board"].unread, 0)

    def test_ledger_dedup_lag_and_unknown_usage(self):
        path = self.task()
        append(path / "events.jsonl", usage())
        row = {
            "call_id": "call",
            "job_type": "task",
            "task": "alpha",
            "tokens": {"input_tokens": 100, "output_tokens": 10},
            "usage_known": True,
        }
        append(self.ledger, row, row, {**row, "call_id": "image", "job_type": "image"})
        mon = self.monitor()
        mon.poll()
        channel = mon.by_name["alpha"]
        self.assertEqual((channel.tokens, channel.token_source), (110, "ledger"))
        append(path / "events.jsonl", usage(200))
        mon.poll()
        self.assertEqual((channel.tokens, channel.token_source), (320, "events"))
        append(self.ledger, {**row, "call_id": "unknown", "tokens": None, "usage_known": False})
        mon.poll()
        self.assertIn("unknown", channel.title())
        append(self.ledger, {"record_type": "void", "voids": "unknown"})
        mon.poll()
        self.assertNotIn("unknown", channel.title())
        append(
            self.ledger,
            {**row, "call_id": "wrong", "tokens": {"input_tokens": 99999, "output_tokens": 10}},
        )
        mon.poll()
        self.assertGreater(channel.tokens, 99999)
        append(self.ledger, {"record_type": "void", "voids": "wrong"})
        mon.poll()
        self.assertEqual(channel.tokens, 320)

    def test_filter_aggregate_landing_gate_runner_and_inbox_only(self):
        path = self.task()
        self.task("beta")
        mailbox = self.tasks / "orchestrator"
        mailbox.mkdir()
        append(mailbox / "inbox.jsonl", note(to="orchestrator"))
        append(path / "events.jsonl", message("hello"))
        (path / "gate.log").write_text("TOTAL GREEN 0.1s\n")
        (path / "runner.log").write_text("runner started\n")
        append(
            self.landings,
            {
                "name": "alpha",
                "commit": "0123456789abcdef",
                "fast_gate_seconds": 0.1,
                "class": "data",
            },
        )
        mon = self.monitor()
        lines = mon.poll()
        self.assertEqual(len(lines), len(mon.channels[0].lines))
        self.assertEqual(len(lines), sum(len(c.lines) for c in mon.channels[1:]))
        self.assertTrue(
            any("landed 0123456789ab as data (gate 0.1s)" in line.text for line in lines)
        )
        self.assertTrue(any("TOTAL GREEN" in line.text for line in lines))
        self.assertIn("inbox:orchestrator", mon.by_name)
        filtered = self.monitor(tasks=["alpha"])
        filtered.poll()
        self.assertNotIn("beta", filtered.by_name)

    def test_task_liveness_timebox_and_elapsed_freezes_on_finish(self):
        path = self.task(
            state="running",
            pid=os.getpid(),
            identity=task_runner.identity(os.getpid()),
            started=1,
            finished=None,
        )
        mon = self.monitor()
        mon.poll()
        chan = mon.by_name["alpha"]
        self.assertTrue(chan.live)
        self.assertTrue(chan.overtime(now=1802))
        self.assertFalse(chan.overtime(now=1801))
        self.assertIn("OVER 30m", chan.title(now=1802))
        state = json.loads((path / "meta.json").read_text())
        state.update(identity="reused pid")
        (path / "meta.json").write_text(json.dumps(state))
        mon.poll()
        self.assertEqual(chan.phase, "interrupted")
        self.assertFalse(chan.overtime(now=99999))
        chan.state.update(state="done", started=100, finished=200)
        self.assertEqual(chan.seconds(now=99999), 100)
        chan.state["landed"] = {"commit": "abc"}
        self.assertEqual(chan.phase, "landed")

    def test_git_ahead_is_real_and_missing_repo_is_tolerated(self):
        repo = self.root / "repo"
        repo.mkdir()

        def git(*args):
            return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

        git("init", "-q", "-b", "main")
        for args in (("user.email", "test@example.invalid"), ("user.name", "Test")):
            git("config", *args)
        git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-qm", "base")
        git("checkout", "-qb", "worker")
        git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-qm", "work")
        self.task(repo=str(repo))
        self.task("beta", repo=str(self.root / "missing"))
        mon = self.monitor(tasks=["alpha", "beta"])
        mon.poll()
        self.assertEqual(mon.by_name["alpha"].ahead, "1")
        self.assertEqual(mon.by_name["beta"].ahead, "?")


class BoundedSnapshots(Fixture):
    def test_initial_tail_is_bounded_and_continues_with_partial_records(self):
        path = self.root / "large.log"
        path.write_bytes(b"old record\n" * 100 + b"recent\npart")
        tail = Tail(path, limit=32)
        self.assertIn("recent", tail.read())
        self.assertTrue(tail.skipped)
        self.assertEqual(tail.offset, path.stat().st_size)
        with path.open("ab") as stream:
            stream.write(b"ial\n")
        self.assertEqual(tail.read(), ["partial"])

    def test_monitor_never_calls_scheduler_lock_and_properties_do_no_io(self):
        path = self.task(state="running", pid=123, identity="born", repo="/unused")
        append(path / "events.jsonl", message("initial"))
        processes = [{123: "born"}]
        with (
            patch.object(task_view, "process_snapshot", return_value=processes),
            patch.object(task_view, "waiting_slots", return_value={"alpha": "build"}),
            patch("host_slots.snapshot", side_effect=AssertionError("scheduler lock")),
        ):
            mon = self.monitor()
            mon.poll()
            self.assertIn("waiting for build slot", mon.by_name["alpha"].title())
            append(path / "events.jsonl", message("new event"))
            self.assertIn("new event", [line.text for line in mon.poll()])
            mon.title()
        with patch.object(task_view, "waiting_slots", side_effect=AssertionError("display I/O")):
            self.assertIn("waiting for build slot", mon.by_name["alpha"].title())

    def test_slot_snapshot_is_read_only_and_ignores_stale_processes(self):
        slots = self.root / "slots"
        slots.mkdir()
        state = slots / "state.json"
        state.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "pid": 1,
                            "identity": "born",
                            "sandbox": "alpha",
                            "kind": "build",
                            "state": "waiting",
                        },
                        {
                            "pid": 2,
                            "identity": "old",
                            "sandbox": "beta",
                            "kind": "build",
                            "state": "waiting",
                        },
                    ]
                }
            )
        )
        before = (state.read_bytes(), state.stat().st_mtime_ns)
        with patch("host_slots.root", return_value=slots):
            self.assertEqual(task_view.waiting_slots([{1: "born", 2: "new"}]), {"alpha": "build"})
        self.assertEqual((state.read_bytes(), state.stat().st_mtime_ns), before)
        self.assertEqual([p.name for p in slots.iterdir()], ["state.json"])

    def test_compact_list_limits_history_and_never_calls_detailed_status(self):
        for n in range(15):
            self.task(f"done-{n:02}", finished=200 + n)
        running = self.task("z-running", state="running", pid=1, identity="born", finished=None)
        self.task("stale", state="running", pid=2, identity="old", finished=None)
        append(running / "events.jsonl", message("hi"))

        def listing(**kwargs):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                task_runner.list_tasks(**kwargs)
            return output.getvalue()

        with (
            patch.object(task_runner.s, "tasks_root", return_value=self.tasks),
            patch.object(task_view, "process_snapshot", return_value=[{1: "born", 2: "new"}]),
            patch.object(task_view, "waiting_slots", return_value={}),
            patch.object(task_runner, "status", side_effect=AssertionError("detailed status")),
            patch.object(task_runner, "read_events", side_effect=AssertionError("full scan")),
            patch.object(task_runner.s, "git", side_effect=AssertionError("per-task git")),
        ):
            output = listing()
            self.assertTrue(output.startswith("z-running: running"))
            self.assertIn("6 more stopped/inbox tasks omitted", output)
            self.assertIn("done-14: done", output)
            self.assertNotIn("done-00:", output)
            self.assertEqual(len(listing(running_only=True).splitlines()), 1)
            output = listing(all_tasks=True)
            self.assertIn("done-00: done", output)
            self.assertIn("stale: interrupted", output)
            self.assertEqual(len(output.splitlines()), 17)

    def test_batch_liveness_supports_legacy_locale_and_rejects_zombies(self):
        calls = []

        def run(*args, **kwargs):
            calls.append(args)
            born = "born canonical" if kwargs["env"]["LC_ALL"] == "C" else "born legacy"
            # ps prints five start-time fields; retain the same shape here.
            return SimpleNamespace(stdout=f"123 S {born} Oct 25 2026\n124 Z {born} Oct 25 2026\n")

        with (
            patch.dict(os.environ, {"LANG": "fi_FI.UTF-8", "LC_TIME": "fi_FI.UTF-8"}),
            patch.object(task_view.s, "run", side_effect=run),
        ):
            snapshot = task_view.process_snapshot()
        self.assertEqual(len(calls), 2)
        self.assertTrue(
            task_view.is_live({"pid": 123, "identity": "born legacy Oct 25 2026"}, snapshot)
        )
        self.assertFalse(
            task_view.is_live({"pid": 124, "identity": "born legacy Oct 25 2026"}, snapshot)
        )
        self.assertFalse(task_view.is_live({"pid": 123, "identity": "reused"}, snapshot))

    def test_omitted_archives_mark_token_total_incomplete(self):
        path = self.task()
        for n in range(3):
            append(path / f"events-{n}.jsonl", usage())
        mon = self.monitor()
        mon.poll()
        self.assertTrue(mon.by_name["alpha"].tokens_unknown)
        self.assertEqual(len(mon.event_keys), 2)

    def test_once_defaults_to_recent_history(self):
        path = self.task()
        append(path / "events.jsonl", *(message(f"history {n}") for n in range(20)))
        mon = self.monitor()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            watch_ui.run_plain(mon, 1, True)
        self.assertNotIn("history 0\n", output.getvalue())
        self.assertIn("history 19", output.getvalue())


class Navigation(Fixture):
    def test_navigation_wrapping_scrolling_and_new_event_anchor(self):
        path = self.task()
        append(path / "events.jsonl", *(message("row " + str(n)) for n in range(20)))
        mon = self.monitor(replay=None)
        mon.poll()
        mon.height = 8
        mon.visible(6, 40)
        mon.key("pgup")
        before = [line.text for line in mon.visible(6, 40)]
        append(path / "events.jsonl", message("long " * 40))
        mon.poll()
        self.assertEqual([line.text for line in mon.visible(6, 40)], before)
        mon.key("end")
        self.assertEqual(mon.channels[0].scroll, 0)
        mon.key("left")
        self.assertEqual(mon.active, len(mon.channels) - 1)
        mon.key("right")
        self.assertEqual(mon.active, 0)
        mon.key("1")
        self.assertFalse(mon.channels[1].unseen)
        mon.key("9")
        self.assertEqual(mon.active, 1)
        self.assertFalse(mon.key("q"))
        self.assertTrue(mon.key("x"))
        self.assertTrue(all(len(line.text) <= 5 for line in mon.visible(6, 5)))

    def test_last_markdown_toggle_late_creation_and_update(self):
        path = self.task()
        mon = self.monitor()
        mon.poll()
        mon.key("1")
        mon.key("l")
        self.assertIn("not available", mon.visible(20, 100)[0].text)
        (path / "last.md").write_text("## Result: DONE\nAll checked.\n")
        self.assertIn("## Result: DONE", [line.text for line in mon.visible(20, 100)])
        (path / "last.md").write_text("## Result: BLOCKED\n")
        self.assertIn("## Result: BLOCKED", [line.text for line in mon.visible(20, 100)])
        mon.key("l")
        self.assertFalse(mon.channels[1].show_last)

    def test_active_channel_visible_in_long_bar(self):
        mon = self.monitor()
        for n in range(50):
            mon.join(f"worker-{n}")
        mon.switch(45)
        self.assertIn("[45:worker-44", watch_ui.channel_bar(mon, 80))


class Identity(unittest.TestCase):
    def runner(self, run):
        # run.py also supports parallel discovery: never patch shared modules or
        # process environment while another Monitor test might be using them.
        spec = importlib.util.spec_from_file_location("identity_fixture", DEV / "task_runner.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.s = SimpleNamespace(run=run)
        module.os = SimpleNamespace(environ={"LANG": "fi_FI.UTF-8", "LC_ALL": "C.UTF-8"})
        return module

    def test_group_snapshots_and_legacy_stop_use_the_same_identity(self):
        canonical = "Sun Oct 25 12:00:00 2026"
        legacy = "su 25 lokakuuta 12:00:00 2026"

        def fake_run(*args, **kwargs):
            locale = kwargs["env"]["LC_ALL"]
            if "-axo" in args:
                self.assertEqual(locale, "C")
                output = f"123 123 S {canonical}\n"
            else:
                output = f"{canonical if locale == 'C' else legacy} S"
            return subprocess.CompletedProcess(args, 0, output)

        runner = self.runner(fake_run)
        signals = []
        runner.os.killpg = lambda pid, sig: signals.append(pid)
        self.assertEqual(runner.signal_group({"pid": 123, "identity": legacy}), {123: canonical})
        self.assertEqual(signals, [123])
        signals.clear()
        self.assertEqual(runner.signal_group({"pid": 123, "identity": "old birth"}), {})
        self.assertEqual(signals, [])

    def test_stable_locale_legacy_fallback_and_missing_identity(self):
        calls = []

        def fake_run(*args, **kwargs):
            locale = kwargs["env"]["LC_ALL"]
            calls.append(locale)
            text = (
                "Sun Oct 25 12:00:00 2026 S" if locale == "C" else "su 25 lokakuuta 12:00:00 2026 S"
            )
            return subprocess.CompletedProcess(args, 0, text)

        runner = self.runner(fake_run)
        self.assertEqual(runner.identity(123), "Sun Oct 25 12:00:00 2026")
        self.assertTrue(runner.running({"pid": 123, "identity": "su 25 lokakuuta 12:00:00 2026"}))
        self.assertFalse(runner.running({"pid": 123, "identity": "wrong start"}))
        self.assertFalse(runner.running({"pid": 123}))
        self.assertIn("fi_FI.UTF-8", calls)

    def test_zombie_and_missing_pid_not_live(self):
        for result in ("", "Sun Oct 25 12:00:00 2026 Z"):
            runner = self.runner(
                lambda *args, result=result, **kwargs: subprocess.CompletedProcess(args, 0, result)
            )
            self.assertIsNone(runner.identity(123))
            self.assertFalse(runner.running({"pid": 123, "identity": "old"}))


class Plain(Fixture):
    def test_once_replay_override_leaves_every_file_unchanged(self):
        path = self.task()
        append(path / "events.jsonl", *(message(f"history {n}") for n in range(12)))
        append(self.board, note(to="all"))
        env = {
            **os.environ,
            "TURKU3D_TASKS_ROOT": str(self.tasks),
            "TURKU3D_COORD_ROOT": str(self.root),
            "TURKU3D_WT_ROOT": str(self.root / "wt"),
            "TURKU3D_CODEX_LEDGER": str(self.ledger),
        }

        def snapshot():
            return {
                str(p): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*")
                if p.is_file()
            }

        before = snapshot()
        result = subprocess.run(
            [str(DEV / "watch"), "--plain", "--once", "--replay", "12"],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(snapshot(), before)
        self.assertIn("history 0", result.stdout)
        self.assertIn("history 11", result.stdout)
        self.assertIn("STATUS alpha", result.stdout)
        self.assertNotIn("\x1b", result.stdout)

    def test_missing_root_and_invalid_arguments(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(watch_ui.main(["--once", "--root", str(self.root / "missing")]), 2)
            for value in ("0", "-1", "nan", "inf"):
                with self.assertRaises(SystemExit) as caught:
                    watch_ui.main(["--interval", value])
                self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
