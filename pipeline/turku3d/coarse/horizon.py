"""On-demand distant scenery from survey inputs, without generating a street tile.

The caller owns the output/cache. Raw surveys and retained facade descriptions are
read-only. Missing coverage stays missing; no downloads or invented city blocks.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from functools import lru_cache
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import shapely

from turku3d import config as C
from turku3d.package.gltf import GlbMesh, write_glb
from turku3d.spatial import frame

from . import extrusion, forests, inputs
from .sources import NoCoverage, Sources
from .horizon_identity import source_key


@lru_cache(maxsize=2048)
def _digest(path, size, modified):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def digest(path):
    p = Path(path)
    s = p.stat()
    return _digest(str(p), s.st_size, s.st_mtime_ns)


class SurveyScenery:
    """Catalogue cheap source identities; build geometry only for requested nodes."""

    def __init__(self, regional=None):
        self.sources = Sources()
        self.regional = str(Path(regional).resolve()) if regional else None
        self.workers = ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn"))
        # This service pins one immutable source overlay for its lifetime.
        for index in (self.sources.wfs, self.sources.ortho, self.sources.mosaics):
            index.MAX_AGE = float('inf')
        root = Path(__file__).parent
        dependencies = ["horizon.py", "horizon_nodes.py", "horizon_identity.py", "horizon_regional.py", "horizon_pack.mjs", "inputs.py", "extrusion.py", "roofs.py", "forests.py",
                        "sources.py", "../spatial.py", "../config.py", "../package/gltf.py",
                        "../buildings/source_surfaces.py"]
        from turku3d.facade_assets import root as facade_root
        self.appearance = {}
        for directory in (facade_root() / 'facade-params', C.data_root() / 'facade-params',
                          C.work_root() / 'facade-params'):
            for path in sorted(directory.glob('*.json')):
                try:
                    row = json.loads(path.read_text())
                    colour = row['description']['wall_colour']
                    self.appearance[str(row['building_key'])] = colour
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        recipe = ''.join(digest(root / p) for p in dependencies) + json.dumps(self.appearance, sort_keys=True)
        if self.regional: recipe += digest(self.regional)
        self.recipe = hashlib.sha256(recipe.encode()).hexdigest()

    @lru_cache(maxsize=4096)
    def describe(self, name):
        tile = C.VisualTile.parse(name)
        if tile.level >= 4 and self.regional:
            return self.regional_row(tile)
        if tile.level:
            from .horizon_nodes import describe
            return describe(self, tile)
        found, buildings = self.sources.wfs_source("bldg:Building_LOD2", tile)
        if not found:
            return self.regional_row(tile)
        try:
            ground, missing = self.sources.lidar_paths(tile)
        except NoCoverage:
            return self.regional_row(tile)
        if missing or not ground:
            return self.regional_row(tile)
        paths = {"ground": list(map(str, ground)), "buildings": str(buildings) if buildings else None}
        for kind, layer in (("water", "kanta:Vesikohde"), ("streets", "GIS:Katualueet")):
            _, path = self.sources.wfs_source(layer, tile)
            paths[kind] = str(path) if path else None
        osm = self.sources.osm_source(tile)
        paths["osm"] = str(osm) if osm else None
        files = ground + [Path(p) for p in paths.values() if isinstance(p, str)]
        hashes = {str(p): digest(p) for p in sorted(set(files))}
        key = source_key(self.recipe, name, paths, hashes)
        return dict(tile=name, key=key, kind="survey", inputs=paths, hashes=hashes,
                    source_sha256=key, h=[0, 120], recipe=self.recipe)

    def regional_row(self, tile):
        from .horizon_regional import covers
        if not self.regional or not covers(tile,self.regional): return None
        paths={'regional':self.regional}; hashes={self.regional:digest(self.regional)}
        key=source_key(self.recipe,tile.id,paths,hashes)
        return dict(tile=tile.id,key=key,kind='survey',inputs=paths,hashes=hashes,
                    source_sha256=key,h=[0,180],recipe=self.recipe,coverage=1,regional=tile.level>=4)

    def build(self, row, output):
        if row['recipe'] != self.recipe:
            raise ValueError('Horizon recipe changed during build')
        for path, expected in row["hashes"].items():
            if digest(path) != expected:
                raise ValueError("Horizon survey changed during build")
        # Python face/feature loops run in two bounded processes, not competing for one GIL.
        return self.workers.submit(produce, row, str(output), self.appearance).result()


def produce(row, output, appearance):
    output = Path(output)
    tile = C.VisualTile.parse(row["tile"])
    if isinstance(row["inputs"],dict) and "regional" in row["inputs"]:
        from .horizon_regional import build as build_regional
        meshes,stats=build_regional(tile,row["inputs"]["regional"])
    elif tile.level:
        from .horizon_nodes import build_node
        meshes, stats = build_node(tile, row["inputs"], appearance)
    else:
        meshes, stats = build(tile, row["inputs"], appearance)
    raw = output.with_suffix('.raw.glb')
    try:
        write_glb(meshes, raw, root_translation=tile.origin,
                  root_extras={"tile": tile.id, "scenery": True, "source": "survey"})
        subprocess.run(['node', str(Path(__file__).with_name('horizon_pack.mjs')), str(raw), str(output)],
                       check=True, capture_output=True, text=True, timeout=90)
        return {**stats, "bytes": output.stat().st_size}
    finally:
        raw.unlink(missing_ok=True)


def layer_mesh(layer, positions, faces, colours, origin):
    p = np.asarray(positions, np.float64).reshape(-1, 3)
    return GlbMesh(layer, p - origin, np.asarray(faces, np.uint32).reshape(-1, 3),
                   colors=np.asarray(colours, np.float32).reshape(-1, 3), color=(1, 1, 1, 1),
                   extras={"layer": layer, "scenery": True})


def shells(buildings, origin, appearance=None):
    """Survey roof planes and walls before facade tessellation; never emit floors."""
    positions, faces, colours = [], [], []
    for building in buildings:
        roof, ri, _, _, walls, _, wi = extrusion.extrude(building)
        # Far nodes use a quiet neutral pigment unless a retained reading exists.
        wall_colour = (.55, .51, .43)
        key = str(getattr(building, "facade_key", building.key))
        colour = (appearance or {}).get(key) or (appearance or {}).get('gis-' + key)
        if colour:
            from PIL import ImageColor
            try:
                rgb = np.asarray(ImageColor.getrgb(colour)[:3]) / 255
                wall_colour = tuple(np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4))
            except (ValueError, TypeError):
                pass
        for p, ix, colour in ((roof, ri, (.20, .23, .22)), (walls, wi, wall_colour)):
            if not len(ix):
                continue
            faces.extend(ix + len(positions))
            positions.extend(p)
            colours.extend([colour] * len(p))
    return layer_mesh("buildings", positions, faces, colours, origin) if faces else None


def polygons(path, tile):
    if not path:
        return shapely.Polygon()
    data = frame(Path(path), tile.bbox)
    return shapely.union_all(shapely.force_2d(shapely.make_valid(data.geometry_local.values)))


def terrain(tile, points, water, streets, woods):
    # Boundary samples are on the same world grid as adjacent tiles.
    xs = np.linspace(tile.origin[0], tile.origin[0] + tile.size, 17)
    zs = np.linspace(tile.origin[2], tile.origin[2] + tile.size, 17)
    heights = inputs.grid_heights(points, xs, zs)
    gx, gz = np.meshgrid(xs, zs)
    vertices = np.c_[gx.ravel(), heights.ravel(), gz.ravel()]
    faces = []
    for z in range(16):
        for x in range(16):
            a = z * 17 + x
            faces.extend(((a, a + 17, a + 1), (a + 1, a + 17, a + 18)))
    # Split colour boundaries per triangle so rivers cannot bleed into green banks.
    p = vertices[np.asarray(faces)].reshape(-1, 3)
    centres = p.reshape(-1, 3, 3).mean(axis=1)
    colours = np.tile((.24, .29, .16), (len(faces), 1))
    colours[shapely.contains_xy(woods, centres[:, 0], centres[:, 2])] = (.12, .19, .08)
    colours[shapely.contains_xy(streets, centres[:, 0], centres[:, 2])] = (.19, .20, .20)
    colours[shapely.contains_xy(water, centres[:, 0], centres[:, 2])] = (.10, .19, .24)
    mesh = layer_mesh("ground", p, np.arange(len(p)).reshape(-1, 3),
                      np.repeat(colours, 3, axis=0), np.asarray(tile.origin))
    from scipy.interpolate import RegularGridInterpolator
    interpolation = RegularGridInterpolator((zs, xs), heights, bounds_error=False, fill_value=None)
    return mesh, lambda x, z: float(interpolation([[z, x]])[0])


def canopy(tile, areas, blocked, height_at):
    """Opaque clusters only within mapped woodland; leave parks/open lawns alone."""
    positions, faces, colours = [], [], []
    for key, geometry, kind, _ in areas:
        if kind not in ("forest", "wood", "scrub"):
            continue
        seed = int.from_bytes(hashlib.sha256(f"{tile.id}:{key}".encode()).digest()[:4], "little")
        for x, z in forests.scatter(geometry, .007, blocked, seed, spacing=10, limit=256):
            height = 5 if kind == "scrub" else 12
            y = height_at(x, z)
            base = len(positions)
            positions.append([x, y + height * .2, z])
            for level in (.38, .76):
                for s in range(6):
                    angle = s * np.pi / 3
                    positions.append([x + 5 * np.cos(angle), y + height * level, z + 5 * np.sin(angle)])
            positions.append([x, y + height, z])
            colours.extend([(.28, .37, .13)] * 14)
            for s in range(6):
                n = (s + 1) % 6
                faces.extend([[base, base + 1 + s, base + 1 + n],
                              [base + 1 + s, base + 7 + s, base + 7 + n],
                              [base + 1 + s, base + 7 + n, base + 1 + n],
                              [base + 13, base + 7 + n, base + 7 + s]])
    return layer_mesh("vegetation", positions, faces, colours, np.asarray(tile.origin)) if faces else None


def build(tile, paths, appearance=None):
    points = inputs.ground_points(tile, list(map(Path, paths["ground"])))
    buildings = inputs.buildings(Path(paths["buildings"]), tile) if paths["buildings"] else []
    water, streets = (polygons(paths.get(kind), tile) for kind in ("water", "streets"))
    areas = forests.read_areas(tile, Path(paths["osm"])) if paths.get("osm") else []
    woods = shapely.union_all([g for _, g, kind, _ in areas if kind in ("forest", "wood", "scrub")])
    ground, height_at = terrain(tile, points, water, streets, woods)
    blocked = shapely.union_all([water.buffer(5), streets.buffer(5),
                                *[b.footprint.buffer(5) for b in buildings],
                                *[g for _, g, kind, _ in areas if kind == "open"]])
    meshes = [m for m in (ground, shells(buildings, np.asarray(tile.origin), appearance),
                           canopy(tile, areas, blocked, height_at)) if m is not None]
    return meshes, dict(triangles=sum(len(m.indices) for m in meshes), buildings=len(buildings), draws=len(meshes))
