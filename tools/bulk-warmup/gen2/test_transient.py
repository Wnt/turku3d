import tempfile, time, unittest
from pathlib import Path

from transient import Transient


class Transient_(unittest.TestCase):
    def test_finished_tiles_and_units_leave_no_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            for tile in ('t0_0_0', 't0_1_0'):
                for kind in ('stages', 'inputs'):
                    (cache / kind / tile / 'x').mkdir(parents=True)
                    (cache / kind / tile / 'x' / 'e.pkl').write_bytes(b'12345')
            files = {u: cache / f'{u}.npy' for u in ('a', 'b')}
            for f in files.values(): f.write_bytes(b'unit')
            shared = cache / 'stages' / 'building-attachments' / 'k'; shared.mkdir(parents=True)
            units = {'t0_0_0': ['a', 'b'], 't0_1_0': ['b']}
            t = Transient(cache, list(units), units.get, lambda u: [files[u]])
            t.done('t0_0_0'); t.close()
            self.assertFalse((cache / 'stages' / 't0_0_0').exists())
            self.assertFalse((cache / 'inputs' / 't0_0_0').exists())
            self.assertTrue((cache / 'stages' / 't0_1_0').exists())
            self.assertFalse(files['a'].exists())  # no remaining tile needs unit a
            self.assertTrue(files['b'].exists())   # t0_1_0 still needs unit b
            self.assertTrue(shared.exists())       # shared neighbour context stays
            t = Transient(cache, ['t0_1_0'], units.get, lambda u: [files[u]])
            t.done('t0_1_0'); t.close()
            self.assertFalse(files['b'].exists())
            self.assertEqual(t.metrics['units_removed'], 1)

    def test_archetypes_older_than_every_inflight_build_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp); root = cache / 'stages' / 'archetypes'
            old, new = root / 'old' / 'package', root / 'new' / 'package'
            for d in (old, new): d.mkdir(parents=True); (d / 'k.pkl').write_bytes(b'x')
            import os
            os.utime(old / 'k.pkl', (time.time() - 600, time.time() - 600))
            t = Transient(cache, [], lambda tile: [], lambda u: [], grace=60)
            t.sweep(time.time() - 120); t.close()
            self.assertFalse((root / 'old').exists())
            self.assertTrue((root / 'new').exists())


if __name__ == '__main__':
    unittest.main()
