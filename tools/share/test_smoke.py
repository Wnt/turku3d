import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("share_smoke", Path(__file__).with_name("smoke.py"))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class SmokeRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix="smoke-revision-")
        cls.repo = Path(cls.work.name)

        def git(*args):
            return subprocess.check_output(["git", "-C", str(cls.repo), *args], text=True, stderr=subprocess.PIPE).strip()

        def commit(message):
            git("add", "source")
            git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                "commit", "-qm", message)
            return git("rev-parse", "HEAD")

        git("init", "-q")
        (cls.repo / "source").write_text("requested\n")
        cls.requested = commit("requested slice")
        (cls.repo / "source").write_text("newer\n")
        cls.newer = commit("another agent's slice")
        git("checkout", "--orphan", "unrelated")
        cls.unrelated = commit("unrelated history")

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def setUp(self):
        self.deadline = patch.object(smoke, "DEADLINE", time.monotonic() + 5)
        self.deadline.start()
        self.addCleanup(self.deadline.stop)

    def test_exact_revision_and_prefix_need_no_git_objects(self):
        for want in [self.requested, self.requested[:8]]:
            self.assertTrue(smoke.version_relation(self.requested, want, "/no-repository")[0])

    def test_a_newer_descendant_proves_the_requested_slice_is_deployed(self):
        ok, detail = smoke.version_relation(self.newer, self.requested[:8], self.repo)
        self.assertTrue(ok)
        self.assertIn("includes requested", detail)
        self.assertIn("newer deploy", detail)

    def test_an_older_revision_is_red(self):
        self.assertFalse(smoke.version_relation(self.requested, self.newer, self.repo)[0])

    def test_unrelated_history_is_red(self):
        self.assertFalse(smoke.version_relation(self.unrelated, self.requested, self.repo)[0])

    def test_unknown_revision_is_red(self):
        ok, detail = smoke.version_relation("0" * 40, self.requested, self.repo)
        self.assertFalse(ok)
        self.assertIn("cannot verify ancestry locally", detail)

    def test_malformed_metadata_is_red_even_without_a_requested_revision(self):
        for sha in [None, "", "not-a-commit", self.requested[:8]]:
            self.assertFalse(smoke.version_relation(sha, "", self.repo)[0])
        self.assertFalse(smoke.version_relation(self.newer, "--help", self.repo)[0])

    def test_ancestry_verification_uses_the_existing_smoke_deadline(self):
        with patch.object(smoke, "DEADLINE", time.monotonic() - 1):
            self.assertFalse(smoke.version_relation(self.newer, self.requested, self.repo)[0])

    def test_page_failure_stays_red_with_a_proven_newer_release(self):
        with patch.object(smoke, "REPO", self.repo), patch.object(smoke.sys, "argv", ["smoke.py", self.requested]), \
             patch.object(smoke, "get", side_effect=[(503, b"offline"), (200, json.dumps({"sha": self.newer}).encode())]):
            self.assertFalse(smoke.version()[0])

    def test_main_still_checks_farm_outcomes_when_a_newer_release_contains_the_slice(self):
        for failures, expected in [([], 0), ([{"error": "failed build"}], 1)]:
            def get(url):
                data = {
                    smoke.SITE + "/": b"page",
                    smoke.SITE + "/version.json": json.dumps({"sha": self.newer}).encode(),
                    smoke.FARM + "/status": b'{"aliases":{"train22":{}}}',
                    smoke.FARM + "/v/train22/coarse/health": json.dumps({"state": "ready", "failures": failures}).encode(),
                }
                return 200, data[url]

            output = io.StringIO()
            with patch.object(smoke, "REPO", self.repo), patch.object(smoke.sys, "argv", ["smoke.py", self.requested]), \
                 patch.object(smoke, "get", side_effect=get), patch.object(smoke, "scenery", return_value=(True, "fixture")), \
                 patch.object(smoke, "cities", return_value=(True, "fixture")), \
                 patch.object(smoke, "manifest", side_effect=lambda prefix: lambda: (True, "fixture")), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(smoke.main(), expected)
            self.assertIn("includes requested", output.getvalue())
            if failures:
                self.assertIn("RED farm:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
