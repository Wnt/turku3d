"""Visual-only massing around the playable tiles, built per L0 tile (turku3d.tile_layer).

The ring is every unlisted L0 tile within RING tiles of a listed one (for the four core tiles,
exactly work-3x3). A ring tile's massing is the city's CityGML LOD2 buildings whose footprint
centroid lies in it, extruded from the footprint base to the area-weighted mean roof height,
except buildings within EDGE_M of a listed tile, which the buildings layer owns. LOD2 comes
from any prepared raw area covering the tile (work-3x3, tile-<id>, the draft prefetcher's
blocks); `fetch_missing` reads it through for ring tiles that have none."""

from __future__ import annotations

import json
from pathlib import Path

import shapely

from turku3d import config as C
from turku3d import tile_layer
from turku3d.facade_kit import parameters
from turku3d.layers import write_visual
from turku3d.package.gltf import GlbMesh
from turku3d.package.manifest import _atomic_write

RING = 2
EDGE_M = 1.0
LOD2 = "bldg_Building_LOD2"


def ring(listed) -> list[C.VisualTile]:
    tiles = {C.VisualTile.parse(t) if isinstance(t, str) else t for t in listed}
    around = {
        C.VisualTile(0, t.i + di, t.j + dj)
        for t in tiles
        for di in range(-RING, RING + 1)
        for dj in range(-RING, RING + 1)
    }
    return sorted(around - tiles)


def lod2_source(tile) -> Path | None:
    """A complete LOD2 parquet of a prepared area whose bounds cover the tile."""
    t = tile.bbox.to_en()
    for summary in sorted(C.raw_dir("wfs").glob(f"*/{LOD2}.summary.json")):
        s = json.loads(summary.read_text())
        e0, n0, e1, n1 = s.get("bbox_en", (0, 0, 0, 0))
        parquet = summary.with_name(f"{LOD2}.parquet")
        if (
            s.get("status") == "ok"
            and parquet.is_file()
            and e0 <= t.emin
            and n0 <= t.nmin
            and e1 >= t.emax
            and n1 >= t.nmax
        ):
            return parquet
    return None


def fetch_missing(listed, log=print) -> list[str]:
    """Read through the LOD2 layer of ring tiles without one, as their `tile-<id>` area."""
    from turku3d.fetch import wfs
    from turku3d.fetch.sources import WFS_LAYERS

    layer = next(item for item in WFS_LAYERS if item.slug == LOD2)
    fetched = []
    for tile in ring(listed):
        if lod2_source(tile) is None:
            wfs.fetch_layer(layer, C.tile_input_area(tile), log=log)
            fetched.append(tile.id)
    return fetched


def listed_near(tile) -> list[str]:
    listed = set(C.playable_tile_ids())
    return sorted(
        n.id
        for di in (-1, 0, 1)
        for dj in (-1, 0, 1)
        if (n := C.VisualTile(0, tile.i + di, tile.j + dj)).id in listed
    )


def code_hash():
    base = Path(__file__).parents[1]
    return tile_layer.code_hash(
        Path(__file__),
        base / "coarse/build.py",
        base / "coarse/inputs.py",
        base / "facade_kit.py",
        C.REPO_ROOT / "textures/turkutex/facade_fast_render.py",
        base / "tile_layer.py",
        base / "config.py",
        base / "layers.py",
        base / "package/gltf.py",
    )


def tile_key(tile):
    source = lod2_source(tile)
    return {
        "lod2": tile_layer.identities({"lod2": source})["lod2"] if source else None,
        "listed_near": listed_near(tile),
    }


def build_tile(tile, staging):
    from turku3d.coarse.build import extrude
    from turku3d.coarse.inputs import buildings

    source = lod2_source(tile)
    if source is None:
        return {"records": [], "metrics": {"buildings": 0, "missing_lod2": True}}
    listed = shapely.union_all(
        [shapely.box(*C.VisualTile.parse(t).bbox.as_tuple()) for t in listed_near(tile)]
    )
    meshes, records = [], []
    for b in buildings(source, tile):
        if not listed.is_empty and b.footprint.distance(listed) < EDGE_M:
            continue
        roof, roof_idx, _, _, walls, wall_uv, wall_idx = extrude(b)
        ident = f"outer-lod2-{b.key}"
        params = parameters(
            b.facade_key, b.top - b.base, max(float(wall_uv[:, 0].max()), 1.0), b.attributes
        )
        for suffix, positions, indices, uvs, material, extra in (
            ("walls", walls, wall_idx, wall_uv, "facade_kit", {"facade_kit": params}),
            ("roof", roof, roof_idx, roof[:, [0, 2]], "roof_metal_gray", {}),
        ):
            meshes.append(
                GlbMesh(
                    f"{ident}/{suffix}",
                    positions - tile.origin,
                    indices,
                    uvs=uvs,
                    color=(0.8, 0.8, 0.8, 1.0),
                    extras={
                        "layer": "buildings_outer",
                        "object_id": f"{ident}/{suffix}",
                        "building_id": b.facade_key,
                        "material_key": material,
                        "surface": suffix,
                        "lidar_massing": False,
                        **extra,
                    },
                )
            )
        records.append({"source_id": b.key, "base": b.base, "top": b.top})
    if meshes:
        write_visual(
            Path(staging) / f"{tile.id}.vis.glb",
            meshes,
            root_translation=tile.origin,
            root_extras={"layer": "buildings_outer", "tile": tile.id},
        )
    return {"records": records, "metrics": {"buildings": len(records), "source": str(source)}}


def combine(directory, tiles):
    metrics = {
        "buildings": sum(e["metrics"]["buildings"] for e in tiles.values()),
        "by_tile": {tid: e["metrics"]["buildings"] for tid, e in tiles.items()},
        "missing_lod2": sorted(t for t, e in tiles.items() if e["metrics"].get("missing_lod2")),
    }
    _atomic_write(
        Path(directory) / "metrics.json",
        (json.dumps(metrics, indent=2, sort_keys=True) + "\n").encode(),
    )
    return {"visual_only": True, "ring": RING, "height_source": "CityGML LOD2 mean roof"}


PRODUCER = tile_layer.Producer("buildings_outer", code_hash, tile_key, build_tile, combine)


def main(argv=None):
    import argparse

    from turku3d.layers import layer_root

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true", help="read through missing LOD2 first")
    args = ap.parse_args(argv)
    listed = C.playable_tile_ids()
    if args.fetch:
        fetch_missing(listed)
    result = tile_layer.run(layer_root() / "buildings_outer", PRODUCER, ring(listed))
    print(json.dumps({k: v for k, v in result.items() if k != "tile_seconds"}, indent=2))


if __name__ == "__main__":
    main()
