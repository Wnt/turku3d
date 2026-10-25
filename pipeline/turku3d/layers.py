"""Shared, pickle-free layer interchange. See docs/specs/layers.md for the v1 contract."""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.col.materials import is_shippable
from turku3d.package.gltf import GlbMesh, build_glb, read_glb
from turku3d.package.manifest import _atomic_write

PRIMITIVE_DTYPE = np.dtype([
    ("kind", "U8"), ("center", "<f8", (3,)), ("half_extents", "<f8", (3,)),
    ("radius", "<f8"), ("height", "<f8"), ("yaw", "<f8"), ("material", "u1"),
])


@dataclass
class CollisionLayer:
    positions: np.ndarray
    indices: np.ndarray
    material: np.ndarray
    primitives: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=PRIMITIVE_DTYPE))

    def validate(self) -> None:
        for name, dtype, shape in (("positions", np.dtype("float64"), (3,)),
                                   ("indices", np.dtype("uint32"), (3,)),
                                   ("material", np.dtype("uint8"), ())):
            a = getattr(self, name)
            if a.dtype != dtype or a.shape[1:] != shape or a.ndim != len(shape) + 1:
                raise ValueError(f"{name}: expected {dtype} (N,{shape})")
        if not np.isfinite(self.positions).all():
            raise ValueError("positions must be finite")
        if len(self.indices) != len(self.material):
            raise ValueError("one material per triangle required")
        if self.indices.size and self.indices.max() >= len(self.positions):
            raise ValueError("index out of range")
        if self.primitives.dtype != PRIMITIVE_DTYPE or self.primitives.ndim != 1:
            raise ValueError("primitives must use PRIMITIVE_DTYPE")
        for mid in np.unique(np.concatenate([self.material, self.primitives['material']])):
            if not is_shippable(int(mid)):
                raise ValueError(f"unshippable material {mid}")
        for p in self.primitives:
            if p['kind'] not in ('box', 'capsule', 'cylinder'):
                raise ValueError(f"unknown primitive {p['kind']}")
            if not all(np.isfinite(p[k]).all() for k in ('center', 'half_extents', 'radius', 'height', 'yaw')):
                raise ValueError("primitive values must be finite")
            if p['kind'] == 'box':
                valid = (p['half_extents'] > 0).all()
            else:
                valid = p['radius'] > 0 and p['height'] > 0
            if not valid:
                raise ValueError("primitive dimensions must be positive")


def layer_root() -> Path:
    return C.work_root() / 'build' / 'layers'


def write_collision(path: str | Path, layer: CollisionLayer) -> None:
    layer.validate()
    out = io.BytesIO()
    np.savez_compressed(out, positions=layer.positions, indices=layer.indices,
                        material=layer.material, primitives=layer.primitives)
    _atomic_write(Path(path), out.getvalue())


def read_collision(path: str | Path) -> CollisionLayer:
    with np.load(path, allow_pickle=False) as a:
        layer = CollisionLayer(a['positions'], a['indices'], a['material'],
                               a['primitives'] if 'primitives' in a else np.empty(0, PRIMITIVE_DTYPE))
    layer.validate()
    return layer


def read_visual(path: str | Path) -> bytes:
    data = Path(path).read_bytes()
    js, _ = read_glb(data)
    if js['asset']['version'] != '2.0' or any('uri' in b for b in js['buffers']):
        raise ValueError('visual layers must be self-contained GLB 2.0')
    for node in js.get('nodes', []):
        if 'mesh' in node:
            extras = node.get('extras', {})
            if not all(isinstance(extras.get(k), str) and extras[k] for k in ('layer', 'object_id', 'material_key')):
                raise ValueError('mesh nodes require layer, object_id and material_key extras')
    if any('uri' in im for im in js.get('images', [])):
        raise ValueError('visual images must be embedded')
    return data


def write_visual(path: str | Path, meshes: list[GlbMesh], **kwargs) -> None:
    """Meshes are world-local, or tile-local with root_translation=tile.origin."""
    for mesh in meshes:
        if not all(isinstance(mesh.extras.get(k), str) and mesh.extras[k]
                   for k in ('layer', 'object_id', 'material_key')):
            raise ValueError('mesh extras require layer, object_id and material_key')
    _atomic_write(Path(path), build_glb(meshes, **kwargs))


def write_manifest(directory: str | Path, *, inputs: dict, params: dict, code: str,
                   tiles: list[str]) -> dict:
    """Publish last; tiles lists relative .col.npz/.vis.glb filenames, not bare ids."""
    directory = Path(directory)
    files = {}
    for name in sorted(set(tiles)):
        _layer_file(name)
        data = (directory / name).read_bytes()
        files[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    manifest = dict(format='turku3d-layer', version=1, layer=directory.name,
                    inputs=inputs, params=params, code=code, tiles=files)
    _atomic_write(directory / 'MANIFEST.json', (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode())
    return manifest


def _layer_file(name: str) -> None:
    if Path(name).name != name:
        raise ValueError('layer filenames must not contain paths')
    if name.endswith('.col.npz'):
        C.CollisionTile.parse(name.removesuffix('.col.npz'))
    elif name.endswith('.vis.glb'):
        C.VisualTile.parse(name.removesuffix('.vis.glb'))
    else:
        raise ValueError(f'unsupported layer file {name}')


def read_manifest(directory: str | Path, *, verify: bool = True) -> dict:
    directory = Path(directory)
    m = json.loads((directory / 'MANIFEST.json').read_text())
    if m.get('format') != 'turku3d-layer' or m.get('version') != 1 or m.get('layer') != directory.name:
        raise ValueError('expected turku3d-layer v1 matching directory name')
    if not isinstance(m.get('inputs'), dict) or not isinstance(m.get('params'), dict) or not m.get('code'):
        raise ValueError('manifest requires inputs, params and code')
    for name, entry in m['tiles'].items():
        _layer_file(name)
        if verify:
            data = (directory / name).read_bytes()
            if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError(f'layer hash/size mismatch: {directory / name}')
    return m
