"""Planting: the hedges, shrub masses, single shrubs and unmown ground of a 62.5 m cell.

discover  the cell's census (turku3d.world.green.hedges): hedge lines of the base map and OSM measured in the lidar's
          shrub layer (line, width and height per metre), hedges and masses only the lidar shows, single shrubs;
          the green register's planting beds as masses (height from the scan where it shows them); meadows and
          forest floors of the registers and OSM as a raster (turku3d.world.green.tufts).
describe  per hedge: clipped or loose (from how level and even the lidar found it; a stored description overrides it
          with what a panorama shows), evergreen or not, the summer size. Per shrub: a family and a size.
compile   a hedge is its line swept by a section of its width and height: a closed solid in the family `hedge`
          whose leaf sprays the client grows from it (turku3d.world.green.hedgegen); a mass is a closed dome over its
          outline; a shrub is an instance of the shared shrub library (turku3d.world.green.treegen); unmown ground
          grows grass, flowers and undergrowth as leaf cards.

Levels, as the trees': near (lod 0) the cell is instance rows only, one per shrub model of the library and one row
of the cell's own asset (its hedge and mass bodies and its tufts, placed once at the origin); far (lod 1) it is one
merged mesh (the bodies again, coarser, whose far sprays the client grows at a share of the near ones, and one
leaf mesh of the shrubs' far level and the far tufts, which the client's grown sprays join: a cell is two or three
draws far). The client draws the near level round the camera and the far mesh beyond, and a still
draws a scene row near. Nothing per cell is stored that the client derives: the sprays, 65 % of the bytes before.

Description `turku-description/planting/1`:

    cell     [i, j]
    hedges   [{id, source, line: [[e, n], ...], ground: [...], width: [...], height: [...], form: clipped | loose,
               evergreen, seed, genus?, sparse?, measured: {level, even, seen} | false}]
    masses   [{id, ring: [[e, n], ...], ground (one, or one per vertex), height, evergreen, seed,
               open?: [per vertex: on the cell's edge, the mass goes on next door], source?, seen?}]
    shrubs   [{id, e, n, ground, family, height, width, seed}]
    cover?   {step, rows: ["..mmff", ...] from the south (m meadow, f forest floor), reliefStep, relief: [[h, ...]]}

A stored description overrides per id: `{"hedges": {"<id>": {"form": "clipped", "height": 1.2, "remove": true}},
"masses": {...}, "shrubs": {...}}`; a hedge's `height` or `width` given as one number applies along its whole line.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..api import Area, Instances, Output, Stub, envelope
from ..frame import CELL, cell_at, cell_box_en, cell_key, to_engine
from ..geometry import seed as seed_of
from ..green import census, hedgegen, treegen, tufts

LEAF_MARGIN = 0.25  # metres a leaf-off hedge or shrub gains in width in leaf (half of it in height)
# A leafless hedge returns from its twig mass, not from the outline its leaves draw: the summer top stands this far
# above the scan's (dev points: 0.9 m in the scan for a hedge of about 1.4 m in the photo).
HEDGE_TOP = {False: 0.3, True: 0.1}  # by evergreen
# A garden hedge is let grow to a screen: one lower than GROWN in the scan gained up to GROWTH in the five summers
# since (dev points: a newly planted row 0.4 m in the scan stands 1.5 m in the photo). A hedge on the city's ground
# (street or green area) is kept at its height.
GROWN, GROWTH = 1.4, 0.75
SHRUB_HEIGHTS = (0.6, 0.8, 1.0, 1.3, 1.6, 2.0, 2.5, 3.2)
SHRUB_SPREADS = (0.35, 0.5, 0.7, 1.0, 1.4)  # crown radius / height
VARIANTS = 2
LAYERS = ("kanta:Pensasaita",)


def hedge_row(row: dict) -> dict:
    key = f"hedge-{row['source'].split(':')[-1].lower()}-{row['key']}-{round(row['line'][0][0] * 10)}-{round(row['line'][0][1] * 10)}"
    measured = row.get("measured", True)
    grow = LEAF_MARGIN if measured else 0.0
    top = HEDGE_TOP[bool(row["evergreen"])] if measured else 0.0
    height = [h + top for h in row["height"]]
    if row.get("garden", True) and measured:
        height = [max(h, min(h + GROWTH, GROWN)) for h in height]
    out = {
        "id": key, "source": row["source"], "line": row["line"], "ground": row["ground"],
        "width": [round(w + grow, 2) for w in row["width"]], "height": [round(h, 2) for h in height],
        "form": row["form"], "evergreen": row["evergreen"], "seed": seed_of(key),
        "measured": {"level": row["level"], "even": row["even"], "seen": row.get("seen", 1.0)} if measured else False,
    }
    if row.get("sparse"):
        out["sparse"] = True
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
                if not (found["hedges"] or found["masses"] or found["shrubs"] or found.get("cover")):
                    continue
                refs = [{"source": "turku-lidar:pistepilvi", "key": f"{cell_key(i, j)}@{census.RECIPE}"},
                        *({"source": f"turku-wfs:{layer}", "key": cell_key(i, j)} for layer in LAYERS),
                        {"source": "osm:barrier=hedge", "key": cell_key(i, j)}]
                stubs.append(Stub(f"planting/{cell_key(i, j)}", cell, refs,
                                  {"cell": [i, j], "hedges": found["hedges"], "masses": found["masses"], "shrubs": found["shrubs"],
                                   **({"cover": found["cover"]} if found.get("cover") else {})}))
        return stubs

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        stored = stored or {}
        facts = stub.facts
        hedges = overridden([hedge_row(row) for row in facts["hedges"]], stored.get("hedges", {}))
        masses = overridden([{"id": f"mass-{row['key']}", "ring": row["ring"], "ground": row["ground"], "height": round(row["height"] + HEDGE_TOP[bool(row["evergreen"])], 2),
                              "evergreen": row["evergreen"], "seed": seed_of(row["key"]),
                              **({"open": row["open"], "source": row["source"], "seen": row["seen"]} if "open" in row else {})}
                             for row in facts["masses"]], stored.get("masses", {}))
        shrubs = overridden([shrub_row(row) for row in facts["shrubs"]], stored.get("shrubs", {}))
        return {**envelope(self.name, 1, stub, stored, "w2-green"), "cell": facts["cell"],
                "hedges": sorted(hedges, key=lambda h: h["id"]), "masses": sorted(masses, key=lambda m: m["id"]),
                "shrubs": sorted(shrubs, key=lambda s: s["id"]), **({"cover": facts["cover"]} if facts.get("cover") else {})}

    def compile(self, description: dict) -> Output:
        i, j = description["cell"]
        hedges, masses, shrubs = description["hedges"], description["masses"], description["shrubs"]
        cover = description.get("cover")
        grounds = [g for h in hedges for g in h["ground"]] + [g for m in masses for g in np.atleast_1d(m["ground"])] + [s["ground"] for s in shrubs]
        grounds += [float(np.min(cover["relief"]))] if cover else []
        if not grounds:
            return Output((i * CELL, 0.0, j * CELL), [])
        origin = (i * CELL, float(math.floor(min(grounds))), j * CELL)
        own, far = treegen.Meshes(), treegen.Meshes()  # the cell's own near asset; its far mesh

        # A body near and a coarser one far (both from the same line or outline), each growing its sprays in the client.
        for hedge in hedges:
            line = np.array(hedge["line"], float)
            x, y, z = to_engine(line[:, 0], line[:, 1], np.array(hedge["ground"], float))
            points = np.stack([x - origin[0], y - origin[1], z - origin[2]], axis=1)
            kind = "evergreen" if hedge["evergreen"] else "leafy"
            for lod, step in ((0, hedgegen.STEP), (1, hedgegen.FAR_STEP)):
                at, (width, height), _ = hedgegen.resample(points, [np.array(hedge["width"], float), np.array(hedge["height"], float)], step)
                body = hedgegen.sweep(at, width, height, hedge["form"], np.random.default_rng([hedge["seed"], 17]))
                (far if lod else own).add(f"body-{kind}", lod, hedgegen.body_material(hedge["evergreen"], far=bool(lod)), body)
        for mass in masses:
            ring = np.array(mass["ring"], float)
            x, y, z = to_engine(ring[:, 0], ring[:, 1], np.broadcast_to(np.asarray(mass["ground"], float), (len(ring),)))
            kind = "evergreen" if mass["evergreen"] else "leafy"
            for lod, step in ((0, 0.8), (1, hedgegen.FAR_STEP)):
                body = hedgegen.mass(np.stack([x - origin[0], z - origin[2]], axis=1), y - origin[1], mass["height"],
                                     np.random.default_rng([mass["seed"], 19]), mass.get("open"), step)
                (far if lod else own).add(f"body-{kind}", lod, hedgegen.body_material(mass["evergreen"], far=bool(lod)), body)

        # Shrubs are scenery nothing collides with: near, instances of the library models; far, their far level
        # merged into the cell's far mesh.
        groups: dict[treegen.Shape, list[tuple]] = {}
        for shrub in shrubs:
            shape = shrub_model(shrub)
            x, y, z = to_engine(shrub["e"], shrub["n"], shrub["ground"])
            side = shrub["width"] / 2 / shape.radius
            groups.setdefault(shape, []).append((x - origin[0], y - origin[1] - 0.05, z - origin[2], math.radians((shrub["seed"] >> 8) % 360),
                                                 side, shrub["height"] / shape.height, side))
        instances = []
        for shape in sorted(groups, key=lambda s: (s.family, s.height, s.radius, s.seed)):
            transforms = np.round(np.array(groups[shape]), 4)
            instances.append(Instances(list(shrub_parts(shape, 0)), transforms, lod=0))
            for part in shrub_parts(shape, 1):
                for row in transforms:
                    far.add(part.material.family, 1, treegen.shared_material(part.material), treegen.placed(part, row))
        box = cell_box_en(i, j)
        # Unmown ground: the covered squares at the ground, over which the client grows the cards (tufts.py).
        meadow = tufts.ground(cover) if cover else None
        if meadow is not None:
            x, y, z = to_engine(box[0] + meadow["positions"][:, 0], box[1] + meadow["positions"][:, 2], meadow["positions"][:, 1])
            meadow["positions"] = np.stack([x - origin[0], y - origin[1], z - origin[2]], axis=1)
            own.add("tufts", 0, tufts.material(), meadow)
            far.add("tufts", 1, tufts.material(far=True), meadow)
        mine = own.parts()
        if mine:
            instances.insert(0, Instances(mine, np.array([[0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]]), lod=0))
        return Output(origin, far.parts(), instances)
