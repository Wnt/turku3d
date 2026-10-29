"""Reduce reviewed wall/trim patches to colour descriptors; never publish crop pixels.

Input: JSON list of {building_id, wall_id, crop, wall: [[x,y,w,h], ...],
trim: [...]}. Rectangles are normalized to the existing characterization crop.
The median of patch medians limits one patch's lighting/area influence. These
are photo appearance priors, explicitly not calibrated albedo measurements.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


def sample(requests):
    result = {}
    for row in requests:
        path = Path(row['crop'])
        rgb = np.asarray(Image.open(path).convert('RGB'))
        height, width = rgb.shape[:2]
        wall = result.setdefault(row['building_id'], {}).setdefault('walls', {}).setdefault(row['wall_id'], {})
        for section in ('wall', 'trim'):
            medians = []
            for x, y, w, h in row[section]:
                assert 0 <= x < x+w <= 1 and 0 <= y < y+h <= 1
                patch = rgb[round(y*height):round((y+h)*height), round(x*width):round((x+w)*width)]
                assert patch.size
                medians.append(np.median(patch.reshape(-1, 3), axis=0))
            colour = np.rint(np.median(medians, axis=0)).astype(int)
            wall[section] = dict(colour=dict(value='#'+''.join(f'{v:02x}' for v in colour),
                source='reviewed same-wall patch medians; uncalibrated photo appearance prior', support=0.65))
        wall['provenance'] = dict(crop_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                  patches={key: row[key] for key in ('wall', 'trim')})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('requests', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(sample(json.loads(args.requests.read_text())), indent=2)+'\n')
