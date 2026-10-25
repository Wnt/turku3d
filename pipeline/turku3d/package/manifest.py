"""dist/tiles/manifest.json + tileset.json (docs/specs/tiles.md §4, §5).

Packaging steps register files with `DistTree.add_visual` / `add_collision`, then `commit()` rewrites
tileset.json from all visual entries and writes manifest.json last via an atomic rename. Entries from
earlier builds (other tiles) are kept, so `make tile T=a` then `make tile T=b` gives one tree with both.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from turku3d import config as C

MANIFEST_FORMAT = "turku3d-manifest"
MANIFEST_VERSION = 1
GEOMETRIC_ERROR = {0: 0.0, 1: 16.0, 2: 64.0, 3: 256.0}
ROOT_ERROR = 512.0


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git_sha() -> str:
    try:
        return (
            subprocess.run(
                ["git", "-C", str(C.REPO_ROOT), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            or "nogit"
        )
    except Exception:
        return "nogit"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def tile_box(tile: C.VisualTile, h: tuple[float, float], bounds=None) -> list[float]:
    """3D Tiles frame box (Z-up, Y = north) for tile tL_i_j and height range h."""
    if bounds is not None:
        lo, hi = bounds
        return [
            (lo[0] + hi[0]) / 2,
            -(lo[2] + hi[2]) / 2,
            (lo[1] + hi[1]) / 2,
            max(0.001, (hi[0] - lo[0]) / 2),
            0,
            0,
            0,
            max(0.001, (hi[2] - lo[2]) / 2),
            0,
            0,
            0,
            max(1.0, (hi[1] - lo[1]) / 2),
        ]
    s = tile.size
    hmin, hmax = float(h[0]), float(h[1])
    hz = max(1.0, (hmax - hmin) / 2)
    return [
        (tile.i + 0.5) * s,
        -(tile.j + 0.5) * s,
        (hmin + hmax) / 2,
        s / 2,
        0,
        0,
        0,
        s / 2,
        0,
        0,
        0,
        hz,
    ]


def build_tileset(visual: dict, generator: str) -> dict:
    """Quadtree with available 500 m / 1 km parents; missing children remain absent."""
    children = []
    for tid in sorted(visual):
        e = visual[tid]
        t = C.VisualTile.parse(tid)
        box = tile_box(t, e["h"], e.get("bounds"))
        children.append(
            {
                "boundingVolume": {"box": box},
                "geometricError": GEOMETRIC_ERROR[t.level],
                "content": {"uri": e["url"]},
                "extras": {"id": tid},
            }
        )
    nodes = {node["extras"]["id"]: node for node in children}
    roots = []
    for tid, node in nodes.items():
        node["refine"] = "REPLACE"
        parent = C.VisualTile.parse(tid).parent()
        if parent and parent.id in nodes:
            nodes[parent.id].setdefault("children", []).append(node)
        else:
            roots.append(node)
    children = roots

    def union_box(items):
        lo = [min(b[k] - b[(3, 7, 11)[k]] for b in items) for k in range(3)]
        hi = [max(b[k] + b[(3, 7, 11)[k]] for b in items) for k in range(3)]
        return [
            (lo[0] + hi[0]) / 2,
            (lo[1] + hi[1]) / 2,
            (lo[2] + hi[2]) / 2,
            (hi[0] - lo[0]) / 2,
            0,
            0,
            0,
            (hi[1] - lo[1]) / 2,
            0,
            0,
            0,
            (hi[2] - lo[2]) / 2,
        ]

    def expand(node):
        for child in node.get("children", []):
            expand(child)
        node["boundingVolume"]["box"] = union_box(
            [node["boundingVolume"]["box"]]
            + [c["boundingVolume"]["box"] for c in node.get("children", [])]
        )

    for node in children:
        expand(node)
    root_box = (
        union_box([n["boundingVolume"]["box"] for n in children])
        if children
        else tile_box(C.VisualTile(0, 0, 0), (0, 1))
    )
    return {
        "asset": {
            "version": "1.0",
            "generator": generator,
            "extras": {
                "frame": "turku3d-local-zup",
                "origin_en": [C.ORIGIN_E, C.ORIGIN_N, C.ORIGIN_H],
                "crs": C.CRS_COMPOUND,
            },
        },
        "geometricError": ROOT_ERROR,
        "root": {
            "boundingVolume": {"box": root_box},
            "geometricError": ROOT_ERROR,
            "refine": "REPLACE",
            "children": children,
        },
    }


class DistTree:
    """dist/tiles/ tree: writes files, keeps manifest entries, commits manifest last."""

    def __init__(self, root: str | Path, areas: list[str] | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        mpath = self.root / "manifest.json"
        old = json.loads(mpath.read_text()) if mpath.exists() else {}
        if old and (old.get("format") != MANIFEST_FORMAT or old.get("version") != MANIFEST_VERSION):
            raise ValueError(f"{mpath}: not a {MANIFEST_FORMAT} v{MANIFEST_VERSION}")
        self.visual: dict = dict(old.get("visual", {}))
        self.collision: dict = dict(old.get("collision", {}))
        self.areas = sorted(set(old.get("areas", [])) | set(areas or []))

    def _put(self, rel: str, data: bytes) -> dict:
        _atomic_write(self.root / rel, data)
        return {"url": rel, "sha256": sha256_bytes(data), "bytes": len(data)}

    def add_visual(
        self, tile: C.VisualTile, glb: bytes, *, tris: int, h: tuple[float, float]
    ) -> dict:
        e = self._put(f"visual/{tile.level}/{tile.id}.glb", glb)
        e.update(level=tile.level, tris=int(tris), h=[round(float(h[0]), 3), round(float(h[1]), 3)])
        self.visual[tile.id] = e
        return e

    def add_collision(
        self, tile: C.CollisionTile, col: bytes, *, tris: int, prims: int, h, flags: int
    ) -> dict:
        parent = tile.visual_parent().id
        e = self._put(f"collision/{parent}/{tile.id}.col", col)
        e.update(
            tris=int(tris),
            prims=int(prims),
            h=[round(float(h[0]), 3), round(float(h[1]), 3)],
            flags=int(flags),
        )
        self.collision[tile.id] = e
        return e

    def commit(self, *, code: str | None = None) -> dict:
        code = code or git_sha()
        ts = build_tileset(self.visual, f"turku3d {code}")
        ts_bytes = (json.dumps(ts, indent=1) + "\n").encode()
        ts_entry = self._put("tileset.json", ts_bytes)
        m = {
            "format": MANIFEST_FORMAT,
            "version": MANIFEST_VERSION,
            "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "code": code,
            "origin_en": [C.ORIGIN_E, C.ORIGIN_N, C.ORIGIN_H],
            "crs": C.CRS_COMPOUND,
            "grid": {
                "visual_l0": C.VISUAL_TILE_SIZE,
                "collision": C.COLLISION_TILE_SIZE,
                "levels": C.VISUAL_MAX_LEVEL + 1,
            },
            "materials_version": 1,
            "col_version": [1, 0],
            "areas": self.areas,
            "tileset": ts_entry,
            "visual": {k: self.visual[k] for k in sorted(self.visual)},
            "collision": {k: self.collision[k] for k in sorted(self.collision)},
        }
        _atomic_write(self.root / "manifest.json", (json.dumps(m, indent=1) + "\n").encode())
        return m


def verify_tree(root: str | Path) -> list[str]:
    """Re-hash every file in manifest.json; returns a list of problems (empty = OK)."""
    root = Path(root)
    m = json.loads((root / "manifest.json").read_text())
    bad = []
    for e in [m["tileset"], *m["visual"].values(), *m["collision"].values()]:
        f = root / e["url"]
        if not f.exists():
            bad.append(f"missing {e['url']}")
            continue
        b = f.read_bytes()
        if len(b) != e["bytes"] or sha256_bytes(b) != e["sha256"]:
            bad.append(f"hash/size mismatch {e['url']}")
    return bad
