"""light: what stands in a judged photograph, by kind, and the area profile of a location.

    python -m turku3d.observe light --location NAME --view ID... [--views-file FILE] [--no-store]

One call per judged photograph: the reader lists the permanent things in view (buildings, fences, walls, ground
finishes, planting, street objects, water), each with the few attributes its automatic rule needs. The buildings
the photograph shows are found by rays against LoD2 first, so the reader is told which building stands where and
its answer carries their entity ids. Everything else gets a position estimated from the camera, the place in the
image and the reader's distance.

The answer for an image is kept under $TURKU3D_WORK/observe/light/<image hash>-<instruction version>.json and
reused: a photograph is read once. The location's file is `turku-description/location/1`, stored through
turku3d.world.store as location/<name>: views, items and the area profile aggregated from the items in code
(typical materials, colours and types per kind), which turku3d.world.profile.near hands to the automatic rules of
unread neighbours.

A building the reader described and nobody has stored a description of gets a small stored description of its
own (style, wall material and colour, roofing, plinth; tier observed, provenance source `photo`): the automatic
fit with the things a glance settles. A full reading replaces it.
"""

from __future__ import annotations

import collections
import hashlib
import json
import math
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from turku3d import config
from turku3d.world import store

from . import lod2
from .against import colour_class, median_colour

MODEL = "claude-fable-5-2"
EFFORT = "low"
TIMEOUT_S = 10 * 60
VERSION = "light-1"
BY = "w2-observe light reader 1"
REACH_M = 250.0
LEAST_SHARE = 0.004  # of the photograph's pixels: a building smaller than this is not named to the reader
FULL_SHARE = 0.12  # a building filling this much of a judged view gets the full reading (describe)
WALL_FAMILIES = ["plaster", "brick", "weatherboard", "log", "concrete", "ceramic_tile", "sheet_metal", "stone",
                 "granite", "glass"]
ROOF_FAMILIES = ["sheet_metal", "felt", "clay_tile", "concrete_tile", "slate", "copper"]
COLOUR = {"type": "string", "pattern": "^#[0-9a-f]{6}$"}
INSTRUCTIONS = """\
You take stock of one street photograph from Turku, Finland, for a 3D city generator: everything permanent that \
stands in view, listed by kind with a few attributes each. The generator draws the place from registers and \
rules; your list tells it what the rules got wrong and what is typical of this area. Leave out people, vehicles, \
weather, shadows and anything temporary. Be honest: leave a field out when you cannot see it, and give a low \
`conf` to what is far, small or half hidden.

Method
1. Read the photograph named in the task (use the Read tool).
2. `x` is where the thing stands in the picture: [from, to] as shares of the picture's width, 0 at the left edge, \
1 at the right. `distance_m` is your estimate of how far it is from the camera (a storey is about 3 m, a car 4.5 m \
long, a lane 3 m wide).
3. Buildings: the task lists the buildings the city model puts in view, each with a letter and its `x` range. \
Describe each one you can see, under its letter; say `seen: false` for one that is hidden. A building in the \
picture that the list lacks (a shed, a garage, a new block) gets `label: null`. Wall and roof colours are the \
paint or material in overcast daylight as sRGB hex: correct for sun, shade and the camera.
4. Fences, walls, hedges: one entry per run. A fence's `family`: board (closed boards), picket (spaced boards), \
iron (metal bars), mesh (wire mesh, chain link), railing (a low rail on posts), stone-wall (stone, brick or \
concrete wall). A hedge with no fence in it is a `planting` entry, not a fence.
5. Ground: one entry per surface you can tell apart: the carriageway, each footway or cycleway, verges, yards, \
parking, paths; say what it is made of, its colour, the kerb beside it and the painted markings on it.
6. Planting: one entry per tree or group of like trees, per hedge, shrub group, lawn, flowerbed, wild verge or \
forest edge; `count` for a group; `species` only as far as you can tell (else other_broadleaf or other_conifer).
7. Objects: lamps, traffic signs, signals, poles and overhead wires, benches, bins, bollards, shelters, bike \
racks, cabinets, flagpoles, post boxes, barriers: one entry per kind and type with a `count`, the `type` in a \
few words that would let somebody pick the right model ('curved steel pole with cobra head', 'wooden pole with \
arm and wires').
8. `character`: one sentence that tells this kind of place from other parts of the city.
Answer with the JSON only."""


def _thing(required: list, properties: dict) -> dict:
    base = {"x": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            "distance_m": {"type": "number"}, "conf": {"type": "number", "minimum": 0, "maximum": 1}}
    return {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                       "required": [*required, "x", "conf"], "properties": {**properties, **base}}}


def _material(families: list) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["family"],
            "properties": {"family": {"enum": families}, "colour": COLOUR, "where": {"type": "string"}}}


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["character", "buildings", "fences", "ground", "planting", "objects"],
    "properties": {
        "character": {"type": "string"},
        "buildings": _thing(["label", "seen"], {
            "label": {"type": ["string", "null"]}, "seen": {"type": "boolean"},
            "type": {"enum": ["detached_house", "row_house", "wooden_house", "apartment_block", "tower_block", "hall",
                              "commercial", "office", "public", "church", "outbuilding", "garage", "kiosk"]},
            "storeys": {"type": "integer", "minimum": 1},
            "wall": _material(WALL_FAMILIES), "wall2": _material(WALL_FAMILIES),
            "roof": {"type": "object", "additionalProperties": False, "properties": {
                "form": {"enum": ["flat", "shed", "gable", "hip", "half_hip", "mansard", "pyramid", "complex"]},
                "family": {"enum": ROOF_FAMILIES}, "colour": COLOUR}},
            "ground_floor": {"enum": ["plain", "shopfront", "garage", "other_material"]},
            "balconies": {"enum": ["none", "cantilever", "recessed", "glazed", "french"]},
            "plinth": {"type": "object", "additionalProperties": False, "properties": {
                "family": {"enum": ["granite", "rubble", "concrete", "plaster", "brick", "stone"]},
                "height_m": {"type": "number"}}},
            "windows": {"enum": ["single", "two_leaf", "three_leaf", "t_frame", "cross", "grid", "side_vent",
                                 "top_vent", "ribbon"]},
            "trim": COLOUR, "chimneys": {"type": "integer"}, "dormers": {"type": "integer"},
            "note": {"type": "string"}}),
        "fences": _thing(["family"], {
            "family": {"enum": ["board", "picket", "iron", "mesh", "railing", "stone-wall"]},
            "height_m": {"type": "number"}, "colour": COLOUR, "hedge": {"type": "boolean"},
            "side": {"enum": ["left", "right", "across"]}, "note": {"type": "string"}}),
        "walls": _thing(["material"], {
            "material": {"enum": ["granite", "rubble", "concrete", "brick", "plaster", "stone"]},
            "height_m": {"type": "number"}, "colour": COLOUR, "note": {"type": "string"}}),
        "ground": _thing(["what", "surface"], {
            "what": {"enum": ["carriageway", "footway", "cycleway", "verge", "yard", "parking", "square", "path",
                              "track", "driveway"]},
            "surface": {"enum": ["asphalt", "sett", "cobble", "concrete_slab", "paving_stone", "gravel", "grass",
                                 "bare", "sand"]},
            "colour": COLOUR, "kerb": {"enum": ["granite", "concrete", "none"]},
            "markings": {"type": "array", "items": {"enum": [
                "centre_line", "edge_line", "lane_line", "crossing", "stop_line", "parking_bay", "cycle_symbol",
                "arrow", "give_way_teeth", "speed_bump", "none"]}},
            "width_m": {"type": "number"}}),
        "planting": _thing(["what"], {
            "what": {"enum": ["tree", "hedge", "shrub", "lawn", "flowerbed", "wild_verge", "forest_edge", "climber"]},
            "species": {"enum": ["birch", "lime", "maple", "oak", "rowan", "fruit", "other_broadleaf", "pine",
                                 "spruce", "thuja", "other_conifer"]},
            "form": {"enum": ["clipped", "loose", "columnar", "round", "spreading", "weeping"]},
            "height_m": {"type": "number"}, "count": {"type": "integer", "minimum": 1},
            "note": {"type": "string"}}),
        "objects": _thing(["what"], {
            "what": {"enum": ["lamp", "sign", "signal", "pole", "wire", "bench", "bin", "bollard", "shelter",
                              "bike_rack", "cabinet", "flagpole", "mailbox", "post_box", "hydrant", "playground",
                              "barrier", "other"]},
            "type": {"type": "string"}, "height_m": {"type": "number"}, "colour": COLOUR,
            "count": {"type": "integer", "minimum": 1}}),
        "water": _thing(["what"], {"what": {"enum": ["river", "sea", "pond", "ditch", "fountain"]},
                                    "edge": {"type": "string"}}),
    },
}
KINDS = {"buildings": "building", "fences": "fence", "walls": "wall", "ground": "ground", "planting": "planting",
         "objects": "object", "water": "water"}
STYLES = {"detached_house": "detached_house", "row_house": "detached_house", "wooden_house": "wooden_house",
          "apartment_block": None, "tower_block": "tower_block", "hall": "industrial", "commercial": "commercial",
          "office": "office", "public": "public", "church": "church", "outbuilding": "outbuilding",
          "garage": "garage", "kiosk": "kiosk"}


def image_path(view: dict) -> Path:
    path = Path(view["image"])
    return path if path.is_absolute() else config.data_root().parent / path


def visible(view: dict, scene: lod2.Scene, grid=(192, 144)) -> list[dict]:
    """The buildings a view shows, left to right: key, x range, share of the pixels, distance, register facts."""
    from qalib.camera import pixel_rays

    pose = view["pose"]
    rays = pixel_rays(pose, *grid).reshape(-1, 3)
    cam = np.array([pose["E"], pose["N"], pose["H"]])
    hit, who = scene.cast(cam, cam + rays * REACH_M, stop_short=0.0)
    columns = np.tile(np.arange(grid[0]), grid[1])
    found = []
    for index in np.unique(who[np.isfinite(hit)]):
        mine = (who == index) & np.isfinite(hit)
        share = float(mine.mean())
        if share < LEAST_SHARE:
            continue
        key = scene.ids[index]
        seen = columns[mine]
        found.append({"key": key, "x": [round(float(seen.min()) / grid[0], 2), round(float(seen.max() + 1) / grid[0], 2)],
                      "share": round(share, 3), "distance_m": round(float(np.percentile(hit[mine], 20)), 0),
                      "register": scene.buildings[key].register})
    found.sort(key=lambda b: (b["x"][0] + b["x"][1]) / 2)
    for letter, building in zip("ABCDEFGHJKLMNPQRSTUVWXYZ", found, strict=False):
        building["label"] = letter
    return [b for b in found if "label" in b]


def task_text(view: dict, buildings: list) -> str:
    lines = [f"Read the photograph {image_path(view).name}.", f"About the view: {view.get('source', {}).get('note', '')}".strip(),
             "", "Buildings the city model puts in view (letter: x range, distance, register facts):"]
    for b in buildings:
        r = b["register"]
        facts = ", ".join(str(v) for v in (r.get("address"), r.get("year") and f"built {r['year']}",
                                           r.get("storeys") and f"{r['storeys']} storeys in the register") if v)
        lines.append(f"  {b['label']}: x {b['x'][0]:.2f}-{b['x'][1]:.2f}, about {b['distance_m']:.0f} m; {facts or 'no register facts'}")
    if not buildings:
        lines.append("  (none)")
    return "\n".join(lines)


def ask(view: dict, buildings: list, cache: Path, *, model: str = MODEL, effort: str = EFFORT) -> dict:
    """The reading of one photograph: from the cache when this image was read with these instructions before."""
    picture = image_path(view)
    digest = hashlib.sha256(picture.read_bytes()).hexdigest()
    kept = cache / f"{digest[:24]}-{VERSION}.json"
    if kept.exists():
        return {**json.loads(kept.read_text()), "reused": True}
    task = task_text(view, buildings)
    args = ["claude", "-p", "--model", model, "--effort", effort, "--system-prompt", INSTRUCTIONS,
            "--tools", "Read", "--allowedTools", "Read", "--add-dir", str(picture.parent), "--strict-mcp-config",
            "--disable-slash-commands", "--no-session-persistence", "--setting-sources", "",
            "--json-schema", json.dumps(SCHEMA), "--output-format", "json", task]
    started = time.time()
    done = subprocess.run(args, cwd=picture.parent, capture_output=True, text=True, timeout=TIMEOUT_S,
                          stdin=subprocess.DEVNULL, check=False)
    try:
        answer = json.loads(done.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"light reader: no JSON answer ({done.stderr[:300] or done.stdout[:300]})") from error
    if done.returncode or answer.get("is_error"):
        raise RuntimeError(f"light reader: {answer.get('result') or done.stderr[:300]}")
    reading = answer.get("structured_output")
    if reading is None:
        reading = json.loads(str(answer.get("result", "")).strip().removeprefix("```json").removesuffix("```").strip())
    saved = {"image_sha256": digest, "instruction": VERSION, "task": task, "reading": reading, "buildings": buildings,
             "reader": {"model": next(iter(answer.get("modelUsage") or {}), model), "effort": effort,
                        "seconds": round(time.time() - started, 1), "cost_usd": answer.get("total_cost_usd"),
                        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}}
    cache.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps(saved, indent=1, ensure_ascii=False))
    return saved


def placed(view: dict, x: list, distance: float | None) -> list | None:
    """[E, N] of something at share x of the picture's width, `distance` metres from the camera."""
    if not distance:
        return None
    pose = view["pose"]
    half = math.tan(math.radians(pose["fov"]) / 2) * view.get("width", 4) / view.get("height", 3)
    bearing = math.radians(pose["yaw"]) + math.atan((sum(x) / len(x) * 2 - 1) * half)
    return [round(pose["E"] + distance * math.sin(bearing), 1), round(pose["N"] + distance * math.cos(bearing), 1)]


def items_of(view: dict, saved: dict) -> list[dict]:
    by_label = {b["label"]: b for b in saved["buildings"]}
    items = []
    for group, kind in KINDS.items():
        for thing in saved["reading"].get(group, []):
            item = {"kind": kind, "view": view["id"], **thing}
            if kind == "building":
                known = by_label.get(item.pop("label", None))
                if not item.pop("seen", True):
                    continue
                if known:
                    item.update(entity=f"building/{known['key']}", share=known["share"], distance_m=known["distance_m"])
            where = placed(view, item["x"], item.get("distance_m"))
            if where:
                item["at"] = where
            items.append(item)
    return items


def _shares(values: list) -> dict:
    counts = collections.Counter(v for v in values if v is not None)
    total = sum(counts.values())
    return {str(name): round(n / total, 2) for name, n in counts.most_common()} if total else {}


def _median(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 1) if values else None


def _typical(rows: list, keys: tuple, colour: str = "colour", weight: str | None = None) -> list:
    """Rows grouped by `keys` and by colour class: the median colour and the share of each group, largest first."""
    groups = collections.defaultdict(list)
    for row in rows:
        if row and row.get(keys[0]):
            groups[(*(row.get(k) for k in keys), colour_class(row.get(colour)))].append(row)
    total = sum(r.get(weight, 1) if weight else 1 for group in groups.values() for r in group) or 1
    out = []
    for group_key, group in groups.items():
        colours = [r[colour] for r in group if r.get(colour)]
        entry = {k: v for k, v in zip(keys, group_key, strict=False) if v is not None}
        if colours:
            entry["colour"] = median_colour(colours)
        entry["share"] = round(sum(r.get(weight, 1) if weight else 1 for r in group) / total, 2)
        heights = _median([r.get("height_m") for r in group])
        if heights is not None:
            entry["height_m"] = heights
        out.append(entry)
    return sorted(out, key=lambda e: -e["share"])


def profile(items: list) -> dict:
    """The area profile: per kind the typical values with their shares, from the items of every view."""
    best = {}
    for item in (i for i in items if i["kind"] == "building"):  # a building seen in two views counts once
        key = item.get("entity") or (item["view"], tuple(item["x"]))
        if key not in best or item["conf"] > best[key]["conf"]:
            best[key] = item
    buildings = list(best.values())
    of = lambda kind: [i for i in items if i["kind"] == kind]  # noqa: E731
    out = {}
    if buildings:
        out["building"] = {
            "count": len(buildings), "types": _shares([b.get("type") for b in buildings]),
            "storeys": _shares([b.get("storeys") for b in buildings]),
            "wall": _typical([b.get("wall") for b in buildings], ("family",)),
            "roof": _typical([b.get("roof") for b in buildings], ("form", "family")),
            "plinth": _shares([b.get("plinth", {}).get("family") for b in buildings]),
            "plinth_m": _median([b.get("plinth", {}).get("height_m") for b in buildings]),
            "ground_floor": _shares([b.get("ground_floor") for b in buildings]),
            "balconies": _shares([b.get("balconies") for b in buildings]),
            "windows": _shares([b.get("windows") for b in buildings]),
            "trim": median_colour([b["trim"] for b in buildings if b.get("trim")]) if any(b.get("trim") for b in buildings) else None,
            "chimneys": round(statistics.fmean(min(b.get("chimneys", 0), 1) for b in buildings), 2),
            "dormers": round(statistics.fmean(min(b.get("dormers", 0), 1) for b in buildings), 2),
        }
    fences, hedges = of("fence"), [p for p in of("planting") if p["what"] == "hedge"]
    if fences or hedges:
        out["fence"] = {"count": len(fences), "families": _typical(fences, ("family",)),
                        "hedge": {"count": len(hedges), "with_fence": sum(bool(f.get("hedge")) for f in fences),
                                  "forms": _shares([h.get("form") for h in hedges]),
                                  "species": _shares([h.get("species") for h in hedges]),
                                  "height_m": _median([h.get("height_m") for h in hedges])}}
    if of("wall"):
        out["wall"] = _typical(of("wall"), ("material",))
    ground = collections.defaultdict(list)
    for item in of("ground"):
        ground[item["what"]].append(item)
    if ground:
        out["ground"] = {what: {"surfaces": _typical(rows, ("surface",)), "kerb": _shares([r.get("kerb") for r in rows]),
                                "markings": _shares([m for r in rows for m in r.get("markings", [])]),
                                "width_m": _median([r.get("width_m") for r in rows])} for what, rows in ground.items()}
    planting = collections.defaultdict(list)
    for item in of("planting"):
        planting[item["what"]].append(item)
    if planting:
        out["planting"] = {what: _typical([{**r, "n": r.get("count", 1)} for r in rows], ("species", "form") if what in ("tree", "hedge", "shrub") else ("what",),
                                          weight="n") for what, rows in planting.items()}
    objects = collections.defaultdict(list)
    for item in of("object"):
        objects[item["what"]].append(item)
    if objects:
        out["object"] = {what: [{k: r[k] for k in ("type", "height_m", "colour", "count") if k in r} for r in rows]
                         for what, rows in objects.items()}
    if of("water"):
        out["water"] = [{k: w[k] for k in ("what", "edge") if k in w} for w in of("water")]
    return out


def building_description(item: dict, provenance: dict) -> dict | None:
    """The small stored description a glance at a building gives: style, wall, roofing, plinth."""
    wall = item.get("wall")
    if not wall or not wall.get("colour") or item["conf"] < 0.5:
        return None
    conf = {"*": round(min(item["conf"], 0.6), 2)}
    doc = {"schema": "turku-description/building/1", "id": item["entity"], "tier": "observed",
           "provenance": [provenance], "materials": {"wall": {"family": wall["family"], "colour": wall["colour"], "conf": conf}},
           "wall_defaults": {"material": "wall", "conf": conf}, "src": provenance["key"], "conf": conf}
    style = STYLES.get(item.get("type"))
    if style:
        doc["style"] = {"type": style, "conf": conf}
    roof = item.get("roof", {})
    if roof.get("family"):
        roofing = {"family": roof["family"], **({"colour": roof["colour"]} if roof.get("colour") else {}), "conf": conf}
        doc["materials"]["roof"] = roofing
        doc["roof"] = {"material": "roof", **({"form_hint": roof["form"]} if roof.get("form") else {}), "conf": conf}
    plinth = item.get("plinth", {})
    if plinth.get("family"):
        doc["materials"]["plinth"] = {"family": plinth["family"], "conf": conf}
        doc["wall_defaults"]["plinth"] = {"material": "plinth", **({"height_m": plinth["height_m"]} if plinth.get("height_m") else {})}
    if item.get("note"):
        doc["notes"] = item["note"]
    return doc


def run(name: str, views: list[dict], root: Path, *, effort: str = EFFORT, write: bool = True) -> dict:
    """Reads the views of one location, stores its location file and the small building descriptions."""
    e, n = [v["pose"]["E"] for v in views], [v["pose"]["N"] for v in views]
    scene = lod2.Scene((min(e) - REACH_M - 50, min(n) - REACH_M - 50, max(e) + REACH_M + 50, max(n) + REACH_M + 50))
    items, records, cost, seconds, characters, reused = [], [], 0.0, 0.0, [], 0
    for view in views:
        saved = ask(view, visible(view, scene), root / "light", effort=effort)
        if saved.get("reused"):
            reused += 1
        else:
            cost += saved["reader"].get("cost_usd") or 0.0
            seconds += saved["reader"]["seconds"]
        items += items_of(view, saved)
        characters.append(saved["reading"].get("character", ""))
        records.append({"id": view["id"], "image_sha256": saved["image_sha256"], "pose": view["pose"],
                        "reader": saved["reader"], "buildings": [{k: b[k] for k in ("label", "key", "x", "share")} for b in saved["buildings"]]})
    centre = [round(statistics.fmean(e), 1), round(statistics.fmean(n), 1)]
    reach = max((math.dist(centre, i["at"]) for i in items if "at" in i), default=60.0)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    key = f"light-{now}"
    doc = {"schema": "turku-description/location/1", "id": f"location/{name}", "tier": "observed",
           "provenance": [{"source": "photo", "key": key, "at": now, "by": BY,
                           "note": f"{MODEL} effort {effort}, {len(views)} judged photographs, instruction {VERSION}"}],
           "at": centre, "radius_m": round(min(max(reach, 40.0), REACH_M), 0), "character": " ".join(c for c in characters if c),
           "views": records, "items": items, "profile": profile(items)}
    full = sorted({i["entity"] for i in items if i["kind"] == "building" and i.get("share", 0) >= FULL_SHARE and "entity" in i})
    small = []
    if write:
        store.write_description(doc, BY, f"light reading of {len(views)} photographs")
        for item in items:
            if item["kind"] == "building" and "entity" in item and store.read_description(item["entity"]) is None:
                description = building_description(item, {"source": "photo", "key": key, "at": now, "by": BY,
                                                          "note": f"a glance in judged view {item['view']}"})
                if description:
                    store.write_description(description, BY, f"from the light reading of {name}")
                    small.append(item["entity"])
    out = root / "locations" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    return {"location": name, "views": len(views), "items": dict(collections.Counter(i["kind"] for i in items)),
            "cost_usd": round(cost, 3), "read_s": round(seconds, 1), "reused": reused,
            "full_reading_for": full, "small_descriptions": small, "file": str(out)}
