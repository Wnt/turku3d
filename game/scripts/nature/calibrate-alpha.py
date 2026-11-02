"""Preserve foliage cutout coverage per atlas cell in the encoded mip chain.

Build dependency: Python numpy/Pillow and KTX-Software's ktx/toktx on PATH.
Run after the nature builder; geometry and all other textures remain byte-identical.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

import numpy as np
from PIL import Image


def calibrate(source, output):
    base = np.array(Image.open(source / 'output_level0.png').convert('RGBA'))
    size = base.shape[0]
    targets = np.array([[(base[y*size//4:(y+1)*size//4, x*size//4:(x+1)*size//4, 3] >= 128).mean()
                         for x in range(4)] for y in range(4)])
    levels = []
    for level in range(size.bit_length()):
        image = np.array(Image.open(source / f'output_level{level}.png').convert('RGBA'))
        width = image.shape[0]
        grid = 4 if width >= 4 else 1
        for y in range(grid):
            for x in range(grid):
                region = image[y*width//grid:(y+1)*width//grid, x*width//grid:(x+1)*width//grid, 3]
                target = targets[y, x] if grid == 4 else float((base[:, :, 3] >= 128).mean())
                values = region.astype(float)
                count = max(0, min(values.size, round(target * values.size)))
                positive = np.sort(values[values > 0])[::-1]
                scales = [1.0]
                if level and len(positive):
                    for k in {max(1, count), min(len(positive), count+1), len(positive)}:
                        if 0 < k <= len(positive):
                            scales.append(127.5 / positive[k-1] + 1e-7)
                    scales.append(0.0)
                def correction(scale):
                    return np.clip(np.floor(values * scale + .5), 0, 255).astype(np.uint8)
                scale = min(scales, key=lambda s: (abs(float((correction(s) >= 128).mean())-target), abs(s-1)))
                region[:] = correction(scale)
        target_path = output / f'level{level}.png'
        Image.fromarray(image).save(target_path)
        levels.append(str(target_path))
    return levels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    glb_path = args.directory / 'nature.glb'
    manifest_path = args.directory / 'nature.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest['textures']['nature_foliage'].get('alphaCoverage') == 'per-cell-v1':
        return
    raw = glb_path.read_bytes()
    json_size = struct.unpack_from('<I', raw, 12)[0]
    doc = json.loads(raw[20:20+json_size])
    binary = raw[28+json_size:]
    image = next(image for image in doc['images'] if image.get('name') == 'nature_foliage')
    view = doc['bufferViews'][image['bufferView']]
    start, length = view.get('byteOffset', 0), view['byteLength']
    with tempfile.TemporaryDirectory(prefix='nature-alpha-') as scratch:
        work = Path(scratch)
        encoded = work / 'source.ktx2'
        encoded.write_bytes(binary[start:start+length])
        decoded = work / 'decoded'
        decoded.mkdir()
        subprocess.run(['ktx', 'extract', '--transcode', 'rgba8', '--level', 'all', str(encoded), str(decoded)], check=True)
        levels = calibrate(decoded, work)
        result = work / 'foliage.ktx2'
        subprocess.run(['toktx', '--t2', '--mipmap', '--assign_oetf', 'srgb', '--encode', 'etc1s',
                        '--clevel', '2', '--qlevel', '200', '--threads', '2', str(result), *levels], check=True)
        texture = result.read_bytes()
    # Meshopt views point into buffer0 through their extension; fallback buffer1 is virtual.
    old_end = (start + length + 3) & ~3
    replacement = texture + b'\0' * (-len(texture) % 4)
    delta = len(replacement) - (old_end-start)
    for item in doc['bufferViews']:
        for location in [item, item.get('extensions', {}).get('EXT_meshopt_compression', {})]:
            if location.get('buffer') == 0 and location.get('byteOffset', 0) >= old_end:
                location['byteOffset'] += delta
    view['byteLength'] = len(texture)
    binary = binary[:start] + replacement + binary[old_end:]
    doc['buffers'][0]['byteLength'] += delta
    encoded_json = json.dumps(doc, separators=(',', ':')).encode()
    encoded_json += b' ' * (-len(encoded_json) % 4)
    updated = (struct.pack('<III', 0x46546C67, 2, 28+len(encoded_json)+len(binary))
               + struct.pack('<II', len(encoded_json), 0x4E4F534A) + encoded_json
               + struct.pack('<II', len(binary), 0x004E4942) + binary)
    manifest['glb'] = {'bytes': len(updated), 'sha256': hashlib.sha256(updated).hexdigest()}
    manifest['textures']['nature_foliage'].update(bytes=len(texture), alphaCoverage='per-cell-v1')
    glb_path.write_bytes(updated)
    manifest_path.write_text(json.dumps(manifest, indent=1) + '\n')
    print(f'Calibrated foliage mip coverage: {len(texture)} bytes; geometry unchanged')


if __name__ == '__main__':
    main()
