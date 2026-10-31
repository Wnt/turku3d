"""Imagery admission/download policy for unpublished full tiles; no producer changes.

Never repair shared coarse mosaics in place: a neighbouring warmed tile may hash every
file in that mosaic. New imagery gets a tile-sized, earlier-sorting VRT instead.
"""

import dataclasses
import json
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from turku3d import config as C
from turku3d.fetch import wms
from turku3d.fetch.common import atomic_write_text
from turku3d.fetch.sources import WMS_LAYERS

from .retention import tile_lock
from .sources import ORTHO_PX, ORTHO_SLUG, NoCoverage, Sources, block_tiles, content_paths


def full_exists(tile, root):
    for name in ("manifest.json", "last-full.json"):
        path = root / tile.id / name
        if path.exists():
            # A corrupt pointer is protected too; input repair must not replace it.
            try:
                if json.loads(path.read_text()).get("pass_kind") == "full":
                    return True
            except (OSError, ValueError):
                return True
    return False


@lru_cache(maxsize=4096)
def _blank(path, bounds, signature):
    import numpy as np
    import rasterio
    from rasterio.windows import from_bounds

    with rasterio.open(path) as source:
        window = from_bounds(*bounds, source.transform)
        rgb = source.read((1, 2, 3), window=window)
        valid = source.dataset_mask(window=window) > 0
    return not valid.any() or bool(np.all(rgb[:, valid] >= 254))


def blank(tile, path):
    try:
        signature = tuple(
            (str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in content_paths([path])
        )
        return _blank(str(path), tile.bbox.to_en().as_tuple(), signature)
    except OSError:
        return True  # A pruned or incomplete mosaic is not imagery for a new tile.


class ImagerySources(Sources):
    """Existing full tiles keep their original source selection and input identity."""

    def __init__(self, root=None):
        super().__init__()
        self.root = Path(root) if root is not None else C.work_root() / "coarse"

    def ortho_source(self, tile):
        protected = full_exists(tile, self.root)
        try:
            path = super().ortho_source(tile)
        except NoCoverage:
            if protected:
                raise
            return None  # Try the newer imagery before declaring this tile unavailable.
        if path is not None and not protected and blank(tile, path):
            return None
        return path


def area(tile):
    return C.Area(f"00-new-{tile.id}", f"imagery for new tile {tile.id}", tile.bbox, 0.0)


def paths(block):
    result = []
    for tile in block_tiles(block):
        name = area(tile).name
        for slug in (ORTHO_SLUG, "ortho2025"):
            root = C.raw_dir("ortho") / slug
            result.extend(
                [
                    root / name,
                    *(
                        root / f"{name}{suffix}"
                        for suffix in (".vrt", ".chunks.json", ".summary.json")
                    ),
                ]
            )
    return result


def fetch(block, log=print, root=None, repair_tiles=()):
    """Fetch new imagery; only explicitly selected repairs may change a full tile's source."""
    sources = ImagerySources(root)
    repair_tiles = set(repair_tiles)
    primary = dataclasses.replace(
        next(layer for layer in WMS_LAYERS if layer.slug == ORTHO_SLUG),
        px_m=C.VISUAL_TILE_SIZE / ORTHO_PX,
    )
    fallback = dataclasses.replace(
        next(layer for layer in WMS_LAYERS if layer.slug == "ortho2025"), px_m=primary.px_m
    )
    for tile in block_tiles(block):
        # Serialize with generation and external publication, including the final recheck.
        with tile_lock(sources.root / tile.id):
            repair = tile.id in repair_tiles
            if full_exists(tile, sources.root) and not repair:
                continue
            path = sources.ortho_source(tile)
            if path is not None and (not repair or not blank(tile, path)):
                continue
            target = area(tile)
            for layer in (primary, fallback):
                summary = wms.fetch_layer(layer, target, workers=1, log=log, chunk_px=ORTHO_PX)
                if summary["errors"]:
                    raise RuntimeError(f"WMS {layer.name} {tile.id}: {summary['errors'][:2]}")
                if not summary["chunks_ok"]:
                    continue
                if layer.slug != ORTHO_SLUG:
                    # The unchanged source reader discovers mosaics under the primary slug.
                    # Only the VRT/metadata live there; imagery and provenance remain in 2025.
                    origin = C.raw_dir("ortho") / layer.slug
                    dest = C.raw_dir("ortho") / ORTHO_SLUG
                    for suffix in (".chunks.json", ".summary.json", ".vrt"):
                        text = (origin / (target.name + suffix)).read_text()
                        if suffix == ".vrt":
                            tree = ET.fromstring(text)
                            for node in tree.iter("SourceFilename"):
                                if node.get("relativeToVRT") == "1":
                                    node.text = str(origin / node.text)
                                    node.set("relativeToVRT", "0")
                            text = ET.tostring(tree, encoding="unicode")
                        atomic_write_text(dest / (target.name + suffix), text)
                sources.invalidate()
                selected = sources.ortho_source(tile)
                if selected is not None and not blank(tile, selected):
                    break
            else:
                raise NoCoverage(f"{tile.id}: neither 2022 nor 2025 has usable aerial imagery")
