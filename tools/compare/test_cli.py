"""Small safety-boundary checks, without opening a browser."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.compare.gmaps_compare import local_game_url, output_dir


class CliTests(unittest.TestCase):
    def test_only_sandbox_game_ports(self):
        url = "http://127.0.0.1:5181/?tiles=/draft/tileset.json"
        self.assertEqual(local_game_url(url, 5180), url)
        for bad in (
            "http://localhost:5171",
            "http://localhost:5175",
            "http://localhost:5190",
            "https://example.com:5181/",
            "http://user@localhost:5181/",
        ):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                local_game_url(bad, 5180)

    def test_local_output_labels_and_repo_exclusion(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp).resolve()
            with patch("tools.compare.gmaps_compare.ROOT", work / "repo"):
                first = output_dir(work, "hameensilta")
                second = output_dir(work, "hameensilta")
                self.assertTrue(first.is_relative_to(work / "qa/gmaps-compare"))
                self.assertNotEqual(first, second)
                with self.assertRaises(ValueError):
                    output_dir(work / "repo", "view")
            for label in ("../escape", "/abs", "", "a/b"):
                with self.subTest(label=label), self.assertRaises(ValueError):
                    output_dir(work, label)
