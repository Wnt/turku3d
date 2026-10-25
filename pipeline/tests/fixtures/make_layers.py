"""Explicit synthetic layer generator for the packaging/Chrome integration check. Never a build fallback."""
import argparse
import io
from pathlib import Path

import numpy as np
from PIL import Image

from turku3d import config as C
from turku3d.layers import CollisionLayer, write_collision, write_visual, write_manifest
from turku3d.package.gltf import GlbMesh


def generate(root: Path):
    directory = root / 'ground'
    files = []
    stream = io.BytesIO()
    # Broad diagnostic color bands survive distance mipmaps; pixel noise averages to flat gray.
    y, x = np.mgrid[:256, :256]
    pixels = np.stack([128 + 105*np.sin(x/8)*np.cos(y/16),
                       128 + 105*np.cos(x/11+y/13), 128 + 105*np.sin(y/9)], axis=-1).astype(np.uint8)
    Image.fromarray(pixels).save(stream, format='PNG')
    texture = stream.getvalue()
    indices = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    for tile in C.OFFICE_BLOCK.visual_tiles:
        for col in tile.collision_tiles():
            x, _, z = col.origin; s = C.COLLISION_TILE_SIZE
            positions = np.array([[x, 2., z], [x+s, 2., z], [x, 2., z+s], [x+s, 2., z+s]])
            name = col.id + '.col.npz'
            write_collision(directory / name, CollisionLayer(positions, indices, np.ones(2, np.uint8)))
            files.append(name)
        s = tile.size
        positions = np.array([[0., 2, 0], [s, 2, 0], [0, 2, s], [s, 2, s]])
        mesh = GlbMesh(tile.id, positions, indices, uvs=np.array([[0,0],[1,0],[0,1],[1,1]], np.float32), image=texture,
                       extras=dict(layer='ground', object_id='ground_'+tile.id, material_key='synthetic', texture_uv='normalized'))
        name = tile.id + '.vis.glb'
        write_visual(directory / name, [mesh], root_translation=tile.origin)
        files.append(name)
    write_manifest(directory, inputs={'fixture':'synthetic-v2'}, params={}, code='synthetic-v2', tiles=files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    generate(parser.parse_args().out)
