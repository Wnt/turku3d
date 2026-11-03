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
    (root / 'meta.json').write_text(json.dumps({'trace': {'version': 1, 'slices': timeline.SLICES}, 'jsProfiler': {'sampleInterval': 10}}))
    # 60 fps with two hitches: a 90 ms frame dominated by an install slice and a 70 ms frame next to a cell GLB.
    times = [i * 16.7 for i in range(60)]
    times[30:] = [t + 75 for t in times[30:]]
    times[45:] = [t + 55 for t in times[45:]]
    write('frames', [[[t, i, 5, 8, i, 100, 1e6] for i, t in enumerate(times)]])
    write('slices', [[times[30], 30, 0, 60, 0, 0, 0, 0]])
    # Five 10 ms samples of r186 node-program building inside the second hitch (70 ms): shader build, not the cell.
    trace = {'resources': ['http://x/node_modules/.vite/deps/three_webgpu.js?v=1', 'http://x/src/world/population.ts'],
             'frames': [{'name': 'build', 'resourceId': 0}, {'name': 'update', 'resourceId': 0}, {'name': 'walker', 'resourceId': 1}],
             'stacks': [{'frameId': 1}, {'frameId': 0, 'parentId': 0}, {'frameId': 2, 'parentId': 0}],
             'samples': [{'timestamp': times[45] - 60 + 10 * k, 'stackId': 1} for k in range(5)] + [{'timestamp': 10, 'stackId': 2}]}
    write('js', [{'t': 0, 'end': 2000, 'trace': trace}])
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
        {'t': times[30] + 1, 'k': 'band.swap', 'id': 't0_0_0', 'dir': 'out', 'reason': 'twin not compiled'},
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
            self.assertIn('swap: out twin not compiled', text)  # the code's own reason for the instant swap
            self.assertIn('hitches (> 50 ms): 2', text)
            # Hitches belong to what ran in the frame: the measured install slice, then the sampled shader build; the
            # cell GLB that finished nearby is context only.
            self.assertIn('slice:install', text); self.assertIn('js:shader-build', text)
            self.assertIn('near: cell.glb t0_5_0', text); self.assertNotIn('event:', text)
            self.assertIn('band.block>lod 1', text)

    def test_html_page_is_self_contained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture(root)
            page = timeline.svg_page(timeline.load(root))
            self.assertIn('<svg', page); self.assertIn('Full tiles', page); self.assertIn('Scenery cells', page)
            self.assertIn('Camera path', page); self.assertNotIn('http://', page.replace('http://www.w3.org/2000/svg', ''))
            self.assertIn('install 60.0 ms', page)


class HdCoverageTest(unittest.TestCase):
    # Camera at (375, 375) in tile t0_1_1 looking down +x (yaw -90 degrees about y), 60 degree fov, 16:9.
    CAM = [375, 10, 375, 0, -0.7071068, 0, 0.7071068, 60]

    def test_view_tiles_are_the_window_plus_the_wedge_ahead_within_400_m(self):
        import math
        tiles = timeline.view_tiles(self.CAM, math.atan(math.tan(math.radians(30)) * 16 / 9) + math.radians(5))
        window = {id for id, role in tiles.items() if role == 'window'}
        self.assertEqual(window, {f't0_{i}_{j}' for i in (0, 1, 2) for j in (0, 1, 2)})
        view = {id for id, role in tiles.items() if role == 'view'}
        self.assertIn('t0_3_1', view)       # straight ahead, nearest edge 375 m away
        self.assertNotIn('t0_-1_1', view)   # behind
        self.assertNotIn('t0_4_1', view)    # ahead but beyond 400 m

    def capture(self, events, stats=None):
        poses = [{'t': 1000 * k, 'cam': self.CAM} for k in range(4)]
        return {'poses': poses, 'stats': stats or [{'viewport': [1600, 900]}], 'events': events}

    def test_snapshots_give_the_share_drawing_full_models(self):
        # A 9x9 grid around t0_1_1: everything full except t0_2_1 (preview, in the window) at the second snapshot.
        full = 'F' * 81
        grid = list(full)
        grid[4 * 9 + 5] = 'P'
        events = [{'t': 0, 'k': 'coverage', 'i0': -3, 'j0': -3, 'n': 9, 'cells': full},
                  {'t': 1500, 'k': 'coverage', 'i0': -3, 'j0': -3, 'n': 9, 'cells': ''.join(grid)}]
        hd = timeline.hd_coverage(self.capture(events))
        self.assertEqual(hd['source'], 'coverage snapshots')
        shares = [x['share'] for x in hd['seconds']]
        self.assertEqual(shares[:2], [1, 1])
        self.assertLess(shares[2], 1)
        self.assertEqual(hd['seconds'][2]['missing'], [('t0_2_1', 'P', 'window')])
        lines = '\n'.join(timeline.hd_lines(self.capture(events), lambda t: t / 1000))
        self.assertIn('2 of 4 s below 100 %', lines)
        self.assertIn('t0_2_1 P*', lines)

    def test_without_snapshots_lifecycle_events_reconstruct_the_drawn_band(self):
        # The start window is full; the column ahead shows previews, t0_3_1 until its full model at 2.5 s; t0_0_0 leaves
        # the window's draw at 1.2 s (cached, then released to its covering cell).
        events = [*({'t': 0, 'k': 'tile.preview', 'id': f't0_3_{j}'} for j in (0, 1, 2)),
                  {'t': 1200, 'k': 'tile.cache', 'id': 't0_0_0'},
                  {'t': 1300, 'k': 'tile.release', 'id': 't0_0_0', 'covered': True},
                  {'t': 2500, 'k': 'tile.full', 'id': 't0_3_1', 'distant': False}]
        hd = timeline.hd_coverage(self.capture(events))
        self.assertEqual(hd['source'], 'reconstructed from tile lifecycle events')
        missing = [x['missing'] for x in hd['seconds']]
        self.assertEqual(missing[0], [(f't0_3_{j}', 'P', 'view') for j in (0, 1, 2)])
        self.assertIn(('t0_0_0', 'C', 'window'), missing[2])
        self.assertNotIn(('t0_3_1', 'P', 'view'), missing[3])


if __name__ == '__main__':
    unittest.main()
