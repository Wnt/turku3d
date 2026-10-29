"""Real first/full passage regression and a read-only audit of existing opening geometry."""

import argparse
import importlib
import importlib.util
import json
import os
import pickle
import time
from pathlib import Path

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse import road_top
from turku3d.coarse.build_health import BuildHealth
from turku3d.coarse.inputs import gather
from turku3d.coarse.openings import Openings
from turku3d.coarse.sources import Sources
from turku3d.coarse.tunnels import dimensions, prepare
from turku3d.col.format import read_col
from turku3d.stage_cache import decompress_payload

MARKET = np.array([291.32, 210.84])


def cached(root, tile, times, stage):
    path = root / '.stages' / tile / stage / (times[stage]['key'] + '.pkl')
    return pickle.loads(decompress_payload(path.read_bytes()[32:]))


def area(mesh):
    tri = mesh.positions[mesh.indices]
    return float(np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1).sum() / 2)


def segment_hits(triangles, start, end):
    """Independent segment/triangle intersection, including vertical blocking faces."""
    direction = end - start
    edge1, edge2 = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    h = np.cross(direction, edge2)
    det = np.sum(edge1 * h, axis=1)
    valid = abs(det) > 1e-10
    inverse = np.where(valid, 1 / np.where(valid, det, 1), 0)
    s = start - triangles[:, 0]
    u = inverse * np.sum(s * h, axis=1)
    q = np.cross(s, edge1)
    v = inverse * (q @ direction)
    distance = inverse * np.sum(edge2 * q, axis=1)
    return valid & (u >= -1e-8) & (v >= -1e-8) & (u + v <= 1 + 1e-8) & (distance > 1e-7) & (distance < 1 - 1e-7)


def survey(root):
    tested, affected, details = set(), set(), []
    missing = []
    sources = Sources()
    for path in sorted(root.glob('t0_*/stage-times.json')):
        tile_id = path.parent.name
        times = json.loads(path.read_text())
        if not {'tunnels', 'stairs', 'stair_ground', 'river_banks'} <= times.keys():
            continue
        try:
            tunnels = cached(root, tile_id, times, 'tunnels')
            floors = [m for m in tunnels.meshes if m.object_id.endswith(':floor')]
            if not floors:
                continue
            structures, _ = cached(root, tile_id, times, 'stairs')
            dry, solid = cached(root, tile_id, times, 'stair_ground')
            _, river, bank, _ = cached(root, tile_id, times, 'river_banks')
            tile = C.VisualTile.parse(tile_id)
            tags = {key: tags for key, _, tags in prepare(tile, sources.osm_source(tile))}
        except (OSError, KeyError) as error:
            missing.append(dict(tile=tile_id, error=str(error)))
            continue
        for floor in floors:
            key = floor.object_id.removeprefix('tunnel:').removesuffix(':floor')
            if key not in tags:
                continue
            opening = Openings()
            opening.add(floor, dimensions(tags[key])[1])
            removed = sum(area(m) for m in structures) - sum(area(m) for m in opening.structures(structures))
            for mesh in (dry, river, bank, solid):
                removed += area(mesh) - area(opening.ground(mesh))
            tested.add(key)
            if removed > 1e-4:
                affected.add(key)
                details.append(dict(tile=tile_id, opening=key, removed_surface_m2=round(removed, 5)))
    report = dict(tested_openings=len(tested), affected_openings=len(affected),
                  ids=sorted(affected), details=details, unavailable_cached_tiles=missing)
    print(json.dumps(dict(survey=report)), flush=True)
    return report


def verify(tile, inputs, manifest, out, tunnels, raycaster):
    collision = {key: read_col((out / entry['url']).read_bytes())
                 for key, entry in manifest['collision'].items()}
    cols = {key: raycaster(col) for key, col in collision.items()}
    checked = []
    window = shapely.box(*tile.bbox.as_tuple())
    for key, line, tags in inputs.tunnels:
        if tags.get('vault') != 'barrel' or not window.covers(line):
            continue
        width, clearance = dimensions(tags)
        floor = next(m for m in tunnels.meshes if m.object_id == f'tunnel:{key}:floor')
        floor_ray = raycaster(type('Col', (), dict(positions=floor.positions,
                             indices=floor.indices, origin=np.zeros(3),
                             materials=np.zeros(len(floor.indices))))())
        rays, lowest = 0, np.inf
        # Include both two-metre approaches. This is a 1.3 m-wide pedestrian
        # route through the arch, including the actual mission interaction point.
        coords = np.asarray(line.coords)
        direction = (coords[-1] - coords[0]) / np.linalg.norm(coords[-1] - coords[0])
        normal = np.array([-direction[1], direction[0]])
        stations = np.arange(-1.9, line.length + 1.91, .2)
        points = [coords[0] + direction * d + normal * offset
                  for d in stations for offset in (-.65, 0, .65)]
        if line.distance(shapely.Point(MARKET)) < 1:
            points.append(MARKET)
        for xz in points:
            if not window.contains(shapely.Point(xz)):
                continue  # The neighbouring tile owns this end of the approach.
            x, z = xz
            expected, _ = floor_ray(x, z)
            assert len(expected), (key, x, z, 'missing generated floor')
            expected = float(max(expected))
            ys, _ = cols[C.CollisionTile.at(x, z).id](x, z)
            assert len(ys) and min(abs(ys - expected)) < .003, (key, x, z, 'missing floor collision', expected, ys)
            bad = (ys > expected + .06) & (ys < expected + 1.9)
            assert not bad.any(), (key, x, z, 'blocked passage collision', expected, ys[bad])
            above = ys[ys > expected + .06]
            if len(above):
                lowest = min(lowest, float(min(above) - expected))
            rays += 1
        triangles = np.concatenate([(col.positions + col.origin)[col.indices] for col in collision.values()])
        bbox = np.asarray(line.bounds)
        use = ((triangles[:, :, [0, 2]].max(axis=1) >= bbox[:2] - 1).all(axis=1)
               & (triangles[:, :, [0, 2]].min(axis=1) <= bbox[2:] + 1).all(axis=1))
        triangles = triangles[use]
        segments = 0
        for offset in (-.65, 0, .65):
            walk = []
            for distance in stations:
                x, z = coords[0] + direction * distance + normal * offset
                if not window.contains(shapely.Point(x, z)):
                    continue
                yy, _ = floor_ray(x, z)
                walk.append([x, max(yy), z])
            walk = np.asarray(walk)
            for lift in (.3, 1, 1.75):
                body = walk + [0, lift, 0]
                for start, end in zip(body[:-1], body[1:], strict=True):
                    assert not segment_hits(triangles, start, end).any(), (key, start, end, 'vertical collision blocks walking')
                    segments += 1
        checked.append(dict(id=key, rays=rays, walk_segments=segments, clearance_min_m=lowest))
    if tile.id == 't0_1_0':
        assert any(row['id'].startswith('quay-526219736-') for row in checked), checked
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    os.environ['TURKU3D_WORK'] = str(args.root)
    os.environ['TURKU3D_BROWSER_DIR'] = str(args.root / 'browser')
    report = dict(root=str(args.root), survey=survey(args.assets), runs=[])
    spec = importlib.util.spec_from_file_location('rays', Path(__file__).with_name('probe-underpass-roads.py'))
    rays = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rays)
    module = importlib.import_module('turku3d.coarse.build')
    original = road_top.stage
    captured = []
    def capture(*a, **kw):
        result = original(*a, **kw)
        captured[:] = [result[0]]
        return result
    road_top.stage = capture
    telemetry = BuildHealth(args.assets)
    # Reuse content-addressed inputs and stages; only owned outputs are written.
    for tile_id in ('t0_1_0', 't0_0_0', 't0_1_1', 't0_0_1'):
        tile = C.VisualTile.parse(tile_id)
        out = args.assets / ('.tunnel-probe-' + args.root.name + '-' + tile_id)
        for kind in ('first', 'full'):
            started = time.monotonic()
            try:
                inputs = gather(tile, args.assets, Sources(), first_pass=kind == 'first')
                manifest = module.build(tile_id, out, inputs=inputs, first_pass=kind == 'first')
                elapsed = time.monotonic() - started
                assert manifest['pass_kind'] == kind
                assert manifest['buildings'] == len(inputs.buildings)
                assert len(manifest['collision']) == 16
                for entry in [*manifest['visual'].values(), *manifest['collision'].values()]:
                    assert (out / entry['url']).is_file()
                checks = verify(tile, inputs, manifest, out, captured[0], rays.raycaster)
                assert elapsed < (45 if kind == 'first' else 150), (tile_id, kind, elapsed)
            except Exception as error:
                telemetry.record(kind, 'FAILED', time.monotonic() - started, type(error).__name__)
                raise
            telemetry.record(kind, 'built', elapsed)
            row = dict(tile=tile_id, pass_kind=kind, seconds=round(elapsed, 3),
                       source_buildings=len(inputs.buildings), buildings=manifest['buildings'],
                       collision_cells=16, openings=checks,
                       clearance_seconds=manifest['seconds'].get('opening_clearance'))
            report['runs'].append(row)
            print(json.dumps(row), flush=True)
            (args.root / 'result.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
