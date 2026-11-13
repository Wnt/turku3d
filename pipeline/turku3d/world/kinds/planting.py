"""Planting: the hedges, shrub masses and single shrubs of a 62.5 m cell, as volumes.

discover  the cell's census (turku3d.world.green.hedges): hedge lines of the base map and OSM measured in the lidar's
          shrub layer (line, width and height per metre), hedges and masses only the lidar shows, single shrubs.
describe  per hedge: clipped or loose (from how level and even the lidar found it; a stored description overrides it
          with what a panorama shows), evergreen or not, the summer size. Per shrub: a family and a size.
compile   a hedge is its line swept by a section of its width and height: a closed solid in the family `hedge` with
          a fringe of leaf cards (turku3d.world.green.hedgegen); a mass is a closed dome over its outline; a shrub is
          a generated shrub model (turku3d.world.green.treegen) baked into the cell's meshes.

Description `turku-description/planting/1`:

    cell     [i, j]
    hedges   [{id, source, line: [[e, n], ...], ground: [...], width: [...], height: [...], form: clipped | loose,
               evergreen, seed, genus?, measured: {level, even} | false}]
    masses   [{id, ring: [[e, n], ...], ground, height, evergreen, seed}]
    shrubs   [{id, e, n, ground, family, height, width, seed}]

A stored description overrides per id: `{"hedges": {"<id>": {"form": "clipped", "height": 1.2, "remove": true}},
"masses": {...}, "shrubs": {...}}`; a hedge's `height` or `width` given as one number applies along its whole line.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..api import Area, Output, Stub, envelope
from ..frame import CELL, cell_at, cell_box_en, cell_key, to_engine
from ..geometry import seed as seed_of
from ..green import census, hedgegen, treegen

LEAF_MARGIN = 0.25  # metres a leaf-off hedge gains in width in leaf (half of it in height)
SHRUB_HEIGHTS = (0.6, 0.8, 1.0, 1.3, 1.6, 2.0, 2.5, 3.2)
SHRUB_SPREADS = (0.35, 0.5, 0.7, 1.0, 1.4)  # crown radius / height
VARIANTS = 2
LAYERS = ("kanta:Pensasaita",)


def hedge_row(row: dict) -> dict:
    key = f"hedge-{row['source'].split(':')[-1].lower()}-{row['key']}-{round(row['line'][0][0] * 10)}-{round(row['line'][0][1] * 10)}"
    measured = row.get("measured", True)
    grow = LEAF_MARGIN if measured else 0.0
    out = {
        "id": key, "source": row["source"], "line": row["line"], "ground": row["ground"],
        "width": [round(w + grow, 2) for w in row["width"]], "height": [round(h + grow / 2, 2) for h in row["height"]],
        "form": row["form"], "evergreen": row["evergreen"], "seed": seed_of(key),
        "measured": {"level": row["level"], "even": row["even"]} if measured else False,
    }
    if row.get("genus"):
        out["genus"] = row["genus"]
    return out


def shrub_row(row: dict) -> dict:
    seed = seed_of(row["id"])
    spread = row["width"] / max(row["height"], 0.3)
    if row["evergreen"]:
        family = "juniper"
    else:
        family = "rose" if row["height"] < 1.3 and spread > 1.0 else "lilac"
    return {"id": row["id"], "e": row["e"], "n": row["n"], "ground": row["ground"], "family": family,
            "height": round(row["height"] + LEAF_MARGIN / 2, 2), "width": round(row["width"] + LEAF_MARGIN, 2), "seed": seed}


def nearest(options: tuple, value: float) -> float:
    return min(options, key=lambda option: abs(math.log(option / max(value, 1e-6))))


def shrub_model(shrub: dict) -> treegen.Shape:
    height = nearest(SHRUB_HEIGHTS, shrub["height"])
    spread = nearest(SHRUB_SPREADS, shrub["width"] / 2 / shrub["height"])
    return treegen.Shape(shrub["family"], height, round(spread * height, 3), 0.0, "mature", shrub["seed"] % VARIANTS)


@lru_cache(maxsize=256)
def shrub_parts(shape: treegen.Shape, lod: int):
    return treegen.build(shape, lod)


def overridden(rows: list[dict], changes: dict) -> list[dict]:
    kept = []
    for row in rows:
        change = changes.get(row["id"], {})
        if change.get("remove"):
            continue
        row = {**row, **change}
        for key in ("width", "height"):
            if "line" in row and isinstance(row[key], (int, float)):
                row[key] = [float(row[key])] * len(row["line"])
        kept.append(row)
    return kept


class Planting:
    name = "planting"
    code = (Path(__file__).parent.parent / "green" / "hedgegen.py", Path(__file__).parent.parent / "green" / "treegen.py")

    def discover(self, area: Area, sources) -> list[Stub]:
        box = area.bbox()
        (i0, j1), (i1, j0) = cell_at(box[0], box[1]), cell_at(box[2], box[3])
        stubs = []
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                cell = cell_box_en(i, j)
                if not area.touches(cell):
                    continue
                found = census.cell(i, j, sources.wfs.features)
                if not (found["hedges"] or found["masses"] or found["shrubs"]):
                    continue
                refs = [{"source": "turku-lidar:pistepilvi", "key": f"{cell_key(i, j)}@{census.RECIPE}"},
                        *({"source": f"turku-wfs:{layer}", "key": cell_key(i, j)} for layer in LAYERS),
                        {"source": "osm:barrier=hedge", "key": cell_key(i, j)}]
                stubs.append(Stub(f"planting/{cell_key(i, j)}", cell, refs,
                                  {"cell": [i, j], "hedges": found["hedges"], "masses": found["masses"], "shrubs": found["shrubs"]}))
        return stubs

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        stored = stored or {}
        facts = stub.facts
        hedges = overridden([hedge_row(row) for row in facts["hedges"]], stored.get("hedges", {}))
        masses = overridden([{"id": f"mass-{row['key']}", "ring": row["ring"], "ground": row["ground"], "height": round(row["height"] + LEAF_MARGIN / 2, 2),
                              "evergreen": row["evergreen"], "seed": seed_of(row["key"])} for row in facts["masses"]], stored.get("masses", {}))
        shrubs = overridden([shrub_row(row) for row in facts["shrubs"]], stored.get("shrubs", {}))
        return {**envelope(self.name, 1, stub, stored, "w2-green"), "cell": facts["cell"],
                "hedges": sorted(hedges, key=lambda h: h["id"]), "masses": sorted(masses, key=lambda m: m["id"]),
                "shrubs": sorted(shrubs, key=lambda s: s["id"])}

    def compile(self, description: dict) -> Output:
        i, j = description["cell"]
        hedges, masses, shrubs = description["hedges"], description["masses"], description["shrubs"]
        grounds = [g for h in hedges for g in h["ground"]] + [m["ground"] for m in masses] + [s["ground"] for s in shrubs]
        if not grounds:
            return Output((i * CELL, 0.0, j * CELL), [])
        origin = (i * CELL, float(math.floor(min(grounds))), j * CELL)
        meshes = treegen.Meshes()

        def emit(body: dict, form: str, evergreen: bool, seed: int) -> None:
            kind = "evergreen" if evergreen else "leafy"
            for lod in (0, 1):
                meshes.add(f"body-{kind}", lod, hedgegen.body_material(evergreen), body)
                rng = np.random.default_rng([seed, 31, lod])
                fringe = hedgegen.shell(body["surface"], form, evergreen, rng, far=bool(lod))
                meshes.add(f"leaves-{kind}", lod, hedgegen.leaf_material(evergreen), fringe)

        for hedge in hedges:
            line = np.array(hedge["line"], float)
            x, y, z = to_engine(line[:, 0], line[:, 1], np.array(hedge["ground"], float))
            points = np.stack([x - origin[0], y - origin[1], z - origin[2]], axis=1)
            points, (width, height), _ = hedgegen.resample(points, [np.array(hedge["width"], float), np.array(hedge["height"], float)])
            rng = np.random.default_rng([hedge["seed"], 17])
            emit(hedgegen.sweep(points, width, height, hedge["form"], rng), hedge["form"], hedge["evergreen"], hedge["seed"])
        for mass in masses:
            ring = np.array(mass["ring"], float)
            x, y, z = to_engine(ring[:, 0], ring[:, 1], mass["ground"])
            rng = np.random.default_rng([mass["seed"], 19])
            body = hedgegen.mass(np.stack([x - origin[0], z - origin[2]], axis=1), float(np.mean(y)) - origin[1], mass["height"], rng)
            emit(body, "loose", mass["evergreen"], mass["seed"])

        # Shrubs are scenery nothing collides with: the library models baked into the cell's own leaf and stem meshes.
        for shrub in shrubs:
            shape = shrub_model(shrub)
            x, y, z = to_engine(shrub["e"], shrub["n"], shrub["ground"])
            side = shrub["width"] / 2 / shape.radius
            row = (x - origin[0], y - origin[1] - 0.05, z - origin[2], math.radians((shrub["seed"] >> 8) % 360), side, shrub["height"] / shape.height, side)
            for lod in (0, 1):
                for part in shrub_parts(shape, lod):
                    meshes.add(f"shrub-{part.material.family}", lod, treegen.shared_material(part.material), treegen.placed(part, row))
        return Output(origin, meshes.parts())
