"""Selective first/full OCI builds and generated-facade field/texture acceptance."""
import argparse
import importlib.util
import json
import struct
import time
from pathlib import Path


def main(root):
    spec = importlib.util.spec_from_file_location('build_probe', Path(__file__).with_name('probe-builds.py'))
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    result = []
    started = time.monotonic()
    for tile, building, wall_id, colour in [
        ('t0_0_0', '103295896N', '103295896N_770004661f', '#ac9686'),
        ('t0_0_1', '103295899S', '103295899S_a8098c44e3', '#7e96a4'),
    ]:
        remaining = 520 - (time.monotonic() - started)
        assert remaining > 30, 'No time remaining for second tile'
        row = probe.run(root, tile_id=tile, first_budget=min(45, remaining/3),
                        full_budget=min(220, remaining-20))
        out = Path(row['output'])
        programs = json.loads((out/'facade-programs.json').read_text())
        own = [p for p in programs if p['building_id'] == building]
        assert own, (tile, building, 'missing facade program')
        walls = [w for p in own for w in p['walls'] if w.get('measured_wall_id') == wall_id]
        assert walls, (tile, wall_id, 'no matched reviewed wall')
        assert all(w['paint_source'] == 'reviewed-program' for w in walls)
        assert all(w['wall']['colour']['value'] == colour for w in walls)
        if building == '103295899S':
            assert all(w['wall']['material']['value'] == 'stone' for p in own for w in p['walls'])
        else:
            assert all(w['bays']['count'] == 9 for w in walls)
            assert any(w.get('appearance_details', {}).get('pilasters', {}).get('value') for w in walls)
        manifest = json.loads((out/'manifest.json').read_text())
        texture_count = 0
        for entry in manifest['visual'].values():
            data = (out/entry['url']).read_bytes()
            assert data[:4] == b'glTF'
            length = struct.unpack_from('<I', data, 12)[0]
            doc = json.loads(data[20:20+length])
            texture_count += len(doc.get('textures', []))
        assert texture_count > 0, (tile, 'full pass has no textures')
        row['facade_acceptance'] = dict(building=building, matching_walls=len(walls),
            colour=colour, materials=sorted({w['wall']['material']['value'] for w in walls}),
            textures=texture_count, generated=True)
        result.append(row)
        print(json.dumps(row), flush=True)
    print(json.dumps(dict(result='PASS', seconds=round(time.monotonic()-started,3), tiles=len(result))), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    main(args.root)
