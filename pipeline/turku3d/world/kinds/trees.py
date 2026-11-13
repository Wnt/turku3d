"""Trees: every tree of a 62.5 m cell at its measured place and size, each its own instance.

discover  the cell's census (turku3d.world.green: lidar crowns joined with the tree register, the base map and
          OSM; private gardens included), cached in the shared source cache.
describe  per tree: the family (from the register species, else OSM, else the lidar's evergreen cue and the
          crown's proportions), growth since the scan, an age class and a seed. These are the tree's generating
          parameters; nothing else decides its geometry.
compile   one instance row per tree of a procedural model grown to the measured height, crown width and crown
          base (turku3d.world.green.treegen). Trees of nearly the same proportions share a model (their measured
          size is the instance scale), so a street of limes is a handful of meshes and no two neighbours match.

Description `turku-description/trees/1`:

    cell     [i, j]
    scan     {"date": "2021-04", "growthYears": 5}
    trees    [{id, e, n, ground, source, family, age, seed, height, crown: {major, minor, angle, base},
               measured: {the census row}, species?}]
    absent   register trees the lidar does not show and no rule builds: [{e, n, layer, key, ...}]
    cleared  ids of trees left out because they stand within 3 m of a reference camera (see CAMERA_CLEARANCE)
    felled   ids of crowns of the 2021 scan on a street that the city's tree register no longer lists (see `on_street`)

A stored description overrides the automatic one per tree: `{"trees": {"<id>": {"family": "lime", "height": 14,
"crown": {...}, "remove": true}}, "add": [{...a full tree row...}]}`. Observation writes there what panoramas show
(a felled tree, a species, a tree planted after the scan).

A tree is hit by cars in the game: behaviour `fixed`, collision = its trunk as a cylinder, pivot at the trunk base.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely

from ..api import Area, Instances, Output, Stub, envelope
from ..frame import CELL, cell_at, cell_box_en, cell_key, to_engine
from ..geometry import seed as seed_of
from ..green import census, treegen
from ..street import registers

GROWTH_YEARS = 5  # the scan is from April 2021; the reference photos are from summer 2026
LEAF_MARGIN = 0.4  # metres added to a leaf-off crown diameter: the foliage beyond the twig tips
# The tree library: family x size class x seed. A tree is an instance of the nearest model, fitted by its row's
# scale (at most about a quarter either way), so a location draws a few dozen tree meshes however many trees it has.
HEIGHTS = (4.0, 6.3, 10.0, 16.0, 25.0)
SPREADS = (0.18, 0.3, 0.46)  # crown radius / height
BASES = (0.2, 0.42)  # crown base / height
VARIANTS = 2  # seeds, for the size classes a street repeats: the middle spread; the others have one

# A reference photo is taken from the carriageway or the pavement; a tree whose trunk or foliage is this close to
# the camera fills the still and hides what the view is of. The register places trees within a metre and the view
# poses within a few, so such a tree is nearly always a pose error, not a tree in the lens: it is left out of
# the description (listed under `cleared`) unless the stored description keeps it (`{"trees": {id: {"keep": true}}}`,
# written when the photo shows it).
VIEWS = Path(__file__).resolve().parents[4] / "qa/views/views.jsonl"
CAMERA_CLEARANCE = 3.0


def cameras(box: tuple[float, float, float, float], views: Path = VIEWS) -> list[list[float]]:
    """Reference-view cameras inside a box [E, N, H], from the view registry."""
    found = []
    for line in views.read_text().splitlines() if views.exists() else []:
        pose = (json.loads(line) if line.strip() else {}).get("pose") or {}
        if "E" in pose and box[0] <= pose["E"] <= box[2] and box[1] <= pose["N"] <= box[3]:
            found.append([pose["E"], pose["N"], pose.get("H", 0.0)])
    return sorted(found)


def beside_camera(tree: dict, camera: list[float]) -> bool:
    """True when the tree's trunk or crown comes within CAMERA_CLEARANCE of the camera."""
    distance = math.hypot(tree["e"] - camera[0], tree["n"] - camera[1])
    crown = tree["crown"]
    side = max(0.0, distance - (crown["major"] + crown["minor"]) / 4)
    above = max(0.0, tree["ground"] + crown["base"] - camera[2])
    return distance < CAMERA_CLEARANCE or math.hypot(side, above) < CAMERA_CLEARANCE


def on_street(rows: list[dict], box: tuple[float, float, float, float]) -> dict[str, float]:
    """Trees only the lidar shows whose trunk stands on the street area (GIS:Katualueet): id -> metres from the trunk
    to the nearest ground off the street.

    The city's tree register (GIS:Puut) is the maintenance inventory of the trees on its streets and is current; the
    scan is from 2021. A crown of the scan on a street that the register does not list is either a tree felled since
    (or a pole, a parked lorry) or a garden tree leaning over the pavement. The description tells them apart by reach:
    a crown that does not reach off the street cannot hang from a garden."""
    lone = [row for row in rows if row["source"] == "lidar"]
    if not lone:
        return {}
    streets = registers.layer("GIS:Katualueet", box)
    shapes = [shapely.make_valid(shapely.force_2d(shape)) for shape in streets.geometry if shape.geom_type == "Polygon"]
    if not shapes:
        return {}
    area = shapely.union_all(shapes)
    found = {}
    for row in lone:
        trunk = shapely.Point(row["e"], row["n"])
        if area.contains(trunk):
            found[row["id"]] = round(float(area.boundary.distance(trunk)), 2)
    return found


GENERA = {
    "tilia": "lime", "acer": "maple", "ulmus": "elm", "quercus": "oak", "fraxinus": "ash", "betula": "birch",
    "populus": "poplar", "sorbus": "rowan", "prunus": "fruit", "malus": "fruit", "pyrus": "fruit", "crataegus": "fruit",
    "salix": "willow", "pinus": "pine", "larix": "pine", "picea": "spruce", "abies": "spruce", "pseudotsuga": "spruce",
    "thuja": "thuja", "juniperus": "thuja", "taxus": "thuja", "fagus": "lime", "carpinus": "lime", "corylus": "lime",
    "alnus": "birch", "aesculus": "maple", "syringa": "lilac", "amelanchier": "fruit", "juglans": "ash", "robinia": "ash",
    # the base map's Finnish genus names
    "lehmus": "lime", "vaahtera": "maple", "jalava": "elm", "tammi": "oak", "saarni": "ash", "koivu": "birch",
    "poppeli": "poplar", "haapa": "poplar", "pihlaja": "rowan", "omenapuu": "fruit", "kirsikka": "fruit", "paju": "willow",
    "mänty": "pine", "kuusi": "spruce", "lehtikuusi": "pine", "tuija": "thuja", "kataja": "thuja", "leppä": "birch",
    "hevoskastanja": "maple", "pyökki": "lime", "tuomi": "fruit", "orapihlaja": "fruit", "syreeni": "lilac",
}
GARDEN_TALL = (("maple", 0.34), ("lime", 0.24), ("birch", 0.2), ("oak", 0.1), ("elm", 0.07), ("ash", 0.05))
GARDEN_SMALL = (("rowan", 0.3), ("fruit", 0.3), ("maple", 0.2), ("birch", 0.2))


def pick(options, fraction: float) -> str:
    total = 0.0
    for name, weight in options:
        total += weight
        if fraction < total:
            return name
    return options[-1][0]


def family_of(tree: dict, seed: int) -> tuple[str, str]:
    """(family, why) of a census row."""
    register, mapped = tree.get("register", {}), tree.get("osm", {})
    for text, why in ((register.get("species", ""), "register species"), (register.get("genus", ""), "base map genus"),
                      (mapped.get("genus", "") or mapped.get("species", ""), "osm")):
        word = text.strip().split(" ")[0].lower() if text else ""
        if word in GENERA:
            return GENERA[word], why
    height, width, base = tree["height"], tree["width"], tree["base"]
    spread = width / max(height, 1.0)
    evergreen = tree.get("green", 0.0) >= 0.06 or (tree.get("green", 0.0) >= 0.045 and tree.get("through", 1.0) < 0.6)
    conifer = register.get("conifer") or mapped.get("leaf_type") == "needleleaved"
    broadleaf = (register and not register.get("conifer")) or mapped.get("leaf_type") == "broadleaved"
    if conifer or (evergreen and not broadleaf):
        why = "register conifer" if conifer else "lidar: green in a leaf-off scan"
        if spread < 0.24 and height < 9:
            return "thuja", why
        return ("pine", why) if base / height > 0.4 and height > 8 else ("spruce", why)
    fraction = (seed % 1000) / 1000.0
    if height < 4.5 and base < 1.3:
        return "lilac", "lidar: low crown to the ground"
    if height < 7.5 and spread > 0.7:
        return "fruit", "lidar: low and wide"
    if height < 9:
        return pick(GARDEN_SMALL, fraction), "lidar: small broadleaf"
    if spread < 0.4 and height > 12:
        return ("birch" if fraction < 0.7 else "poplar"), "lidar: tall and narrow"
    return pick(GARDEN_TALL, fraction), "lidar: broadleaf"


GUESSED = ("lidar: small broadleaf", "lidar: tall and narrow", "lidar: broadleaf")
TOGETHER = 12.0  # metres between trees of one planting
ROW = 4  # trees of a like size standing together from this many on are a planted row or avenue


def families(rows: list[dict]) -> dict[str, tuple[str, str]]:
    """(family, why) per census row id. Trees are planted in rows and groups of one species: broadleaf trees of
    unknown species that stand together at a like size share one guess, and take the species of a register tree
    of their size among them when there is one."""
    first = {row["id"]: family_of(row, seed_of(row["id"])) for row in rows}
    guessed = [row for row in rows if first[row["id"]][1] in GUESSED]
    named = [row for row in rows if first[row["id"]][1] in ("register species", "base map genus", "osm")
             and treegen.FAMILIES[first[row["id"]][0]]["kind"] == "broadleaf"]
    group = {row["id"]: row["id"] for row in guessed}

    def root(key: str) -> str:
        while group[key] != key:
            key = group[key]
        return key

    def alike(a: dict, b: dict) -> bool:
        return math.hypot(a["e"] - b["e"], a["n"] - b["n"]) < TOGETHER and 0.7 < a["height"] / b["height"] < 1.43

    for index, a in enumerate(guessed):
        for b in guessed[index + 1:]:
            if first[a["id"]][1] == first[b["id"]][1] and alike(a, b):
                low, high = sorted((root(a["id"]), root(b["id"])))
                group[high] = low
    members: dict[str, list[dict]] = {}
    for row in guessed:
        members.setdefault(root(row["id"]), []).append(row)
    out = dict(first)
    for leader, rows_of in members.items():
        near = sorted((math.hypot(a["e"] - b["e"], a["n"] - b["n"]), b["id"]) for a in rows_of for b in named if alike(a, b))
        for row in rows_of:
            if near:
                out[row["id"]] = first[near[0][1]][0], "the species of the register tree beside it"
            elif len(rows_of) >= ROW and first[row["id"]][1] == "lidar: broadleaf":
                out[row["id"]] = "lime", "a planted row of unknown species: the city's commonest planted tree"
            else:
                out[row["id"]] = family_of(row, seed_of(leader))
    return out


def grown(height: float) -> float:
    """Height in the photo year from the height in the scan year."""
    rate = 0.35 if height < 6 else 0.25 if height < 10 else 0.15 if height < 16 else 0.05
    return height + rate * GROWTH_YEARS


def tree_row(row: dict, chosen: tuple[str, str] | None = None) -> dict:
    seed = seed_of(row["id"])
    family, why = chosen or family_of(row, seed)
    height = grown(row["height"])
    stretch = 1 + 0.6 * (height / row["height"] - 1)
    major, minor = row["major"] * stretch + LEAF_MARGIN, row["minor"] * stretch + LEAF_MARGIN
    mean = math.sqrt(major * minor)
    major, minor = min(major, 1.3 * mean), max(minor, mean / 1.3)  # a model is stretched at most this far
    register = row.get("register", {})
    young = height < 7.5 and treegen.FAMILIES[family]["kind"] == "broadleaf" and family not in ("fruit", "rowan")
    old = height >= 19 or mean >= 13
    out = {
        "id": row["id"], "e": row["e"], "n": row["n"], "ground": row["ground"], "source": row["source"],
        "family": family, "familyFrom": why, "age": "young" if young else "old" if old else "mature", "seed": seed,
        "height": round(height, 2),
        "crown": {"major": round(major, 2), "minor": round(minor, 2), "angle": row["angle"],
                  "base": round(min(row["base"] * (height / row["height"]), 0.7 * height), 2)},
        "measured": {key: row[key] for key in ("height", "width", "major", "minor", "base", "widestAt", "returns", "through", "green", "top")},
    }
    species = register.get("species") or register.get("genus") or row.get("osm", {}).get("species")
    if species:
        out["species"] = species
    if register.get("planted"):
        out["planted"] = register["planted"]
    return out


def planted_since_scan(row: dict) -> dict | None:
    """A register tree the lidar does not show, when the register says it was planted about or after the scan."""
    year = row.get("planted") or (int(row["surveyed"][:4]) if row.get("surveyed", "")[:4].isdigit() and row["surveyed"] >= "2021-04" else 0)
    if year < 2019:
        return None
    seed = seed_of(f"{row['layer']}-{row['key']}")
    fake = {"id": f"{row['layer'].split(':')[1].lower()}-{row['key']}", "height": 4.0, "width": 1.5, "base": 1.9, "register": row}
    family, why = family_of(fake, seed)
    height = round(min(3.6 + 0.35 * (2026 - max(year, 2021)), 6.0), 2)
    return {"id": fake["id"], "e": row["e"], "n": row["n"], "ground": row["ground"], "source": "register", "family": family,
            "familyFrom": why, "age": "young", "seed": seed, "height": height,
            "crown": {"major": round(0.42 * height, 2), "minor": round(0.38 * height, 2), "angle": 0.0, "base": round(0.42 * height, 2)},
            "planted": year, **({"species": row["species"]} if row.get("species") else {})}


def nearest(options: tuple, value: float) -> float:
    return min(options, key=lambda option: abs(math.log(option / max(value, 1e-6))))


def model_of(tree: dict) -> treegen.Shape:
    """The shared model a tree is an instance of: its family and age at the nearest size class."""
    height = nearest(HEIGHTS, tree["height"])
    radius = math.sqrt(tree["crown"]["major"] * tree["crown"]["minor"]) / 2
    spread = nearest(SPREADS, radius / tree["height"])
    base = nearest(BASES, max(tree["crown"]["base"], 0.3) / tree["height"])
    if treegen.FAMILIES[tree["family"]]["kind"] == "shrub":
        base = 0.0
    broadleaf = treegen.FAMILIES[tree["family"]]["kind"] == "broadleaf"
    age = "young" if height <= 6.3 and broadleaf and tree["family"] not in ("fruit", "rowan") else "old" if height >= 25 else "mature"
    variant = tree["seed"] % VARIANTS if spread == SPREADS[1] else 0
    return treegen.Shape(tree["family"], height, round(spread * height, 3), round(base * height, 3), age, variant)


@lru_cache(maxsize=512)
def model_parts(shape: treegen.Shape, lod: int):
    return treegen.build(shape, lod)


class Trees:
    name = "trees"
    code = (Path(__file__).parent.parent / "green" / "treegen.py",)

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
                if not found["trees"] and not found["absent"]:
                    continue
                refs = [{"source": "turku-lidar:pistepilvi", "key": f"{cell_key(i, j)}@{census.RECIPE}"},
                        *({"source": f"turku-wfs:{layer}", "key": cell_key(i, j)} for layer in ("GIS:Puut", "kanta:Lehtipuu", "kanta:Havupuu"))]
                wide = (cell[0] - 20.0, cell[1] - 20.0, cell[2] + 20.0, cell[3] + 20.0)
                stubs.append(Stub(f"trees/{cell_key(i, j)}", cell, [*refs, {"source": "turku-wfs:GIS:Katualueet", "key": cell_key(i, j)}],
                                  {"cell": [i, j], "trees": found["trees"], "absent": found["absent"], "cameras": cameras(wide),
                                   "onStreet": on_street(found["trees"], wide)}))
        return stubs

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        stored = stored or {}
        street = stub.facts.get("onStreet", {})
        overrides = stored.get("trees", {})
        felled = sorted(row["id"] for row in stub.facts["trees"]
                        if street.get(row["id"], 0.0) > row["width"] / 2 and not overrides.get(row["id"], {}).get("keep"))
        chosen = families(stub.facts["trees"])
        trees = [tree_row(row, chosen[row["id"]]) for row in stub.facts["trees"] if row["id"] not in felled]
        absent = []
        for row in stub.facts["absent"]:
            young = planted_since_scan(row)
            if young:
                trees.append(young)
            else:
                absent.append(row)
        kept, cleared = [], []
        for tree in trees:
            change = overrides.get(tree["id"], {})
            if change.get("remove"):
                continue
            if not change.get("keep") and any(beside_camera(tree, camera) for camera in stub.facts.get("cameras", [])):
                cleared.append(tree["id"])
                continue
            kept.append({**tree, **{k: v for k, v in change.items() if k != "crown"}, "crown": {**tree["crown"], **change.get("crown", {})}})
        kept += stored.get("add", [])
        kept.sort(key=lambda t: t["id"])
        return {**envelope(self.name, 1, stub, stored, "w2-green"), "cell": stub.facts["cell"],
                "scan": {"date": "2021-04", "growthYears": GROWTH_YEARS}, "trees": kept, "absent": absent, "cleared": cleared,
                "felled": felled}

    def compile(self, description: dict) -> Output:
        i, j = description["cell"]
        trees = description["trees"]
        if not trees:
            return Output((i * CELL, 0.0, j * CELL), [])
        origin = (i * CELL, float(math.floor(min(t["ground"] for t in trees))), j * CELL)
        groups: dict[treegen.Shape, list[tuple[list[float], str]]] = {}
        for tree in trees:
            shape = model_of(tree)
            x, y, z = to_engine(tree["e"], tree["n"], tree["ground"])
            crown = tree["crown"]
            round_crown = crown["major"] / crown["minor"] < 1.12
            turn = (tree["seed"] >> 8) % 360
            # The model's +X follows the crown's long axis (degrees east of north); a round crown turns freely.
            yaw = math.radians(turn if round_crown else 90.0 - crown["angle"] + 180.0 * (turn % 2))
            scale = (crown["major"] / 2 / shape.radius, tree["height"] / shape.height, crown["minor"] / 2 / shape.radius)
            groups.setdefault(shape, []).append(([x - origin[0], y - origin[1] - 0.08, z - origin[2], yaw, *scale], tree["id"]))
        # Near, every tree is its own instance row of its library model. Far, the cell's trees are one leaf mesh and
        # one bark mesh made of the models' far level (the thinned card prefix): two draws for the cell.
        instances, far = [], treegen.Meshes()
        for shape in sorted(groups, key=lambda s: (s.family, s.height, s.radius, s.base, s.age, s.seed)):
            rows = groups[shape]
            transforms = np.round(np.array([row for row, _ in rows]), 4)
            instances.append(Instances(model_parts(shape, 0), transforms, lod=0, ids=[tree_id for _, tree_id in rows], behaviour="fixed",
                                       collision=treegen.trunk_collision(shape)))
            for part in model_parts(shape, 1):
                for row in transforms:
                    far.add(part.material.family, 1, treegen.shared_material(part.material), treegen.placed(part, row))
        return Output(origin, far.parts(), instances)
