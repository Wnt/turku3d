"""Synthetic inbox cases for the Codex task hooks."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inbox_hook


class InboxHookTests(unittest.TestCase):
    def row(self, kind, ident, **extra):
        return {
            "kind": kind,
            "id": ident,
            "from": "sender",
            "subject": ident,
            "body": f"body for {ident}",
            **extra,
        }

    def test_ack_records_remove_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inbox.jsonl"
            rows = [
                self.row("request", "one"),
                self.row("warning", "two"),
                self.row("ack", "ack-one", ack="one"),
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows) + '{"partial":')
            self.assertEqual([row["id"] for row in inbox_hook.unread(path)], ["two"])

    def test_announcements_repeat_only_urgent_messages(self):
        rows = [
            self.row(kind, kind) for kind in ("request", "warning", "note", "interface", "landed")
        ]
        first, seen = inbox_hook.announcements(rows, {}, 1000)
        self.assertIn("body for request", first)
        self.assertIn("rebase before the next gate", first)
        self.assertNotIn("body for note", first)
        self.assertEqual(inbox_hook.announcements(rows, seen, 1899)[0], "")
        repeat, _ = inbox_hook.announcements(rows, seen, 1900)
        self.assertIn("body for request", repeat)
        self.assertIn("body for warning", repeat)
        self.assertNotIn("Inbox note", repeat)

    def test_context_limit_preserves_later_messages(self):
        rows = [self.row("request", "one", body="x" * 1000), self.row("request", "two")]
        context, seen = inbox_hook.announcements(rows, {}, 100, limit=150)
        self.assertLessEqual(len(context), 150)
        self.assertEqual(set(seen), {"one"})

    def test_stop_blocks_once_for_unread_urgent_messages(self):
        rows = [self.row("note", "n"), self.row("request", "r")]
        self.assertEqual(inbox_hook.stop_decision(rows, False)["decision"], "block")
        self.assertIn("r", inbox_hook.stop_decision(rows, False)["reason"])
        self.assertEqual(inbox_hook.stop_decision(rows, True), {})
        self.assertEqual(inbox_hook.stop_decision(rows[:1], False), {})


if __name__ == "__main__":
    unittest.main()
