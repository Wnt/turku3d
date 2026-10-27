"""Advisory checks in synthetic integration batches: red Chrome, audits and facade drops are
published to live and Pages, reported to the batch's authors once, and never bisected."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import coordination  # noqa: E402
import integrator  # noqa: E402
import test_integrator  # noqa: E402

# The cases patch os.environ: tests/run.py runs them one by one in a process of their own.
SERIAL = True


class AdvisoryTests(test_integrator.IntegratorFixture):
    def test_red_chrome_is_advisory_publishes_live_and_pages_and_warns_the_authors(self):
        self.land("one", "game/a.ts", "a")
        head = self.land("two", "game/b.ts", "b")
        self.chrome_red = 1
        with self.pages() as publish:
            row = self.integrate()
        publish.assert_called_once_with(self.published)
        self.assertEqual((row["result"], row["builds"], row["culprit"]), ("green", 1, None))
        self.assertEqual([entry["stage"] for entry in row["advisory"]], ["chrome"])
        self.assertEqual(row["pages"]["url"], "https://o.github.io/site/")
        # Nothing was bisected or reverted; live and the release record carry the failure.
        self.assertEqual(self.git("rev-parse", "main"), head)
        self.assertEqual(integrator.live_commit(self.published), head)
        self.assertEqual(self.live_record()["advisory"], row["advisory"])
        self.assertEqual(self.live_record()["from"], self.base)
        self.assertEqual(row["warned"], ["one", "two"])
        for name in ("one", "two"):
            [warning] = coordination.inbox(name, unread=True)
            self.assertEqual(warning["kind"], "warning")
            self.assertIn("chrome webgpu:game/tests/e2e-tile/tile.spec.ts", warning["body"])
            self.assertIn(f"published {head[:12]} to live and Pages", warning["body"])

    def test_a_failure_the_previous_release_had_is_not_warned_again(self):
        self.land("one", "game/a.ts", "a")
        self.chrome_red = 2
        self.assertEqual(self.integrate()["warned"], ["one"])
        self.land("two", "game/b.ts", "b")
        row = self.integrate()
        self.assertEqual(([e["stage"] for e in row["advisory"]], row["warned"]), (["chrome"], []))
        self.assertEqual(coordination.inbox("two", unread=True), [])
        # A clean batch clears the release's advisory list.
        self.land("three", "game/c.ts", "c")
        row = self.integrate()
        self.assertEqual((row["advisory"], self.live_record()["advisory"]), ([], []))

    def test_red_deep_gate_after_publication_is_advisory_and_warned_once(self):
        red = '#!/bin/sh\ncase "$*" in *--deep*) echo "FAIL   tooling-tests 9.0s"; exit 1;; esac\n'
        head = self.land("one", "scripts/dev/gate", red)
        row = self.integrate()
        self.assertEqual((row["result"], integrator.live_commit(self.published)), ("green", head))
        [deep] = row["advisory"]
        self.assertEqual((deep["stage"], deep["checks"]), ("deep-gate", ["tooling-tests"]))
        self.assertIn("FAIL   tooling-tests", Path(deep["log"]).read_text())
        self.assertEqual(row["warned"], ["one"])
        self.land("two", "game/b.ts", "b")
        self.assertEqual(self.integrate()["warned"], [])

    def test_facade_drop_from_changed_approvals_is_published_with_a_warning(self):
        head = self.land("docs-and-prompts", "game/a.ts", "a")
        self.approve(1)
        # The code lane ships the live tiles: no drop there.
        row = self.integrate()
        self.assertEqual((row["result"], row["advisory"], row["facades_bound"]), ("green", [], 2))
        # The data lane rebuilds the tiles with the new approvals and publishes the drop.
        row = self.data()
        self.assertEqual((row["result"], row["culprit"], row["facades_bound"]), ("green", None, 1))
        [drop] = row["advisory"]
        self.assertEqual(
            (drop["stage"], drop["facades_bound"], drop["facades_baseline"]),
            ("facade-baseline", 1, 2),
        )
        self.assertIn("facades_bound regressed", drop["detail"])
        self.assertEqual(self.git("rev-parse", "main"), head)
        self.assertEqual(integrator.live_commit(self.published), head)
        [warning] = coordination.inbox("orchestrator", unread=True)
        self.assertIn("facade-baseline", warning["subject"])

    def test_every_published_batch_deploys_pages_full_suite_or_not(self):
        for full in (False, True):
            head = self.land(f"game-{full}", f"game/src/render/{full}.ts", "a")
            with self.pages() as publish:
                row = self.integrate(full=full)
            publish.assert_called_once_with(self.published)
            self.assertEqual((row["result"], row["full_suite"]), ("green", full))
            self.assertEqual(integrator.live_commit(self.published), head)
            self.assertEqual(self.live_record()["full_suite"], full)

    def test_red_full_run_is_advisory_and_reverts_nothing(self):
        self.land("first", "game/a.ts", "a")
        self.assertEqual(self.integrate(full=True)["result"], "green")
        self.land("suspect", "game/full-red.ts", "x")
        head = self.land("later", "game/c.ts", "c")
        self.assertEqual(self.integrate()["result"], "green")
        self.builds.clear()
        with patch.object(integrator, "full_due", return_value=True):
            row = self.integrate()
        # The full run of live main is recorded in place: one build, no bisection.
        self.assertEqual((row["result"], row["from"], row["culprit"]), ("green", head, None))
        self.assertEqual((self.builds, self.git("rev-parse", "main")), ([head], head))
        self.assertTrue((self.main / "game/full-red.ts").exists())
        self.assertEqual([entry["stage"] for entry in self.live_record()["advisory"]], ["chrome"])
        self.assertEqual(row["warned"], ["orchestrator"])


if __name__ == "__main__":
    import unittest

    unittest.main()
