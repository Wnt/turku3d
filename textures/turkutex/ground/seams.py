"""Compare shared source-grid texels in neighbouring tile gutters before lossy encoding."""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from turku3d.config import VisualTile
from .build import CORE_PX, HALO_PX


def audit(root: Path):
    manifest = json.loads((root / 'MANIFEST.json').read_text())
    tiles = set(manifest['tiles_written'])
    comparisons = []
    for tile_id in sorted(tiles):
        tile = VisualTile.parse(tile_id)
        for axis, neighbour in [('east', VisualTile(0, tile.i + 1, tile.j)),
                                 ('south', VisualTile(0, tile.i, tile.j + 1))]:
            if neighbour.id not in tiles:
                continue
            n = CORE_PX + 2 * HALO_PX
            for name in ['ortho2022', 'ortho2025', 'ortho']:
                with Image.open(root / tile_id / f'{name}.png') as a, Image.open(root / neighbour.id / f'{name}.png') as b:
                    if axis == 'east':
                        pa, pb = a.crop((CORE_PX, 0, n, n)), b.crop((0, 0, 2 * HALO_PX, n))
                    else:
                        pa, pb = a.crop((0, CORE_PX, n, n)), b.crop((0, 0, n, 2 * HALO_PX))
                    # Exclude outer support where the local de-shadowing filter sees different padding.
                    delta = np.abs(np.asarray(pa).astype(np.int16) - np.asarray(pb).astype(np.int16))
                    h = HALO_PX // 2
                    delta = delta[h:-h, h:-h]
                    comparisons.append({'a': tile_id, 'b': neighbour.id, 'axis': axis, 'product': name,
                        'max_code_difference': int(delta.max()), 'mean_code_difference': float(delta.mean()),
                        'p99_code_difference': float(np.percentile(delta, 99))})
    report = {'comparisons': comparisons, 'pass': bool(comparisons) and all(c['p99_code_difference'] <= 2 for c in comparisons),
              'scope': 'source-grid seam continuity; lossy KTX2 seams also need rendered review'}
    (root / 'seams.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root', type=Path)
    a = ap.parse_args(argv)
    report = audit(a.root)
    print(json.dumps(report))
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
