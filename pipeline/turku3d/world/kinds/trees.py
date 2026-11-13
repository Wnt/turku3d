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
    felled   ids of crowns of the 2021 scan on a street that the city's tree register no longer lists (see `on_street`),
             and of crowns only the lidar shows that stand in a carriageway out of reach of its edge (see `carriageway`)

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
LEAF_MARGIN = 0.4  # metres added to a leaf-off broadleaf crown diameter: the foliage beyond the twig tips
# Nothing grows in a carriageway and no bough hangs low over one: lorries keep 4.5 m clear, and a street-level camera
# must not sit in leaves. A crown only the lidar shows with its trunk on the carriageway is a garden tree whose top
# leans over the street (moved to the edge when its crown reaches it) or a lorry, a pole or a felled tree (left out).
ROAD_CLEAR = 4.5  # metres: the crown base of a tree whose crown reaches over a carriageway
ROAD_TALL = 7.0  # metres: a lower tree cannot be pruned that high; its crown stops at the carriageway's edge
ROAD_HALF = 2.5  # metres: half the carriageway of a street only OpenStreetMap draws
WALK_CLEAR, WALK_TALL = 2.6, 5.0  # metres: the crown base over ground people walk on, of a tree at least this tall
VERGE = 0.6  # metres from the carriageway's edge to a trunk moved off it
# Garden conifers the scan shows green and no register names are thuja columns far more often than young spruces
# (the photos of the detached-house dev points): a narrow column each, and a long or wide crown is a row or a clump.
THUJA_MAX = 9.0  # metres in the scan
THUJA_SPREAD = 0.55  # a column is at most this wide for its height
THUJA_GROUP = 16  # columns one measured crown is split into at most
# The tree library: family x size class x seed. A tree is an instance of the nearest model, fitted by its row's
# scale (at most about a quarter either way), so a location draws a few dozen tree meshes however many trees it has.
# A grown birch, aspen or pine has shed its low branches: what the scan returns under its crown is the undergrowth
# (dev points: birches 15 m tall measured with a crown base of 1 m stand with 4 m of bare white stem).
CLEAR_STEM = {"birch": 0.28, "poplar": 0.28, "pine": 0.45}  # crown base / height at least
CLEAR_FROM = 9.0  # metres of height
HEIGHTS = (4.0, 6.3, 10.0, 16.0, 25.0)
SPREADS = (0.18, 0.3, 0.46)  # crown radius / height
BASES = (0.16, 0.28, 0.45)  # crown base / height
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


def carriageway(rows: list[dict], box: tuple[float, float, float, float]) -> dict[str, dict]:
    """Trees at a carriageway or at paving: id -> {"d": metres from the trunk to the carriageway (negative: the trunk
    stands this far inside it), "to": [e, n] the nearest ground VERGE off it, for a trunk inside, "w": metres from
    the trunk to ground people walk on (0 on it)}, each when within 15 m. Carriageways are the street register's
    (GIS:Katualueet, Ajorata and Pyöräkaista) and, where it draws none, the mapped roads at ROAD_HALF; walked
    ground is every hard-surfaced part of the street and green registers."""
    from ..green.census import PAVED
    from ..street import osm

    if not rows:
        return {}
    streets = registers.layer("GIS:Katualueet", box)
    low = [shapely.make_valid(shapely.force_2d(row.geometry)) for row in streets.itertuples()
           if row.geometry.geom_type == "Polygon" and str(getattr(row, "Katuosalaji", "")).lower() in ("ajorata", "pyöräkaista")]
    walked = [shapely.make_valid(shapely.force_2d(row.geometry)) for layer in (streets, registers.layer("GIS:Viheralueet", box)) for row in layer.itertuples()
              if row.geometry.geom_type == "Polygon" and any(word in str(getattr(row, "Pintamateriaali", "")).lower() for word in PAVED)]
    mapped = osm.features(box)
    if mapped is not None and len(mapped):
        roads = mapped[mapped.geom_type.isin(("LineString", "MultiLineString")) & mapped.highway.isin(ROADS)]
        low += [shape.buffer(ROAD_HALF, cap_style="flat") for shape in roads.geometry]
    area = shapely.union_all(low) if low else None
    paving = shapely.union_all(walked) if walked else None
    found = {}
    for row in rows:
        trunk = shapely.Point(row["e"], row["n"])
        at = {}
        if paving is not None and (walk := float(paving.distance(trunk))) <= 15.0:
            at["w"] = round(walk, 2)
        distance = float(area.distance(trunk)) if area is not None else 99.0
        if 0.0 < distance <= 15.0:
            at["d"] = round(distance, 2)
        elif distance <= 0.0:
            edge = area.boundary
            inside = float(edge.distance(trunk))
            to = edge.interpolate(edge.project(trunk))
            out = np.array([to.x - trunk.x, to.y - trunk.y]) / max(inside, 1e-6)
            at.update({"d": round(-inside, 2), "to": [round(float(to.x + out[0] * VERGE), 2), round(float(to.y + out[1] * VERGE), 2)]})
        if at:
            found[row["id"]] = at
    return found


ROADS = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
         "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link")
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
        if height < THUJA_MAX and (spread < 0.24 or not (register or mapped)):
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
STAND = 12  # from this many on, and not in a line, they are a stand nobody planted: the region's wild broadleaves
WILD = (("birch", 0.55), ("poplar", 0.15), ("maple", 0.12), ("rowan", 0.08), ("oak", 0.05), ("willow", 0.05))


def in_line(rows: list[dict]) -> bool:
    """True when the trees stand along one line (a row or an avenue) rather than over an area."""
    points = np.array([[row["e"], row["n"]] for row in rows])
    spread = np.sqrt(np.maximum(np.linalg.eigvalsh(np.cov((points - points.mean(axis=0)).T)), 0.0))
    return bool(spread[0] < 0.25 * spread[1])


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
        wild = len(rows_of) >= STAND and not in_line(rows_of)
        for row in rows_of:
            if near:
                out[row["id"]] = first[near[0][1]][0], "the species of the register tree beside it"
            elif wild and first[row["id"]][1] != "lidar: small broadleaf":
                out[row["id"]] = pick(WILD, (seed_of(row["id"]) % 1000) / 1000.0), "a stand of unknown species: wild broadleaves, mostly birch"
            elif len(rows_of) >= ROW and first[row["id"]][1] == "lidar: broadleaf":
                out[row["id"]] = "lime", "a planted row of unknown species: the city's commonest planted tree"
            else:
                out[row["id"]] = family_of(row, seed_of(leader))
    return out


def growth(row: dict, family: str) -> float:
    """Metres a tree gains in height a year after the scan. A street tree the register dates grew at its own mean rate
    since it was planted (about 3.5 m tall); an undated register tree at its size's rate. A tree only the lidar shows
    is a garden tree: mature and pruned, it gains a decimetre a year (4.2 m in the scan stood about 4.5 m in the
    photos of the house points, where the street trees' rates drew it at 6 m). A thuja column gains 0.3 m."""
    height, register = row["height"], row.get("register", {})
    if family == "thuja":
        return 0.3
    if not register:
        return 0.1 if height < 12 else 0.05
    year = register.get("planted") or 0
    if 1900 < year < 2021:
        return min(max((height - 3.5) / (2021 - year), 0.05), 0.35)
    return 0.2 if height < 6 else 0.15 if height < 10 else 0.1 if height < 16 else 0.05


def tree_row(row: dict, chosen: tuple[str, str] | None = None) -> dict:
    seed = seed_of(row["id"])
    family, why = chosen or family_of(row, seed)
    height = row["height"] + growth(row, family) * GROWTH_YEARS
    broadleaf = treegen.FAMILIES[family]["kind"] != "spruce" and treegen.FAMILIES[family]["kind"] != "pine"
    stretch = 1 + 0.6 * (height / row["height"] - 1) if family != "thuja" else 1.0
    margin = LEAF_MARGIN if broadleaf else 0.0
    major, minor = row["major"] * stretch + margin, row["minor"] * stretch + margin
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
                  "base": round(min(max(row["base"] * (height / row["height"]), CLEAR_STEM.get(family, 0.0) * height if height >= CLEAR_FROM else 0.0),
                                    0.7 * height), 2)},
        "measured": {key: row[key] for key in ("height", "width", "major", "minor", "base", "widestAt", "returns", "through", "green", "top")},
    }
    species = register.get("species") or register.get("genus") or row.get("osm", {}).get("species")
    if species:
        out["species"] = species
    if register.get("planted"):
        out["planted"] = register["planted"]
    return out


def thuja_columns(tree: dict, row: dict) -> list[dict]:
    """The columns of a thuja crown only the lidar shows: one column as wide as the crown's short axis and at most
    THUJA_SPREAD of its height, and as many of them along (and across) the crown as it is long (and wide)."""
    # The crown as measured: the described one is limited to what one model can be stretched to.
    crown = {**tree["crown"], "major": row.get("major", tree["crown"]["major"]), "minor": row.get("minor", tree["crown"]["minor"])}
    width = max(min(crown["minor"], THUJA_SPREAD * tree["height"]), 0.8)
    along, across = max(1, round(crown["major"] / width)), max(1, round(crown["minor"] / width))
    while along * across > THUJA_GROUP:
        along, across = max(1, along - 1), max(1, across - (1 if across > along else 0))
    angle = math.radians(crown["angle"])
    axis, side = (math.sin(angle), math.cos(angle)), (math.cos(angle), -math.sin(angle))
    centre = row.get("centre") or (tree["e"], tree["n"])
    out = []
    for a in range(along):
        for b in range(across):
            key = tree["id"] if along * across == 1 else f"{tree['id']}-{chr(97 + a)}{chr(97 + b) if across > 1 else ''}"
            seed = seed_of(key)
            u = (a - (along - 1) / 2) * crown["major"] / along
            v = (b - (across - 1) / 2) * crown["minor"] / across
            size = 0.88 + 0.12 * ((seed >> 4) % 100) / 99 if along * across > 1 else 1.0  # the tallest of a row was measured
            wide = round(width * (0.9 + 0.2 * ((seed >> 12) % 100) / 99), 2)
            out.append({**tree, "id": key, "seed": seed, "e": round(centre[0] + axis[0] * u + side[0] * v, 2),
                        "n": round(centre[1] + axis[1] * u + side[1] * v, 2), "height": round(tree["height"] * size, 2),
                        "crown": {"major": wide, "minor": wide, "angle": 0.0, "base": 0.3}})
    return out


def off_carriageway(tree: dict, at: dict | None) -> dict | None:
    """The tree as it stands beside a carriageway (`at` from `carriageway`): None when it cannot stand there at all."""
    if at is None:
        return tree
    crown = dict(tree["crown"])
    reach = (crown["major"] + crown["minor"]) / 4
    if reach > at.get("w", 99.0) and tree["height"] >= WALK_TALL:
        # Boughs over a footway, a square or a park path are cut to walking height: what the scan returns under
        # such a crown is people, stalls and parked things.
        crown["base"] = round(max(crown["base"], min(WALK_CLEAR, 0.5 * tree["height"])), 2)
    distance = at.get("d", 99.0)
    if distance < 0:
        if tree["source"] != "lidar":
            distance = 0.0  # a surveyed trunk: the register's place is kept, its boughs are lifted
        elif -distance + VERGE <= max(crown["major"] / 2, 1.5):
            tree, distance = {**tree, "e": at["to"][0], "n": at["to"][1], "moved": "off the carriageway"}, VERGE
        else:
            return None
    if reach > distance:
        if tree["height"] >= ROAD_TALL:
            crown["base"] = round(max(crown["base"], min(ROAD_CLEAR, 0.6 * tree["height"])), 2)
        else:
            wide = max(2 * (distance + 0.3), 1.0)
            crown["major"], crown["minor"] = round(min(crown["major"], wide), 2), round(min(crown["minor"], wide), 2)
    return {**tree, "crown": crown}


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
    if tree["family"] == "thuja":
        base = 0.3 / height  # a column is green to the ground
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
                                   "onStreet": on_street(found["trees"], wide), "carriageway": carriageway(found["trees"], wide)}))
        return stubs

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        stored = stored or {}
        street = stub.facts.get("onStreet", {})
        overrides = stored.get("trees", {})
        felled = sorted(row["id"] for row in stub.facts["trees"]
                        if street.get(row["id"], 0.0) > row["width"] / 2 and not overrides.get(row["id"], {}).get("keep"))
        chosen = families(stub.facts["trees"])
        roads = stub.facts.get("carriageway", {})
        trees = []
        for row in stub.facts["trees"]:
            if row["id"] in felled:
                continue
            tree = off_carriageway(tree_row(row, chosen[row["id"]]), None if overrides.get(row["id"], {}).get("keep") else roads.get(row["id"]))
            if tree is None:
                felled.append(row["id"])
            elif tree["family"] == "thuja" and row["source"] == "lidar":
                trees += thuja_columns(tree, row)
            else:
                trees.append(tree)
        felled.sort()
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
