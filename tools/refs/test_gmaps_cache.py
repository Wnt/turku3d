import os
from pathlib import Path
import tempfile
import unittest

from gmaps_cache import owned_usage, reserve_owned


class OwnedCacheTest(unittest.TestCase):
    def test_hardlinks_share_one_quota_allocation_and_limit_remains_strict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'body').write_bytes(b'one body')
            for index in range(100):
                os.link(root/'body', root/f'extent-{index}')
            self.assertEqual(owned_usage(root), 8)
            reserve_owned(root, 10, reserve=2)
            with self.assertRaises(ValueError):
                reserve_owned(root, 10, reserve=3)
            (root/'outside').symlink_to(root/'body')
            with self.assertRaises(ValueError):
                owned_usage(root)


if __name__ == '__main__':
    unittest.main()
