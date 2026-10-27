"""Explicit unloaded-host budgets; excluded from the functional gate."""

import time
import unittest

from test_gate import Fixture
from test_tooling import ToolingTests


class OrientationPerformance(unittest.TestCase):
    setUp = ToolingTests.setUp
    cli = ToolingTests.cli
    git = ToolingTests.git

    def test_docs_gate_budget(self):
        fixture = Fixture()
        self.addCleanup(fixture.close)
        fixture.write("docs/note.md", "hello\n")
        result, report = fixture.gate()
        self.assertEqual(result.returncode, 0)
        self.assertLess(report["seconds"], 2)

    def test_orientation_budget(self):
        start = time.monotonic()
        self.assertIn("main | main", self.cli("here").stdout)
        self.assertLess(time.monotonic() - start, 2)
