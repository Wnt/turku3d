"""City WFS layers by area.

A read covers its box with the 500 m blocks of the city's lidar grid. A block comes from the read-only raw tree
when an area there holds the layer for the whole block, and otherwise from the shared cache, fetched on demand
(sources/wfs/<layer>/<xmin>_<ymin>.parquet). The GML paging and parsing are those of turku3d.fetch.wfs.
"""

from __future__ import annotations

import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np
import shapely

from ... import config
from ...fetch import wfs as gml
from ...fetch.common import http_get
from ...fetch.sources import WFS_FEATURE_CAP, WFS_LAYERS, WFS_URL

BLOCK = 500.0
LAYERS = {layer.name: layer for layer in WFS_LAYERS}
STRUCTURAL = {"layer", "feature_key", "gml_id", "geom_index", "geometry", "geom_type", "geom_role", "surface_type",
              "part_id", "surface_id", "geometry_local"}


def text(value) -> str:
    return value if isinstance(value, str) else ""


@dataclass
class Geom:
    shape: shapely.Geometry  # EPSG:3877, with N2000 Z when the source has it
    type: str
    role: str
    surface_type: str
    surface_id: str


@dataclass
class Feature:
    key: str
    gml_id: str
    attrs: dict[str, str]
    geoms: list[Geom]
    source: dict  # {"source": "turku-wfs:<layer>", "key": ...}


class Wfs:
    def __init__(self, cache: Path, sources):
        self.cache = cache
        self.sources = sources
        self.frames: dict[Path, object] = {}
        self.fetched: list[str] = []

    @cached_property
    def raw_areas(self) -> list[Path]:
        root = config.raw_dir("wfs")
        return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []

    def raw_cover(self, slug: str) -> list[tuple[tuple[float, float, float, float], Path]]:
        """Raw areas that hold this layer: (bbox, parquet)."""
        cached = self.__dict__.setdefault("_cover", {})
        if slug not in cached:
            rows = []
            for area in self.raw_areas:
                summary, parquet = area / f"{slug}.summary.json", area / f"{slug}.parquet"
                if summary.exists() and parquet.exists():
                    meta = json.loads(summary.read_bytes())
                    if meta.get("status") == "ok" and meta.get("bbox_en"):
                        rows.append((tuple(meta["bbox_en"]), parquet))
            cached[slug] = rows
        return cached[slug]

    def block_path(self, layer: str, x0: float, y0: float) -> Path:
        """The parquet that holds a layer for one block, fetched when no source has it yet."""
        slug = LAYERS[layer].slug
        for box, parquet in self.raw_cover(slug):
            if box[0] <= x0 and box[1] <= y0 and box[2] >= x0 + BLOCK and box[3] >= y0 + BLOCK:
                return parquet
        path = self.cache / slug / f"{int(x0)}_{int(y0)}.parquet"
        if not path.exists():
            self.fetch_block(layer, (x0, y0, x0 + BLOCK, y0 + BLOCK), path)
        return path

    def fetch_block(self, layer: str, box, path: Path) -> None:
        spec = LAYERS[layer]
        with tempfile.TemporaryDirectory() as scratch:
            pages, stack = [], [tuple(box)]
            while stack:
                bb = stack.pop()
                body, _ = http_get(WFS_URL, gml.request_params(spec, bb))
                gml.check_body(body, layer)
                if gml.count_features(body) >= WFS_FEATURE_CAP and bb[2] - bb[0] > gml.MIN_PAGE_M:
                    stack.extend(gml.split4(bb))
                    continue
                page = Path(scratch) / f"{gml.page_key(bb)}.gml"
                page.write_bytes(body)
                pages.append(page)
            frame, count = gml.pages_to_frame(sorted(pages), layer)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        frame.drop(columns=["geometry_local"]).to_parquet(tmp)
        tmp.replace(path)
        self.fetched.append(f"{layer} {path.stem} ({count} features)")
        self.sources.log(f"  fetched {layer} block {path.stem}: {count} features")

    def frame(self, path: Path):
        if path not in self.frames:
            import geopandas as gpd

            self.frames[path] = gpd.read_parquet(path)
        return self.frames[path]

    def features(self, layer: str, box: tuple[float, float, float, float]) -> list[Feature]:
        """Whole features of a layer that intersect the survey box (e0, n0, e1, n1), each once."""
        with self.sources.timed(f"wfs:{layer}"):
            blocks = [(float(x), float(y)) for x in np.arange(np.floor(box[0] / BLOCK) * BLOCK, box[2], BLOCK)
                      for y in np.arange(np.floor(box[1] / BLOCK) * BLOCK, box[3], BLOCK)]
            with ThreadPoolExecutor(max_workers=8) as pool:  # missing blocks wait on the server, not on this machine
                paths = list(dict.fromkeys(pool.map(lambda block: self.block_path(layer, *block), blocks)))
            query = shapely.box(*box)
            out: dict[str, Feature] = {}
            for path in paths:
                frame = self.frame(path)
                if not len(frame):
                    continue
                hit = frame.geometry.values.intersects(query)
                keys = set(frame.feature_key.values[hit])
                rows = frame[frame.feature_key.isin(keys - out.keys())]
                columns = [c for c in rows.columns if c not in STRUCTURAL]
                for key, group in rows.groupby("feature_key", sort=False):
                    first = group.iloc[0]
                    attrs = {c: first[c] for c in columns if isinstance(first[c], str) and first[c] != ""}
                    geoms = [Geom(g, text(t), text(r), text(s), text(i)) for g, t, r, s, i in zip(
                        group.geometry.values, group["geom_type"].values, group["geom_role"].values,
                        group["surface_type"].values, group["surface_id"].values)]
                    gml_id = text(first["gml_id"])
                    out[key] = Feature(key, gml_id, attrs, geoms, {"source": f"turku-wfs:{layer}", "key": gml_id or key})
            return list(out.values())
