import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('tile_timeline', Path(__file__).with_name('tile_timeline.py'))
timeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timeline)


def capture(root: Path):
    def write(name, values):
        (root / f'{name}.jsonl').write_text(''.join(json.dumps(v) + '\n' for v in values))
    (root / 'meta.json').write_text(json.dumps({'trace': {'version': 1, 'slices': timeline.SLICES}}))
    # 60 fps with two hitches: a 90 ms frame dominated by an install slice and a 70 ms frame next to a cell GLB.
    times = [i * 16.7 for i in range(60)]
    times[30:] = [t + 75 for t in times[30:]]
    times[45:] = [t + 55 for t in times[45:]]
    write('frames', [[[t, i, 5, 8, i, 100, 1e6] for i, t in enumerate(times)]])
    write('slices', [[times[30], 30, 0, 60, 0, 0, 0, 0]])
    write('poses', [{'t': t, 'cam': [i * 10, 300, 0, 0, 0, 0, 1, 60], 'H': 300} for i, t in enumerate(times[::5])])
    write('stats', [{'t': 500, 'recorder': {'msPerFrame': 0.05, 'frames': 60, 'dropped': 0}}])
    write('tiles', [
        {'t': 10, 'k': 'tile.new', 'id': 't0_0_0', 'd': 120},
        {'t': 200, 'k': 'tile.manifest', 'id': 't0_0_0', 'ms': 150, 'status': 200, 'd': 110},
        {'t': 300, 'k': 'tile.collision', 'id': 't0_0_0', 'ms': 90},
        {'t': 450, 'k': 'tile.glb', 'id': 't0_0_0', 'ms': 240},
        {'t': 620, 'k': 'tile.prepare', 'id': 't0_0_0', 'ms': 160},
        {'t': times[30], 'k': 'tile.full', 'id': 't0_0_0', 'replaced': 'preview', 'd': 90},
        {'t': 40, 'k': 'tile.new', 'id': 't0_1_0'},
        {'t': 900, 'k': 'tile.manifest', 'id': 't0_1_0', 'ms': 800, 'status': 200},
        {'t': 950, 'k': 'band.fade', 'id': 't0_1_0', 'dir': 'out'},
        {'t': 960, 'k': 'tile.full', 'id': 't0_1_0', 'replaced': None, 'd': 300},
        {'t': times[45] - 20, 'k': 'cell.glb', 'id': 't0_5_0', 'ms': 30, 'bytes': 1000},
        {'t': times[45] + 5, 'k': 'cell.install', 'id': 't0_5_0', 'level': 0},
        {'t': 700, 'k': 'band.block', 'id': 't0_0_0', 'to': 'lod'},
        {'t': 800, 'k': 'coverage', 'i0': -1, 'j0': -1, 'n': 3, 'cells': 'CCC.FD' + 'CPU'},
    ])


class TileTimelineTest(unittest.TestCase):
    def test_summary_ranks_tiles_flags_incidents_and_attributes_hitches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture(root)
            text = timeline.summary(timeline.load(root))
            self.assertIn('recorder cost: mean 0.050 ms/frame', text)
            self.assertIn('2 reached a full model', text)
            # t0_1_0 (request 100 ms -> full 960 ms) ranks above t0_0_0 (50 -> ~576 ms); stages are listed per tile.
            ranked = text.split('slowest request -> full model')[1].splitlines()[1:3]
            self.assertIn('t0_1_0', ranked[0]); self.assertIn('manifest 800', ranked[0])
            self.assertIn('t0_0_0', ranked[1]); self.assertIn('glb 240', ranked[1])
            self.assertIn('1 hole cell-samples', text); self.assertIn('1 double-coverage samples', text)
            # t0_0_0 replaced its preview without a dissolve: a pop. t0_1_0 dissolved.
            self.assertIn('pop t0_0_0', text); self.assertNotIn('pop t0_1_0', text)
            self.assertIn('hitches (> 50 ms): 2', text)
            self.assertIn('slice:install', text); self.assertIn('event:cell', text)
            self.assertIn('band.block>lod 1', text)

    def test_html_page_is_self_contained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture(root)
            page = timeline.svg_page(timeline.load(root))
            self.assertIn('<svg', page); self.assertIn('Full tiles', page); self.assertIn('Scenery cells', page)
            self.assertIn('Camera path', page); self.assertNotIn('http://', page.replace('http://www.w3.org/2000/svg', ''))
            self.assertIn('install 60.0 ms', page)


if __name__ == '__main__':
    unittest.main()
