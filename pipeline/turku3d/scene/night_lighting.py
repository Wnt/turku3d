"""Bake direct street illumination after final receiver tessellation, before release hashing.

The finite reflector is an explicit visual photometric prior, not measured lux.
Solid prepared geometry supplies occlusion, including real posts and retaining walls.
"""
import hashlib
import json
from pathlib import Path
import subprocess


def bake_scene(scene):
    helper = Path(__file__).with_name('night_bake.mjs')
    result = subprocess.run(['node', '--experimental-wasm-modules', str(helper)],
        input=json.dumps(scene), text=True, capture_output=True, check=True, timeout=120)
    product = json.loads(result.stdout)
    for mesh in scene['meshes']:
        values = product['values'].get(mesh['id'])
        if values is not None:
            mesh['nightIrradiance'] = values
    scene['sources'].append(dict(id=scene['id']+':street-irradiance', kind='compiled-street-irradiance',
        sha256=hashlib.sha256(helper.read_bytes()+Path(__file__).read_bytes()).hexdigest(),
        uncertaintyM=.01))
    return product['audit']
