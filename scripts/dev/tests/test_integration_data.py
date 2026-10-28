"""Pure data-lane scheduling/publication decisions; no builds or services."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import integration_data as data  # noqa: E402


class DataDecisionTests(unittest.TestCase):
    def test_carried_game_settles_the_published_destination(self):
        row = {
            "published": "/data",
            "lane": "data",
            "from": "old",
            "to": "new",
            "data_key": "key",
            "result": "green",
        }
        self.assertTrue(data.taken([row], Path("/data"), "new", "key"))
        self.assertTrue(data.taken([row], Path("/data"), "old", "key"))
        for change in (
            {"published": "/other"},
            {"lane": "code"},
            {"data_key": "other"},
            {"result": "failed"},
            {"result": "superseded"},
        ):
            with self.subTest(change=change):
                self.assertFalse(data.taken([{**row, **change}], Path("/data"), "new", "key"))
        self.assertFalse(data.taken([row], Path("/data"), "new", "changed"))

    def test_live_identity_does_not_need_a_completed_ledger_row(self):
        key = "123456789abcdef"
        record = {"commit": "new", "data_key": key, "tiles_commit": "builder"}
        self.assertTrue(data.already_live("new-123456789abc", {}, "new", key))
        self.assertTrue(data.already_live("renamed", record, "new", key))
        self.assertFalse(data.already_live("renamed", record, "other", key))
        self.assertFalse(data.already_live("renamed", record, "new", "changed"))
        self.assertTrue(data.already_live("renamed", record, "new", key, "builder"))
        self.assertFalse(data.already_live("renamed", record, "new", key, "new-builder"))
        self.assertTrue(data.already_live("new-123456789abc-builder", {}, "new", key, "builder"))
        self.assertFalse(data.already_live("new-123456789abc", {}, "new", key, "builder"))


if __name__ == "__main__":
    unittest.main()
