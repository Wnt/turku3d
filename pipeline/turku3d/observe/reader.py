"""Read a building's crops into its description with the vision model.

One call per building: the model gets the context views and the rectified wall crops (prepare.py) and answers in
the building description format itself (docs/specs/building-description.md), restricted by a JSON schema derived
from the format's own schema. The call goes through the `claude` CLI as scripts/dev/scene-judge.mjs does: Read tool
only, no settings, no session. Time and notional cost of every call are recorded.
"""

from __future__ import annotations

import copy
import json
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
objects, and what was hidden or wrong.
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


def task_text(record: dict, packet: Path) -> str:
    reg = record["register"]
    lines = [f"The packet directory is {packet}. Read {packet / 'FORMAT.md'} first.", "",
             "Building (register priors): "
             + ", ".join(f"{k} {v}" for k, v in reg.items() if v is not None) + ".", "", "Walls of the city model:"]
    seen = {c["wall"] for c in record["crops"]}
    for w in record["walls"]:
        short = w["id"].split(":")[-1]
        state = "has crops" if w["id"] in seen else "no crop"
        extra = ""
        if w["adjoining"]:
            extra = "; a neighbour stands against it at u " + ", ".join(
                f"{j['x0']:.0f}-{j['x1']:.0f} m (up to v {j['eaves'] - w['base_h']:.0f} m)" for j in w["adjoining"])
        lines.append(f"- {short}: {w['length_m']:.1f} m long, faces {w['bearing_deg']:.0f} deg, eaves at v "
                     f"{w['eaves_h'] - w['base_h']:.1f} m, top at v {w['top_h'] - w['base_h']:.1f} m, {state}{extra}")
    lines += ["", "Images:"]
    for c in record["contexts"]:
        p = c["panorama"]
        lines.append(f"- {Path(c['file']).name}: context view, {p['provider']} {p['date']}, "
                     f"{c['distance_m']:.0f} m from the building, looking {c['pose']['yaw']:.0f} deg")
    for c in record["crops"]:
        p = c["panorama"]
        lines.append(f"- {Path(c['file']).name}: wall {c['wall'].split(':')[-1]} rectified, {p['provider']} "
                     f"{p['date']}, camera {c['distance_m']:.0f} m from the wall plane, {c['oblique_deg']:.0f} deg "
                     f"off frontal, {c['px_per_m']:.0f} px/m, line of sight free for u {c['x0_m']:.1f}-"
                     f"{c['x1_m']:.1f} m")
    lines += ["", "Answer with the description of this building and the per-image report."]
    return "\n".join(lines)


def read(record: dict, packet: Path, *, effort: str = EFFORT, model: str = MODEL) -> tuple[dict, dict]:
    """(reading, meta) for one prepared building. Raises on a failed call."""
    shutil.copyfile(SPEC, packet / "FORMAT.md")
    task = task_text(record, packet)
    (packet / "task.txt").write_text(task)
    args = ["claude", "-p", "--model", model, "--effort", effort, "--system-prompt", INSTRUCTIONS,
            "--tools", "Read", "--allowedTools", "Read", "--add-dir", str(packet), "--strict-mcp-config",
            "--disable-slash-commands", "--no-session-persistence", "--setting-sources", "",
            "--json-schema", json.dumps(reading_schema()), "--output-format", "json", task]
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
    usage = answer.get("usage") or {}
    meta = {
        "model": next(iter(answer.get("modelUsage") or {}), model), "effort": effort,
        "seconds": round(seconds, 1), "cost_usd": answer.get("total_cost_usd"), "turns": answer.get("num_turns"),
        "input_tokens": (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0),
        "output_tokens": usage.get("output_tokens"), "images": len(record["crops"]) + len(record["contexts"]),
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


def _register(node, offset: float, scale: float):
    """The wall's fields with every position along the wall moved from the crop's ruler onto the wall."""
    if isinstance(node, dict):
        return {k: (v if k == "conf"  # confidences are keyed by field name: not positions
                    else round((v - offset) * scale, 2) if k in U_FIELDS and isinstance(v, (int, float))
                    else round(v * scale, 3) if k == "pitch_m" and isinstance(v, (int, float))
                    else _register(v, offset, scale)) for k, v in node.items()}
    if isinstance(node, list):
        return [_register(v, offset, scale) for v in node]
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


def assemble(record: dict, reading: dict, meta: dict) -> dict:
    """The stored description: the reading in its envelope, with frames, what was seen and where it came from."""
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
        ruler = next((c for c in crops if Path(c["file"]).name == wall.get("measured_on")), None)
        offset, scale = registration(length, ruler, report.get(Path(ruler["file"]).name, {}).get("facade_u_m")) \
            if ruler else (0.0, 1.0)
        if (offset, scale) != (0.0, 1.0):
            registered.append(f"{wall['id']} {-offset:+.2f} m x{scale:.3f}")
            moved[wall["id"]] = (offset, scale)
        free = max((c["x1_m"] - c["x0_m"] for c in usable), default=0.0) / max(length, 1e-6)
        hidden_share = min((report.get(Path(c["file"]).name, {}).get("hidden_share", 0.0) for c in usable), default=0.0)
        best = min(usable, key=lambda c: report.get(Path(c["file"]).name, {}).get("hidden_share", 0.0), default=None)
        hidden = report.get(Path(best["file"]).name, {}).get("hidden_m", []) if best else []
        if best is not None:  # hidden rectangles follow the ruler of the crop they were seen in
            off, k = registration(length, best, report.get(Path(best["file"]).name, {}).get("facade_u_m"))
            hidden = [[round((r[0] - off) * k, 2), r[1], round((r[2] - off) * k, 2), r[3]] for r in hidden]
        wall = _register({k: v for k, v in wall.items() if k != "measured_on"}, offset, scale)
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
    doc = {"schema": "turku-description/building/1", "id": f"building/{key}", "tier": "observed",
           "provenance": provenance, **body, "src": run_key}
    return doc
