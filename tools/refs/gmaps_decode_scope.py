"""Select conservative extent metadata bounds before decoding session payloads."""
import hashlib
import json
from pathlib import Path
import re
import struct

import shapely


def response_selection(capture, cache_root):
    import detail
    from mesh import fields
    from storage import atomic_json

    capture = Path(capture)
    record = json.loads((capture / 'capture.json').read_text())
    radius = next(struct.unpack('<f', fields((capture / row['file']).read_bytes())[2][0])[0]
                  for row in record['responses'] if '/PlanetoidMetadata' in row['url'])
    code = hashlib.sha256(Path(detail.__file__).read_bytes()+str(radius).encode()).hexdigest()[:20]
    cache_root = Path(cache_root) / code
    cache_root.mkdir(parents=True, exist_ok=True)
    nodes = {}
    for row in record['responses']:
        if '/BulkMetadata' not in row['url']:
            continue
        cache = cache_root / f'{row["sha256"]}.json'
        if cache.exists():
            parsed = json.loads(cache.read_text())
        else:
            # Bounds are decoded once independently of any quarter selection.
            parsed = detail.decoded_bulk((capture / row['file']).read_bytes(),
                shapely.box(-1e9, -1e9, 1e9, 1e9), radius)
            if parsed['errors']:
                raise ValueError(f'Cannot spatially select malformed metadata: {parsed["errors"]}')
            atomic_json(cache, parsed)
        nodes.update(parsed['nodes'])
    e0, n0, e1, n1 = record['extent_en']
    # Decoder uses a 20 m buffer; add 5 m conservative projection/bounds margin.
    allowed = {node for node, row in nodes.items()
        if row['bounds_en'][0] <= e1+25 and row['bounds_en'][2] >= e0-25
        and row['bounds_en'][1] <= n1+25 and row['bounds_en'][3] >= n0-25}
    selected = []
    for row in record['responses']:
        match = re.search(r'/NodeData/.*!1s([0-7]+)!2u', row['url'])
        if not match or len(match[1]) < 17 or match[1] in allowed:
            selected.append(row)
    return selected


def decode_extent(capture, library, limit, cache_root):
    import mesh
    from storage import atomic_json

    capture = Path(capture)
    record_path = capture / 'capture.json'
    original = json.loads(record_path.read_text())
    selected = response_selection(capture, cache_root)
    evidence = dict(method='metadata AABB intersects quarter plus 25 m; decoder clips at 20 m',
        algorithm_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        catalogued=len(original['responses']), selected=len(selected))
    # Decode through a separate input view; the complete capture stays immutable.
    view = capture / 'decode-input'
    (view / 'responses').mkdir(parents=True, exist_ok=True)
    for row in selected:
        target = view / row['file']
        if not target.exists():
            target.hardlink_to(capture / row['file'])
    atomic_json(view / 'capture.json', dict(original, responses=selected))
    product = capture / 'decoded'
    staged = view / 'decoded'
    # Reuse successful decoded bodies; an interrupted staging directory resumes.
    if product.exists() and not staged.exists():
        product.rename(staged)
    try:
        result = mesh.decode_capture(view, library, limit=limit)
    finally:
        if staged.exists():
            if product.exists():
                raise ValueError('Two decoded product directories for the same extent')
            staged.rename(product)
    result['response_selection'] = evidence
    atomic_json(capture / 'decoded/manifest.json', result)
    atomic_json(record_path, dict(original, decode_selection=evidence))
    return result
