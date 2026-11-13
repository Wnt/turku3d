"""Read a building's crops into its description with the vision model.

One call per building: the model gets the context views and the rectified wall crops (prepare.py) and answers in
the building description format itself (docs/specs/building-description.md), restricted by a JSON schema derived
from the format's own schema. The call goes through the `claude` CLI as scripts/dev/scene-judge.mjs does: Read tool
only, no settings, no session. Time and notional cost of every call are recorded.
"""

from __future__ import annotations

import copy
import json
import math
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SPEC = REPO / "docs/specs/building-description.md"
SCHEMA = REPO / "docs/specs/building-description.schema.json"
MODEL = "claude-fable-5-2"
EFFORT = "low"
TIMEOUT_S = 20 * 60
# Fields the pipeline writes itself, never the reader.
ENVELOPE = ("schema", "id", "tier", "register", "shell", "provenance", "src", "site", "components")
END_REACH_M = 2.5  # a facade end the reader places this close to the wall's end is that end
END_SEEN_M = 0.5  # and the crop's free line of sight must reach this close to it
SCALE_REACH = 0.06  # a facade read this much longer or shorter than the wall is still the same facade
U_FIELDS = ("u_m", "u_from_m", "u_to_m", "first_m")
V_FIELDS = ("v_m", "from_m", "to_m", "ground_floor_m")  # heights above the wall base
GROUND_REACH_M = 1.5  # a ground line read further than this from the surveyed ground is not that ground
GROUND_OUT_M = 0.8  # the surveyed ground is taken this far in front of the wall
QUESTION = "Question."  # a second look that asks its own question starts its text with this (task_text)

INSTRUCTIONS = """\
You measure one real building in Turku, Finland from street photographs, so that a 3D generator can rebuild it and \
somebody who knows the city recognises it. You write a description, never an image. Be exact and honest: a wrong \
count or colour makes the building unrecognisable, and a guess presented as a measurement is worse than a gap.

Method
1. Read FORMAT.md in the packet directory: it defines the description you fill (walls, storeys, bays, opening \
types, materials, bands, roof). Then read every image named in the task, context views first.
2. Context views are plain photographs of the whole building: use them for the roof, depth (balconies, bay \
windows, recesses), the overall character and for telling the building from its neighbours.
3. Wall crops are rectified onto one wall plane, with metric rulers in the white margin: the bottom ruler is `u` in \
metres from the wall's left end, the left ruler is `v` in metres above the wall's base. Read positions and sizes \
off the rulers. The red bars mark where the city model puts the wall; the photograph can be shifted against them \
by a metre or two, so trust the picture for where this building's facade really starts and ends, and report that \
as `facade_u_m` for each crop. Things in front of the wall plane (vehicles, trees, poles) and behind it (roof \
slopes, recessed balconies, side walls) are displaced or stretched by the rectification: do not measure them, and \
do not mistake a neighbour's facade for part of this wall. Measure each wall on one crop, the clearest and most \
complete one: name that file in the wall's `measured_on` and write every `u` and `v` of the wall as read off that \
crop's rulers, uncorrected (the pipeline moves them onto the wall with your `facade_u_m`). Use the wall's other \
crops to confirm counts and colours and to see what is hidden in the first.
4. Count on the clearest storey and check the count on a second one. Bays are window axes: give each bay's centre \
`u_m` when the rhythm is irregular, a grid when it is regular. Storeys go bottom to top; `height_m` is floor to \
floor, which you get from the spacing of successive sill lines. A storey whose openings differ from bay to bay \
gets a `row`; shopfronts, gateways and doors that ignore the bays are placed `openings` with `u_m`, `w_m`.
5. Describe every distinct opening once under `opening_types` (size from the rulers, head shape, light pattern, \
frame colour, surround) and refer to it by name. Name materials under `materials` and refer to them by name.
6. Colours are the paint or material colour in overcast daylight as sRGB hex: correct for sun, shadow and the \
camera's white balance by comparing sunlit and shaded parts and the different photographs.
7. Give `identity_cues`: the few things that make this building recognisable from the street.
8. Write one `walls` entry for every wall id listed in the task that has a crop, using exactly that id, without \
`frame`. When two walls are alike, describe one fully and give the other `like` plus what differs. Put what an \
unphotographed wall of this building most probably looks like in `wall_defaults` (a bay grid with `pitch_m` \
alone, so that it fits walls of any length; storeys; material), with confidence 0.2-0.4.
9. Confidence: every object you write carries `conf` with at least `"*"`. 0.9 measured on a clear frontal crop and \
confirmed in a second view; 0.6-0.8 read from one clear view; 0.3-0.5 oblique, distant or partly hidden; leave a \
field out when you cannot see it. Never write `src`.
10. The register facts in the task are priors from the city's records. Storey counts there may follow other rules \
(basements, attics): report what the photographs show.
11. For each image fill a `views` entry: whether it was usable, the share of the wall hidden by foreground \
objects, and what was hidden or wrong. For a wall crop also give `ground_uv_m`: one point [u, v] read off the \
rulers where you clearly see the ground (pavement, asphalt, lawn) meet this wall; leave it out when vehicles or \
planting hide the foot of the wall. The pipeline uses it to correct the camera's height.
12. Give every wall `ground_floor_m`: the ruler height of the ground storey's floor (the top of the plinth; the \
door threshold). Before answering, check each wall: every bay and opening lies between the facade's ends that you \
report, with its full width; from `ground_floor_m` the storey heights add up to where you see the eaves, within \
0.3 m; no two openings of one storey overlap; windows in a gable or dormer above the eaves are not a wall storey.
13. Roof: count the dormers and chimneys you see above each wall and place each on that wall's ruler. Name a \
dormer's type by its own little roof: `gable` when it ends in a triangle, `shed` when it has one slope, `hip`, \
`flat`, `arched`, `eyebrow`, or `wall_dormer` when the wall face itself rises through the eaves.
14. Balconies: decide the type from a context view, not from a rectified crop. `recessed` when the balcony floor \
lies behind the wall plane (the wall runs on flush beside and above it and you look into a niche; give the \
niche's `depth_m`); `cantilever` when a slab stands out of the wall (you see its underside, its end or its shadow \
on the wall; `depth_m` is how far it stands out); `glazed` only for a glazed box that stands out of the wall (a \
glazed balcony inside a niche is `recessed`); `french` for a railing directly before a door. Give the \
railing's colour as the panel looks in overcast light, like every other colour.
"""


def reading_schema() -> dict:
    """The format's schema without the envelope, wrapped with the reader's per-image report."""
    schema = json.loads(SCHEMA.read_text())
    description = {k: v for k, v in schema.items() if k not in ("$schema", "$id", "$defs", "required", "title")}
    description["properties"] = {k: v for k, v in schema["properties"].items() if k not in ENVELOPE}
    description["required"] = ["style", "identity_cues", "materials", "opening_types", "walls", "roof"]
    defs = copy.deepcopy(schema["$defs"])
    defs["wall"]["properties"].pop("frame", None)
    defs["wall"]["properties"]["measured_on"] = {"type": "string", "description": "File name of the crop whose rulers this wall's u and v follow."}
    defs["wall"]["required"] = sorted({*defs["wall"].get("required", []), "id", "measured_on"})
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["description", "views"],
        "properties": {
            "description": description,
            "views": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["file", "usable", "hidden_share"],
                    "properties": {
                        "file": {"type": "string"},
                        "usable": {"type": "boolean"},
                        "hidden_share": {"type": "number", "minimum": 0, "maximum": 1},
                        "facade_u_m": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                        "ground_uv_m": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                        "hidden_m": {"type": "array", "items": {"type": "array", "items": {"type": "number"},
                                                               "minItems": 4, "maxItems": 4}},
                        "problem": {"type": "string"},
                    },
                },
            },
            "notes": {"type": "string"},
        },
        "$defs": defs,
    }


def correction_schema() -> dict:
    """What a second look answers with: the corrected walls, the opening types and materials that changed, the
    roof when it was asked about, and the images looked at."""
    whole = reading_schema()
    description = whole["properties"]["description"]["properties"]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["walls", "views"],
        "properties": {"walls": description["walls"], "opening_types": description["opening_types"],
                       "materials": description["materials"], "roof": description["roof"],
                       "views": whole["properties"]["views"], "notes": {"type": "string"}},
        "$defs": whole["$defs"],
    }


def corrected(reading: dict, correction: dict) -> dict:
    """The first reading with a second look's walls, opening types, materials, roof and image reports laid over
    it. A roof given replaces the whole roof."""
    out = copy.deepcopy(reading)
    walls = {w.get("id"): w for w in correction.get("walls", [])}
    known = {w.get("id") for w in out["description"].get("walls", [])}
    out["description"]["walls"] = ([walls.get(w.get("id"), w) for w in out["description"].get("walls", [])]
                                   + [w for w in correction.get("walls", []) if w.get("id") not in known])
    out["description"].setdefault("opening_types", {}).update(correction.get("opening_types") or {})
    out["description"].setdefault("materials", {}).update(correction.get("materials") or {})
    if correction.get("roof"):
        out["description"]["roof"] = correction["roof"]
    views = {v["file"]: v for v in correction.get("views", [])}
    out["views"] = [views.pop(v["file"], v) for v in out.get("views", [])] + list(views.values())
    out["notes"] = correction.get("notes", "")  # what this look changed, not the notes of the reading before it
    return out


def task_text(record: dict, packet: Path, feedback_text: str = "", first: dict | None = None) -> str:
    reg = record["register"]
    lines = [f"The packet directory is {packet}. Read {packet / 'FORMAT.md'} first.", "",
             "Building (register priors): "
             + ", ".join(f"{k} {v}" for k, v in reg.items() if v is not None) + ".", "", "Walls of the city model:"]
    seen = {c["wall"] for c in record["crops"]}
    asked = None  # a second look: only the walls the generator complained about, and only their crops
    if first is not None:  # the walls the text names: read before, or (a question) any wall of the building
        named = [w["id"] for w in first["description"].get("walls", [])]
        if feedback_text.startswith(QUESTION):
            named += [w["id"].split(":")[-1] for w in record["walls"]]
        asked = {wall for wall in named if f"- wall {wall} " in feedback_text}
    for w in record["walls"]:
        short = w["id"].split(":")[-1]
        if asked is not None and short not in asked:
            continue
        state = "has crops" if w["id"] in seen else "no crop"
        extra = ""
        if w["adjoining"]:
            extra = "; a neighbour stands against it at u " + ", ".join(
                f"{j['x0']:.0f}-{j['x1']:.0f} m (up to v {j['eaves'] - w['base_h']:.0f} m)" for j in w["adjoining"])
        lines.append(f"- {short}: {w['length_m']:.1f} m long, faces {w['bearing_deg']:.0f} deg, eaves at v "
                     f"{w['eaves_h'] - w['base_h']:.1f} m, top at v {w['top_h'] - w['base_h']:.1f} m, {state}{extra}")
    lines += ["", "Images:"]
    question = feedback_text.startswith(QUESTION)  # its own question may be about the roof: every context view
    for c in record["contexts"][:1] if asked is not None and not question else record["contexts"]:
        p = c["panorama"]
        lines.append(f"- {Path(c['file']).name}: context view, {p['provider']} {p['date']}, "
                     f"{c['distance_m']:.0f} m from the building, looking {c['pose']['yaw']:.0f} deg")
    for c in record["crops"]:
        if asked is not None and c["wall"].split(":")[-1] not in asked:
            continue
        p = c["panorama"]
        lines.append(f"- {Path(c['file']).name}: wall {c['wall'].split(':')[-1]} rectified, {p['provider']} "
                     f"{p['date']}, camera {c['distance_m']:.0f} m from the wall plane, {c['oblique_deg']:.0f} deg "
                     f"off frontal, {c['px_per_m']:.0f} px/m, line of sight free for u {c['x0_m']:.1f}-"
                     f"{c['x1_m']:.1f} m")
    if feedback_text.startswith("Review round."):
        lines += ["", feedback_text]
    elif asked is not None and feedback_text.startswith(QUESTION):
        mine = first["description"]
        lines += ["", feedback_text, "", "Your first reading of what is asked about:",
                  json.dumps({"walls": [w for w in mine.get("walls", []) if w.get("id") in asked],
                              "roof": mine.get("roof", {}), "materials": mine.get("materials", {}),
                              "opening_types": sorted(mine.get("opening_types", {}))}, ensure_ascii=False)]
        return "\n".join(lines)
    elif asked is not None:
        mine = first["description"]
        lines += ["", "A first reading of this building was given to the generator, which could not build the "
                  "parts below (positions on the rulers of the crop each wall was measured on). Look at those "
                  "places again. Correct what the photographs show was misread (a position, a width, a storey "
                  "height, a window that belongs to the roof or to a neighbour); keep what the photographs confirm.",
                  feedback_text, "",
                  "Answer with `walls`: the whole corrected entry of each wall named above (it replaces the first "
                  "one; keep its id and `measured_on`), with `opening_types` only for types you add or change, and "
                  "with the report of the images you looked at. The first reading of those walls, its opening "
                  "types and material names:",
                  json.dumps({"walls": [w for w in mine.get("walls", []) if w.get("id") in asked],
                              "opening_types": mine.get("opening_types", {}),
                              "materials": sorted(mine.get("materials", {}))}, ensure_ascii=False)]
        return "\n".join(lines)
    elif feedback_text:
        lines += ["", "A first reading of these images was given to the generator, which could not build the parts "
                  "below (positions on the rulers of the crop each wall was measured on). Look at those places "
                  "again. Correct what the photographs show was misread (a position, a width, a storey height, a "
                  "window that belongs to the roof or to a neighbour); keep what the photographs confirm.",
                  feedback_text]
    lines += ["", "Answer with the description of this building and the per-image report."]
    return "\n".join(lines)


def read(record: dict, packet: Path, *, effort: str = EFFORT, model: str = MODEL,
         feedback_text: str = "", first: dict | None = None) -> tuple[dict, dict]:
    """(reading, meta) for one prepared building. Raises on a failed call.

    With `first` (an earlier reading) and the generator's complaints as `feedback_text`, only the walls complained
    about are read again, and the reading returned is the first one with those walls corrected."""
    shutil.copyfile(SPEC, packet / "FORMAT.md")
    task = task_text(record, packet, feedback_text, first)
    (packet / "task.txt").write_text(task)
    args = ["claude", "-p", "--model", model, "--effort", effort, "--system-prompt", INSTRUCTIONS,
            "--tools", "Read", "--allowedTools", "Read", "--add-dir", str(packet), "--strict-mcp-config",
            "--disable-slash-commands", "--no-session-persistence", "--setting-sources", "",
            "--json-schema", json.dumps(reading_schema() if first is None else correction_schema()),
            "--output-format", "json", task]
    started = time.time()
    done = subprocess.run(args, cwd=packet, capture_output=True, text=True, timeout=TIMEOUT_S,
                          stdin=subprocess.DEVNULL, check=False)
    seconds = time.time() - started
    try:
        answer = json.loads(done.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"reader: no JSON answer ({done.stderr[:400] or done.stdout[:400]})") from error
    if done.returncode or answer.get("is_error"):
        raise RuntimeError(f"reader: {answer.get('result') or done.stderr[:400]}")
    reading = answer.get("structured_output")
    if reading is None:
        text = str(answer.get("result", "")).strip().removeprefix("```json").removesuffix("```").strip()
        reading = json.loads(text)
    if first is not None:
        reading = corrected(first, reading)
    usage = answer.get("usage") or {}
    meta = {
        "model": next(iter(answer.get("modelUsage") or {}), model), "effort": effort,
        "seconds": round(seconds, 1), "cost_usd": answer.get("total_cost_usd"), "turns": answer.get("num_turns"),
        "input_tokens": (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0),
        "cached_tokens": usage.get("cache_read_input_tokens") or 0,  # of input_tokens, read from the cache
        "output_tokens": usage.get("output_tokens"), "images": sum(".jpg: " in line for line in task.splitlines()),
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return reading, meta


def _strip(node, drop=("src",)):
    """The reading without fields only the pipeline may write."""
    if isinstance(node, dict):
        return {k: _strip(v, drop) for k, v in node.items() if k not in drop}
    if isinstance(node, list):
        return [_strip(v, drop) for v in node]
    return node


def registration(length: float, crop: dict, facade) -> tuple[float, float]:
    """(offset, scale) with wall u = (ruler u - offset) * scale, from where the reader saw the facade's ends.

    A panorama's pose is off by a metre or two, which shifts the picture along the wall. An end counts when the
    crop's line of sight reaches the wall's end and the reader places the facade's end near it."""
    if not facade or len(facade) != 2 or facade[1] - facade[0] < 1.0:
        return 0.0, 1.0
    f0, f1 = float(facade[0]), float(facade[1])
    left = crop["x0_m"] <= END_SEEN_M and abs(f0) <= END_REACH_M
    right = crop["x1_m"] >= length - END_SEEN_M and abs(f1 - length) <= END_REACH_M
    if left and right:
        scale = length / (f1 - f0)
        return (f0, scale) if abs(scale - 1.0) <= SCALE_REACH else (0.0, 1.0)
    if left:
        return f0, 1.0
    if right:
        return f1 - length, 1.0
    return 0.0, 1.0


def ground_shift(geometry: dict, seen, offset: float, scale: float, ground) -> float | None:
    """How far above the true ground line the crop shows the ground: the camera height error on this wall.

    `seen` is the reader's [u, v] of a point where the ground meets the wall; `ground(e, n)` the surveyed ground."""
    if ground is None or not seen or len(seen) != 2:
        return None
    a, b = geometry["a"], geometry["b"]
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length < 1e-6:
        return None
    t = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
    u = min(max((seen[0] - offset) * scale, 0.0), length)
    height = ground(a[0] + t[0] * u + t[1] * GROUND_OUT_M, a[1] + t[1] * u - t[0] * GROUND_OUT_M)
    if height is None:
        return None
    shift = seen[1] - (height - geometry["base_h"])
    return shift if abs(shift) <= GROUND_REACH_M else None


def _register(node, offset: float, scale: float, shift: float = 0.0):
    """The wall's fields moved from the crop's rulers onto the wall: positions along it, and heights above its
    base when the crop showed the ground too high or too low."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            number = isinstance(v, (int, float)) and not isinstance(v, bool)
            if k == "conf":  # confidences are keyed by field name: not positions
                out[k] = v
            elif k in U_FIELDS and number:
                out[k] = round((v - offset) * scale, 2)
            elif k == "pitch_m" and number:
                out[k] = round(v * scale, 3)
            elif k in V_FIELDS and number and shift:
                out[k] = round(v - shift, 2) if k == "v_m" else round(max(0.0, v - shift), 2)
            elif k == "plinth" and isinstance(v, dict) and shift and isinstance(v.get("height_m"), (int, float)):
                out[k] = {**_register(v, offset, scale, shift), "height_m": round(max(0.05, v["height_m"] - shift), 2)}
            else:
                out[k] = _register(v, offset, scale, shift)
        return out
    if isinstance(node, list):
        return [_register(v, offset, scale, shift) for v in node]
    return node


def _anchors(node, moved: dict):
    """Roof parts anchored along a wall, moved as that wall's positions were."""
    if isinstance(node, dict):
        if node.get("wall") in moved and isinstance(node.get("u_m"), (int, float)):
            offset, scale = moved[node["wall"]]
            node = {**node, "u_m": round((node["u_m"] - offset) * scale, 2)}
        return {k: _anchors(v, moved) for k, v in node.items()}
    if isinstance(node, list):
        return [_anchors(v, moved) for v in node]
    return node


def wall_registration(record: dict, reading: dict, wall_id: str, ground=None) -> tuple[float, float, float]:
    """(offset, scale, shift) of a read wall: wall u = (ruler u - offset) * scale, wall v = ruler v - shift."""
    geometry = next((w for w in record["walls"] if w["id"].split(":")[-1] == wall_id), None)
    wall = next((w for w in reading["description"].get("walls", []) if w.get("id") == wall_id), None)
    if geometry is None or wall is None:
        return 0.0, 1.0, 0.0
    report = {v["file"]: v for v in reading.get("views", [])}
    ruler = next((c for c in record["crops"] if c["wall"] == geometry["id"]
                  and Path(c["file"]).name == wall.get("measured_on")), None)
    if ruler is None:
        return 0.0, 1.0, 0.0
    seen = report.get(Path(ruler["file"]).name, {})
    offset, scale = registration(geometry["length_m"], ruler, seen.get("facade_u_m"))
    return offset, scale, ground_shift(geometry, seen.get("ground_uv_m"), offset, scale, ground) or 0.0


def feedback(record: dict, reading: dict, complaints: dict, ground=None) -> str:
    """The generator's complaints about a first reading, in the rulers of the crops it was measured on."""
    lines = []
    for wall_id, items in complaints.items():
        offset, scale, shift = wall_registration(record, reading, wall_id, ground)
        wall = next((w for w in reading["description"].get("walls", []) if w.get("id") == wall_id), {})
        geometry = next(w for w in record["walls"] if w["id"].split(":")[-1] == wall_id)
        lines.append(f"- wall {wall_id} (measured on {wall.get('measured_on', '?')}; the wall is "
                     f"{geometry['length_m']:.1f} m long, which on that crop's ruler is u {offset:.1f} to "
                     f"{offset + geometry['length_m'] / scale:.1f} m; eaves at v "
                     f"{geometry['eaves_h'] - geometry['base_h'] + shift:.1f} m):")
        for item in items:
            where = ""
            if "u_m" in item:
                where = f" at u {item['u_m'] / scale + offset:.1f} m"
                if "v_m" in item:
                    where += f", v {item['v_m'] + shift:.1f} m"
            lines.append(f"  - {item['what']}{where}: {item['why']}")
    return "\n".join(lines)


def roof_text(reading: dict) -> str:
    """A question about what stands on the roof of a read building: its dormers and chimneys again, wall by wall."""
    roof = reading["description"].get("roof", {})
    walls = {w.get("id"): w for w in reading["description"].get("walls", [])}
    under = sorted({part.get("anchor", {}).get("wall") for kind in ("dormers", "chimneys") for part in roof.get(kind, [])}
                   & set(walls)) or sorted(walls)
    lines = [QUESTION + " This is about the roof of a building you read before: the dormers and chimneys above the "
             "walls below. Look at the context views and at each wall's crops above its eaves line."]
    for wall in under:
        have = [f"{d.get('type')} at u {d['anchor']['u_m']:.1f} m" for d in roof.get("dormers", [])
                if d.get("anchor", {}).get("wall") == wall and "u_m" in d.get("anchor", {})]
        lines.append(f"- wall {wall} (measured on {walls[wall].get('measured_on', '?')}): your reading has "
                     + (", ".join(have) if have else "no dormers") + " above it.")
    lines += ["Count the dormers again and name each one's type by rule 13 of your instructions (its own little "
              "roof: a triangle is `gable`, one slope is `shed`; a face of the wall rising through the eaves is "
              "`wall_dormer`). For each give the anchor (`wall`, `u_m` of its centre on the ruler of the crop that "
              "wall was measured on, `setback_m` behind the wall face), `w_m`, `h_m` (its whole height over the "
              "roof at its front), `rise_m` for a gable's triangle, its wall material, `roof_material` and its "
              "`window` (an opening type). Leave out what the photographs do not show; a roof window lying in the "
              "slope is a skylight under `details`, not a dormer.",
              "Answer with `roof`: the whole roof entry again, corrected (it replaces the first one; keep its "
              "covering, eaves and verge unless the photographs show otherwise), with `walls` as an empty list, "
              "`opening_types` only for a dormer window type you add, and the report of the images you looked at."]
    return "\n".join(lines)


def blocked_text(blocked: dict, new: list) -> str:
    """A question about walls whose crops did not show them: read them from the new crops `new` instead."""
    lines = [QUESTION + " When you first read this building, no crop of the walls below showed them: something the "
             "city model does not hold (a quay wall, a fence, trees, vehicles) stood in the way. The wall crops "
             "listed above are new, from panoramas on other paths:"]
    for wall in sorted(blocked):
        names = [Path(c["file"]).name for c in new if c["wall"] == wall]
        lines.append(f"- wall {wall.split(':')[-1]} (new crops: {', '.join(names) or 'none'})")
    lines += ["Read each of these walls from its new crops as your instructions say. Answer with `walls`: the whole "
              "entry of each wall a new crop shows (its id; `measured_on` is the new crop it was measured on; leave "
              "a wall out when its new crops do not show it either), `opening_types` and `materials` only for names "
              "you add or change, `roof` only when the new views show its covering, dormers or chimneys differently "
              "from your first reading (then the whole roof entry), and the report of every image you looked at."]
    return "\n".join(lines)


def render_text(reading: dict, render: str, context: str) -> str:
    """The review in the game's own render: `render` is the building built from the reading, seen from the camera
    of the photograph `context`. Asks for what a flat wall drawing cannot show; wall colours are left alone,
    because the render's light is not the photograph's."""
    walls = reading["description"].get("walls", [])
    lines = [QUESTION + f" {render} in the packet is the game's own render of this building, built from your "
             f"description, from the camera of {context}: the same position, direction and field of view, in "
             "overcast light, without trees, vehicles or neighbours. Read both and compare them part by part:",
             "- the roof: covering and colour, eaves (overhang, fascia, colour), verge, dormers and chimneys (count, "
             "place, size, material), anything else standing on it;",
             "- the ground storey and the plinth: a material that differs from the wall above (a zone), shop "
             "floors, the plinth's height;",
             "- relief and depth: balconies and bay windows (type, depth, railing), cornices and bands, surrounds, "
             "pilasters, recessed entrances, canopies;",
             "- the windows as they read at this distance: proportions, glazing bars, frame colour.",
             "Do not correct wall colours from the render: its light differs from the photograph's. A part that is "
             "in your description and missing or wrong in the render is the generator's fault: leave the "
             "description as it is and say so in `notes`. Change the description only where it contradicts the "
             "photographs. The walls you read:"]
    lines += [f"- wall {w['id']} (measured on {w.get('measured_on', '?')})" for w in walls if w.get("id")]
    lines += ["Answer with what must change: `roof` (the whole roof entry, when anything on it changes), "
              "`materials` (only names you add or change), `opening_types` (only types you add or change) and "
              "`walls` (the whole entry of each wall you change, keeping its id and `measured_on`; an empty list "
              "when none changes), with the report of the images you looked at. In `notes` list each difference "
              "you saw, as `description:` (you corrected it) or `generator:` (described, but not drawn that way)."]
    return "\n".join(lines)


def review_text(pairs: dict) -> str:
    """The task of the review round: {wall id: (crop file, drawing file)} of the walls to compare."""
    lines = ["Review round. first-description.json in the packet is your first description of this building. For "
             "each wall below, the packet also holds a drawing of what the generator builds from it, on the same "
             "rulers as the crop the wall was measured on:"]
    lines += [f"- {wall}: {drawing} beside {crop}" for wall, (crop, drawing) in pairs.items()]
    lines += ["Read each pair and compare photograph and drawing place by place: the number and positions of bays "
              "and openings on every storey, opening widths and heights, sill and head lines, storey lines, the "
              "plinth and the ground storey's height, zones of another material, and the colours of wall, plinth "
              "and frames. The drawing is flat and schematic: it shows no relief, balconies, ornaments, surrounds "
              "or roof, and glass is one dark colour, so judge only what it draws. Return the complete description "
              "again in the same form: corrected wherever photograph and drawing differ by more than about 0.2 m, "
              "by a count, or by a visibly different colour, and unchanged elsewhere. Keep each wall's "
              "`measured_on`. List what you changed in `notes`."]
    return "\n".join(lines)


def assemble(record: dict, reading: dict, meta: dict, ground=None, tier: str = "observed") -> dict:
    """The stored description: the reading in its envelope, with frames, what was seen and where it came from.

    `ground(e, n)` gives the surveyed ground height; with it, heights are corrected by the ground line the reader
    saw at each wall's foot."""
    key = record["building"]
    body = _strip(reading["description"])
    report = {v["file"]: v for v in reading.get("views", [])}
    stamp = meta["at"]
    run_key = f"reading-{stamp}"
    provenance = [{"source": "panorama", "key": run_key, "at": stamp, "by": "w2-observe",
                   "note": f"{meta['model']} effort {meta['effort']}, {meta['images']} images, "
                           f"{meta['seconds']} s, ${meta['cost_usd'] or 0:.3f} notional"}]
    pano_key = {}
    for c in [*record["contexts"], *record["crops"]]:
        p = c["panorama"]
        k = f"{p['provider']}:{p['id']}"
        if k not in pano_key:
            pano_key[k] = True
            provenance.append({"source": "panorama", "key": k, "at": p["date"], "by": p["provider"]})
    by_id = {w["id"].split(":")[-1]: w for w in record["walls"]}
    walls, registered, moved = [], [], {}
    for wall in body.get("walls", []):
        geometry = by_id.get(wall.get("id"))
        if geometry is None:
            continue  # the reader named a wall the building does not have
        crops = [c for c in record["crops"] if c["wall"] == geometry["id"]]
        usable = [c for c in crops if report.get(Path(c["file"]).name, {}).get("usable", True)] or crops
        length = geometry["length_m"]
        offset, scale, shift = wall_registration(record, reading, wall["id"], ground)
        if (offset, scale) != (0.0, 1.0) or shift:
            registered.append(f"{wall['id']} {-offset:+.2f} m x{scale:.3f}" + (f" v{-shift:+.2f} m" if shift else ""))
            moved[wall["id"]] = (offset, scale)
        free = max((c["x1_m"] - c["x0_m"] for c in usable), default=0.0) / max(length, 1e-6)
        hidden_share = min((report.get(Path(c["file"]).name, {}).get("hidden_share", 0.0) for c in usable), default=0.0)
        best = min(usable, key=lambda c: report.get(Path(c["file"]).name, {}).get("hidden_share", 0.0), default=None)
        hidden = report.get(Path(best["file"]).name, {}).get("hidden_m", []) if best else []
        if best is not None:  # hidden rectangles follow the ruler of the crop they were seen in
            off, k = registration(length, best, report.get(Path(best["file"]).name, {}).get("facade_u_m"))
            hidden = [[round((r[0] - off) * k, 2), r[1], round((r[2] - off) * k, 2), r[3]] for r in hidden]
        wall = _register({k: v for k, v in wall.items() if k != "measured_on"}, offset, scale, shift)
        wall = {
            "id": wall["id"],
            "frame": {"p0": geometry["a"], "p1": geometry["b"], "base_h": geometry["base_h"],
                      "height_m": round(geometry["eaves_h"] - geometry["base_h"], 2)},
            "observed": {"visible_share": round(max(0.0, min(1.0, free)) * (1 - hidden_share), 2),
                         **({"hidden_m": hidden} if hidden else {})},
            **{k: v for k, v in wall.items() if k not in ("id", "frame", "observed")},
            "src": [run_key, *dict.fromkeys(f"{c['panorama']['provider']}:{c['panorama']['id']}" for c in crops)],
        }
        walls.append(wall)
    ids = {w["id"] for w in walls}
    for wall in walls:  # an inheritance from a wall that was dropped cannot stand
        if wall.get("like") not in ids:
            wall.pop("like", None)
    body["walls"] = walls
    if "roof" in body:  # chimneys and dormers are placed along a wall, on that wall's ruler
        body["roof"] = _anchors(body["roof"], moved)
    body.get("wall_defaults", {}).pop("measured_on", None)
    grid = body.get("wall_defaults", {}).get("bays")
    if isinstance(grid, dict):  # a default grid has to fit walls of any length: its pitch alone
        body["wall_defaults"]["bays"] = {k: v for k, v in grid.items() if k not in ("count", "first_m")}
        if "pitch_m" not in grid:
            del body["wall_defaults"]["bays"]
    if registered:
        provenance[0]["note"] += "; moved onto the wall: " + ", ".join(registered)
    doc = {"schema": "turku-description/building/1", "id": f"building/{key}", "tier": tier,
           "provenance": provenance, **body, "src": run_key}
    return doc
