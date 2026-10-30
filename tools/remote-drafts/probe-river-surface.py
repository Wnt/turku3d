"""Selective deployed Aura topology, first/full geometry and serving evidence."""

import hashlib
import json
import pickle
import runpy
import subprocess
import time
from pathlib import Path

import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse import water
from turku3d.coarse.fences import STYLES
from turku3d.coarse.inputs import _frame
from turku3d.coarse.packaging import code_hash
from turku3d.coarse.sources import Sources
from turku3d.col.format import read_col
from turku3d.col.materials import mat_id
from turku3d.package.gltf import read_glb
from turku3d.spatial import frame
from turku3d.stage_cache import decompress_payload

ROOT = Path('/srv/turku3d/work/coarse')
SPAWN = ('t0_0_0', 't0_0_1', 't0_1_0', 't0_1_1')
PROTOTYPES = {*STYLES, 'chain_panel'}


def naked_prototypes(data):
    document, _ = read_glb(data)
    return [n.get('extras', {}).get('object_id') for n in document['nodes']
            if n.get('extras', {}).get('layer') == 'structures'
            and n.get('extras', {}).get('object_id') in PROTOTYPES
            and 'EXT_mesh_gpu_instancing' not in n.get('extensions', {})]


def survey():
    sources = Sources()
    cached = {p.parent.name for p in ROOT.glob('t0_*/manifest.json')}
    paths = {sources.osm_source(C.VisualTile.parse(n)) for n in cached | set(SPAWN)} - {None}
    mapped = []
    for path in sorted(paths):
        named = []
        for row in frame(path).itertuples():
            tags = json.loads(row.tags or '{}')
            if tags.get('waterway') == 'river' and tags.get('name') in ('Aurajoki', 'Aura', 'Aura å'):
                named.append(shapely.force_2d(row.geometry_local))
        centre = shapely.union_all(named)
        mapped.extend(g for g in water.flowing_areas(path) if g.intersects(centre))
    reach = shapely.union_all(mapped)
    assert not reach.is_empty, 'No mapped Aura reach'
    interior = reach.buffer(-water.INTERIOR_MARGIN - .001)
    lo_x, lo_z, hi_x, hi_z = reach.bounds
    first, last = C.VisualTile.at(lo_x, lo_z), C.VisualTile.at(hi_x, hi_z)
    rows, unavailable = [], []
    for i in range(first.i, last.i + 1):
        for j in range(first.j, last.j + 1):
            tile = C.VisualTile(0, i, j)
            window = shapely.box(*tile.bbox.as_tuple())
            core = interior.intersection(window)
            if core.area < 1:
                continue
            found, path = sources.wfs_source('kanta:Vesikohde', tile)
            osm = sources.osm_source(tile)
            if not found or not osm:
                unavailable.append(tile.id)
                continue
            before = shapely.union_all([shapely.make_valid(g) for g, _ in _frame(path, tile.bbox)])
            after = shapely.union_all(water.prepare(tile, path, osm))
            missing = core.difference(after).area
            edges = after.boundary.intersection(core).length
            assert missing < .001 and edges < .001, (tile.id, missing, edges)
            row = dict(tile=tile.id, water_level=water.LEVEL,
                       added_water_m2=round(after.difference(before).intersection(window).area, 3),
                       internal_bank_edge_m_before=round(before.boundary.intersection(core).length, 3),
                       internal_bank_edge_m_after=round(edges, 6))
            manifest = ROOT / tile.id / 'manifest.json'
            if manifest.exists():
                m = json.loads(manifest.read_text())
                row['previous_water_level'] = m.get('water_level')
                visual = ROOT / tile.id / m['visual'][tile.id]['url']
                row['bare_prototypes_before'] = len(naked_prototypes(visual.read_bytes()))
            rows.append(row)
    assert rows and set(SPAWN) <= {r['tile'] for r in rows}
    # The original duck ponds must retain all three independent shoreline levels.
    from turku3d.coarse.banks import pond_bodies, pond_levels
    from types import SimpleNamespace

    tile = C.VisualTile.parse('t0_-1_2')
    _, path = sources.wfs_source('kanta:Vesikohde', tile)
    osm = sources.osm_source(tile)
    bodies = water.prepare(tile, path, osm)
    ponds = pond_bodies(SimpleNamespace(water=bodies, sources={'wfs': [None, None, str(path)], 'roads': [osm]}))
    paths = sorted({C.raw_dir('laz') / 'maanpinta' / f'{b.name}.laz'
                    for p in ponds for b in C.laz_tiles_for_bbox(C.BBoxLocal(*p.buffer(3).bounds))})
    levels = pond_levels(ponds, paths)
    assert len(levels) == 3 and all(level > 1 for _, level in levels), levels
    result = dict(bounds=reach.bounds, mapped_parts=len(shapely.get_parts(reach)),
                  available_tiles=len(rows), unavailable_tiles=unavailable,
                  height_changed_tiles=sum(r.get('previous_water_level') not in (None, water.LEVEL) for r in rows),
                  geometry_changed_tiles=sum(r['added_water_m2'] > .001 for r in rows),
                  prototype_affected_tiles=sum(r.get('bare_prototypes_before', 0) > 0 for r in rows),
                  internal_bank_edge_m_before=round(sum(r['internal_bank_edge_m_before'] for r in rows), 3),
                  internal_bank_edge_m_after=sum(r['internal_bank_edge_m_after'] for r in rows),
                  pond_levels=[round(h, 4) for _, h in levels], tiles=rows)
    print(json.dumps(dict(survey=result)), flush=True)
    return result


def verify(out, manifest):
    wet_count = 0
    for entry in manifest['collision'].values():
        col = read_col((out / entry['url']).read_bytes())
        wet = col.indices[col.materials == mat_id('water')]
        heights = col.world_positions()[wet, 1]
        assert np.all(heights == water.LEVEL), (manifest['tile'], np.unique(heights))
        wet_count += len(wet)
    assert wet_count
    visual = out / manifest['visual'][manifest['tile']]['url']
    assert not naked_prototypes(visual.read_bytes()), manifest['tile']
    times = json.loads((out / 'stage-times.json').read_text())
    key = times['river_banks']['key']
    payload = ROOT / '.stages' / manifest['tile'] / 'river_banks' / (key + '.pkl')
    dry, wet, bank, _ = pickle.loads(decompress_payload(payload.read_bytes()[32:]))
    assert np.all(wet.positions[:, 1] == water.LEVEL)
    sources = Sources()
    core = shapely.union_all(water.flowing_areas(sources.osm_source(C.VisualTile.parse(manifest['tile'])))).buffer(-3.001)
    for mesh in (dry, bank):
        centres = mesh.positions[mesh.indices].mean(axis=1)
        assert not shapely.contains_xy(core, centres[:, 0], centres[:, 2]).any(), manifest['tile']
    return dict(water_triangles=wet_count, water_level=water.LEVEL,
                bank_triangles=len(bank.indices), internal_bank_triangles=0, bare_prototypes=0)


def curl(path):
    return json.loads(subprocess.check_output([
        'curl', '-fsS', '--max-time', '4', 'https://turku.lab.madekivi.fi/coarse/' + path,
    ], text=True))


def served():
    deadline = time.monotonic() + 180
    pending, rows = set(SPAWN), {}
    while pending and time.monotonic() < deadline:
        status = curl('status')
        generation = status['code']
        active = Path('/home/ubuntu/turku3d-drafts/repo').resolve()
        assert (active / 'pipeline/turku3d/coarse/water.py').read_bytes() == Path(water.__file__).read_bytes()
        for name in sorted(pending):
            m = curl(name + '/collision.json?d=0')
            if m.get('stale') or m.get('pass_kind') != 'full' or m.get('generation') != generation:
                continue
            entry = m['visual'][name]
            data = (ROOT / name / entry['url']).read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry['sha256']
            assert not naked_prototypes(data), name
            document, _ = read_glb(data)
            planes = []
            for node in document['nodes']:
                if node.get('extras', {}).get('material_key') == 'water':
                    for primitive in document['meshes'][node['mesh']]['primitives']:
                        a = document['accessors'][primitive['attributes']['POSITION']]
                        assert a['min'][1] == a['max'][1] == water.LEVEL, (name, a)
                        planes.append(a['min'][1])
            assert planes, name
            rows[name] = dict(generation=generation, stale=False, pass_kind='full',
                              visual_sha256=entry['sha256'], water_levels=planes, bare_prototypes=0)
            pending.remove(name)
        if pending:
            time.sleep(2)
    assert not pending, ('Still retained or rebuilding', sorted(pending))
    return rows


def main():
    result = dict(generation=code_hash(), survey=survey(), builds=[])
    run = runpy.run_path(str(Path(__file__).with_name('probe-builds.py')))['run']
    for name in ('t0_0_0', 't0_1_1'):
        row = run(ROOT, tile_id=name, first_budget=45, full_budget=110, verify=verify)
        result['builds'].append(row)
        print(json.dumps(dict(build=row)), flush=True)
    result['served'] = served()
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
