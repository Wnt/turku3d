"""Boundary cases for registry lifetime, drift and source budgets."""

import unittest

from checks import check_policy, flags_in_code
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
