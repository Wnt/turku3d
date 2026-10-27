"""Boundary cases for registry lifetime, drift and source budgets."""

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from checks import IMAGE_ALLOWLIST, check_policy, check_published_images, flags_in_code
from test_gate import Fixture


class PolicyTest(unittest.TestCase):
    def test_literal_flag_calls_and_bypass(self):
        self.assertEqual(flags_in_code("x.py", 'feature_flag("trial")')[0], {"trial"})
        self.assertEqual(flags_in_code("x.ts", "featureFlag('trial')")[0], {"trial"})
        for path, source in [
            ("x.py", "feature_flag(name)"),
            ("x.ts", "featureFlag(name)"),
            ("x.py", "from turku3d.flags import feature_flag as f"),
            ("x.ts", "import { featureFlag as f } from './flags'"),
            ("x.py", 'os.getenv("TURKU3D_AB_TRIAL")'),
        ]:
            self.assertTrue(flags_in_code(path, source)[1], source)

    def test_registry_boundaries(self):
        fixture = Fixture()
        self.addCleanup(fixture.close)
        config = {
            "budgets": {"lines": {"py": 5}, "binary_kib": 512, "binary_allowed": []},
            "flags": {
                "registry": "docs/flags.toml",
                "main": "main",
                "sources": ["pipeline/*.py"],
                "readers": [],
            },
        }
        fixture.write("pipeline/example.py", 'feature_flag("trial")\n')
        entry = '[[flags]]\nname="trial"\nowner="test"\nreason="Compare A/B"\nadded_at_merge=1\nremove_after_merges=1\n'
        fixture.write("docs/flags.toml", entry)
        self.assertEqual(check_policy(fixture.root, ["pipeline/example.py"], config), [])
        for old, new in [
            ("merges=1", "merges=11"),
            ("merge=1", "merge=2"),
            ('owner="test"', 'owner=""'),
        ]:
            fixture.write("docs/flags.toml", entry.replace(old, new))
            self.assertTrue(check_policy(fixture.root, ["pipeline/example.py"], config))
        fixture.write("docs/flags.toml", entry)
        fixture.write("pipeline/example.py", "# removed experiment\n")
        self.assertIn(
            "no use", "\n".join(check_policy(fixture.root, ["pipeline/example.py"], config))
        )
        fixture.write("pipeline/example.py", "# over budget\n" * 6)
        self.assertIn(
            "exceeds 5", "\n".join(check_policy(fixture.root, ["pipeline/example.py"], config))
        )


class PublishedImageTest(unittest.TestCase):
    """Street imagery and large images never reach the public repository unlisted."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.files = []

    def write(self, path, data=b"img"):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data.encode() if isinstance(data, str) else data)
        self.files.append(path)

    def errors(self):
        return check_published_images(self.root, sorted(self.files))

    def allow(self, *entries):
        rows = [
            f'[[images]]\npath = "{path}"\nsource = "{source}"\nlicence = "{licence}"\n'
            'reason = "Synthetic test image"\n'
            for path, source, licence in entries
        ]
        self.write(IMAGE_ALLOWLIST, "\n".join(rows))

    def test_marked_paths_fail(self):
        for path in (
            "docs/research/streetview/frame.jpg",
            "docs/reports/street_view_01.png",
            "textures/look-around/face0.webp",
            "qa/mapillary_coverage.jpg",
            "game/public/streetside.png",
            "tests/pano_crop.heic",
            "docs/reports/batch/contact-batch-01.jpg",
            "docs/pilot/Reference_LR15.gif",
            "game/tests/refs/view.jpeg",
        ):
            with self.subTest(path=path):
                self.files = []
                self.write(path)
                [error] = self.errors()
                self.assertIn("marks street imagery", error)

    def test_unmarked_images_and_other_roots_pass(self):
        self.write("docs/reports/before-sv_01.png")
        self.write("game/public/assets/ui/icon_car.webp")
        self.write("pipeline/turku3d/assets/streetview.png", b"x" * (600 * 1024))
        self.write("docs/reports/notes.txt", "streetview")
        self.assertEqual(self.errors(), [])

    def test_sidecar_and_caption_mark_an_image(self):
        self.write("docs/reports/a/frame.png")
        self.write("docs/reports/a/frame.png.json", '{"provider": "Mapillary"}')
        self.write("docs/reports/b/view.jpg")
        self.write("docs/reports/b/view.txt", "Apple Look Around face 0")
        self.write("docs/reports/c/shot.png")
        self.write("docs/reports/c.md", "![Street View frame](c/shot.png)\n")
        self.write("docs/reports/d/game.png")
        self.write("docs/reports/d/game.png.json", '{"backend": "webgpu"}')
        self.write("docs/reports/d.md", "![Game render](./d/game.png)\n")
        errors = "\n".join(self.errors())
        self.assertIn("frame.png: sidecar docs/reports/a/frame.png.json", errors)
        self.assertIn("view.jpg: sidecar docs/reports/b/view.txt", errors)
        self.assertIn("shot.png: caption 'Street View frame'", errors)
        self.assertNotIn("game.png", errors)

    def test_large_image_needs_an_allowlist_entry(self):
        self.write("docs/reports/map.jpg", b"x" * (512 * 1024 + 1))
        [error] = self.errors()
        self.assertIn("exceeds 512 KiB", error)
        self.allow(("docs/reports/map.jpg", "City of Turku 2022 true orthophoto", "CC BY 4.0"))
        self.assertEqual(self.errors(), [])

    def test_allowlist_admits_a_marked_image_with_provenance(self):
        self.write("docs/research/reference_facade.jpg")
        self.allow(("docs/research/reference_facade.jpg", "Wikimedia Commons photo", "CC0 1.0"))
        self.assertEqual(self.errors(), [])

    def test_allowlist_entries_are_validated(self):
        self.write("docs/a.jpg")
        self.write("docs/b.jpg")
        self.allow(
            ("docs/a.jpg", "Google Street View frame", "CC0 1.0"),
            ("docs/b.jpg", "Project render", ""),
            ("docs/b.jpg", "Project render", "MIT"),
            ("docs/missing.jpg", "Project render", "MIT"),
        )
        errors = "\n".join(self.errors())
        self.assertIn("docs/a.jpg: street imagery (Google) cannot be published", errors)
        self.assertIn("docs/b.jpg: missing or multi-line licence", errors)
        self.assertIn("docs/b.jpg: duplicate entry", errors)
        self.assertIn("docs/missing.jpg: no such file", errors)

    def test_policy_stage_runs_the_image_check(self):
        self.write("docs/flags.toml", "flags = []\n")
        self.write("docs/streetview/pano.jpg")
        config = {
            "budgets": {"lines": {}, "binary_kib": 512, "binary_allowed": []},
            "flags": {"registry": "docs/flags.toml", "main": "main", "sources": [], "readers": []},
        }
        with unittest.mock.patch("checks.subprocess.check_output", return_value=b"1"):
            errors = check_policy(self.root, sorted(self.files), config)
        self.assertTrue(any("marks street imagery" in error for error in errors))
