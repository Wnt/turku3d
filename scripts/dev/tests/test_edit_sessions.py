"""Edit sessions read back as effective edits (undo removed, last edit per object) and clusters."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edit_sessions  # noqa: E402

SERIAL = True  # patches os.environ


def edit(seq, key, action="move", move=1.0, reason=None, surface="road"):
    return {"format": "turku3d-edit", "version": 1, "id": f"s-{seq:04d}", "seq": seq, "session": "s", "action": action,
            "changes": {"translated": action == "move", "rotated": action == "rotate", "height": False},
            "object": {"key": key, "category": "prop", "type": "bench", "tile": "t0_0_0", "object_id": key.split(":", 2)[2],
                       "provenance": {"dataset": "GIS_Penkit"}},
            "delta": None if action == "delete" else {"dx": move, "dz": 0, "dy": 0, "horizontal_m": move, "yaw_deg": 0},
            "reason": reason, "context": {"before": {"surface": surface, "road": {"edge_m": -0.4}}, "after": None}}


class EditSessionTests(unittest.TestCase):
    def test_effective_final_and_clusters(self):
        with tempfile.TemporaryDirectory() as temp:
            session = Path(temp) / "s"
            session.mkdir()
            (session / "session.json").write_text(json.dumps({"id": "s", "started": "t"}))
            lines = [edit(1, "prop:t0_0_0:GIS_Penkit:a", move=2, reason="on_road_or_path"),
                     edit(2, "prop:t0_0_0:GIS_Penkit:a", move=3, reason="on_road_or_path"),
                     edit(3, "prop:t0_0_0:GIS_Penkit:b", action="delete", reason="does_not_exist"),
                     {"format": "turku3d-edit", "version": 1, "id": "s-0004", "seq": 4, "session": "s", "action": "undo", "undoes": "s-0002"},
                     edit(5, "prop:t0_0_0:GIS_Penkit:c", move=1),
                     {"format": "turku3d-edit", "version": 1, "id": "s-0006", "seq": 6, "session": "s", "action": "tag", "edit": "s-0005", "reason": "wrong_facing"},
                     {"format": "turku3d-edit", "version": 1, "id": "s-0007", "seq": 7, "session": "s", "action": "tag", "edit": "s-0005", "reason": "on_road_or_path"}]
            (session / "edits.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n{torn")
            rows = edit_sessions.rows(session)
            self.assertEqual([r["seq"] for r in edit_sessions.effective(rows)], [1, 3, 5])
            self.assertEqual(edit_sessions.effective(rows)[2]["reason"], "on_road_or_path")  # the newest tag
            final = edit_sessions.final(rows)
            self.assertEqual(final["prop:t0_0_0:GIS_Penkit:a"]["delta"]["horizontal_m"], 2)
            out = io.StringIO()
            with patch.dict(os.environ, {"TURKU3D_EDITS": temp}), contextlib.redirect_stdout(out):
                self.assertEqual(edit_sessions.main(["clusters"]), 0)
            clusters = [json.loads(line) for line in out.getvalue().splitlines()]
            self.assertEqual(clusters[0]["count"], 2)
            self.assertEqual(clusters[0]["reason"], "on_road_or_path")
            self.assertEqual(clusters[0]["median_move_m"], 1.5)
            self.assertEqual(clusters[0]["original_surface"], {"road": 2})
            self.assertEqual(clusters[1]["action"], "delete")

    def test_show_prints_the_summary_by_id(self):
        with tempfile.TemporaryDirectory() as temp:
            session = Path(temp) / "20261104-201000-abc123"
            session.mkdir()
            (session / "session.json").write_text(json.dumps({"id": session.name, "started": "t", "ended": "u",
                                                              "player": {"id": "p1", "nick": "Jonni"}}))
            (session / "edits.jsonl").write_text(json.dumps(edit(1, "prop:t0_0_0:GIS_Penkit:a")) + "\n")
            (session / "summary.txt").write_text(f"Edit session {session.name} · city.lab.madekivi.fi\n- 1 bench moved 1.0 m\n")
            out = io.StringIO()
            with patch.dict(os.environ, {"TURKU3D_EDITS": temp}), contextlib.redirect_stdout(out):
                self.assertEqual(edit_sessions.main(["show", session.name]), 0)
            lines = out.getvalue().splitlines()
            self.assertEqual(lines[0], f"Edit session {session.name} · city.lab.madekivi.fi")
            self.assertEqual(lines[2], f"folder: {session}")
            self.assertEqual(json.loads(lines[3])["player"]["nick"], "Jonni")
            self.assertEqual(json.loads(lines[3])["objects"], 1)
            err = io.StringIO()
            with patch.dict(os.environ, {"TURKU3D_EDITS": temp}), contextlib.redirect_stderr(err):
                self.assertEqual(edit_sessions.main(["show", "20990101-000000-zzzzzz"]), 1)
            self.assertIn("pull 20990101-000000-zzzzzz", err.getvalue())


if __name__ == "__main__":
    unittest.main()
