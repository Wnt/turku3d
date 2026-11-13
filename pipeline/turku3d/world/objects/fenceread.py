"""Fences read from panorama strips: family, height, paint and gates of a fence line, stored at tier `observed`.

    python -m turku3d.world.objects.fenceread E,N,RADIUS [--limit N] [--dry] [--facts STORE]

For each fence of the area that has no stored description, the strip of its line is cut from the nearest panorama
(fencecrop.py) and a vision model reads it against the strip's metre rulers. Only `fence/…` entities are written,
never over a stored description (so never over a reviewed one). A fence the strip shows no trace of is stored as
family `none`, which the fence kind builds nothing for. Each reading records its seconds and notional cost.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from ... import config
from . import fencecrop

MODEL = "claude-fable-5-2"
EFFORT = "low"
TIMEOUT_S = 5 * 60
BY = "w2-objects fence reader 1"
FAMILIES = ("board", "picket", "iron", "mesh", "stone-wall", "railing", "none")
INSTRUCTIONS = """\
You measure a garden or street fence from one picture. The picture is a street panorama projected on the vertical
plane through the fence's surveyed line: the bottom ruler is metres along the line, the left ruler metres above the
ground at the fence's foot; the red bars mark the surveyed line's extent and the height the fence is drawn with now.
The survey can be a metre off and years old, and plants often grow on or over a fence. Read the fence itself, not
the plants: its top edge against the left ruler, what it is made of, its paint. Answer with the JSON only."""
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["family", "height_m", "colour", "hedge", "confidence", "note"],
    "properties": {
        "family": {"enum": list(FAMILIES), "description": "board: closed boards; picket: spaced boards one sees "
                   "through; iron: metal bars; mesh: wire mesh or chain link; stone-wall: stone, brick or concrete "
                   "wall; railing: a low rail on posts; none: no fence, wall or railing stands on the line"},
        "height_m": {"type": "number", "description": "top of the fence above the ground, metres; 0 for none"},
        "colour": {"type": "string", "pattern": "^#[0-9a-f]{6}$", "description": "its paint or material, sRGB hex, "
                   "as in even daylight"},
        "hedge": {"type": "boolean", "description": "a hedge or dense bushes run along the line"},
        "gates": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["at_m", "width_m"],
                  "properties": {"at_m": {"type": "number"}, "width_m": {"type": "number"}}}},
        "confidence": {"enum": ["low", "medium", "high"]},
        "note": {"type": "string", "description": "one short sentence: what is seen"},
    },
}


def read(strip: dict, description: dict, *, model: str = MODEL, effort: str = EFFORT) -> tuple[dict, dict]:
    """(reading, meta) of one strip record of fencecrop.strip. Raises on a failed call."""
    picture = Path(strip["file"])
    task = (f"Read the picture {picture.name} (use the Read tool). The surveyed line is {strip['length_m']} m long in it and "
            f"the fence is drawn {description.get('height')} m high as {description.get('family')} now; the panorama "
            f"stands {strip['distance_m']} m from the line.")
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
        raise RuntimeError(f"fence reader: no JSON answer ({done.stderr[:300] or done.stdout[:300]})") from error
    if done.returncode or answer.get("is_error"):
        raise RuntimeError(f"fence reader: {answer.get('result') or done.stderr[:300]}")
    reading = answer.get("structured_output")
    if reading is None:
        reading = json.loads(str(answer.get("result", "")).strip().removeprefix("```json").removesuffix("```").strip())
    meta = {"model": next(iter(answer.get("modelUsage") or {}), model), "effort": effort,
            "seconds": round(time.time() - started, 1), "cost_usd": answer.get("total_cost_usd"),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return reading, meta


def observed(description: dict, reading: dict, strip: dict, meta: dict) -> dict | None:
    """The stored description a reading gives: the fields it replaces in the automatic one, at tier `observed`.
    None when the reading is not sure enough to replace a measurement."""
    if reading["confidence"] == "low":
        return None
    family = reading["family"]
    stored = {"schema": description["schema"], "id": description["id"], "tier": "observed",
              "provenance": [{"source": f"panorama:{strip['panorama']['provider']}", "key": strip["panorama"]["id"], "by": BY}],
              "family": family, "guessed": [name for name in description.get("guessed", []) if name not in ("family", "height", "colour")],
              "seen": {**reading, "panorama": strip["panorama"], "reader": meta}}
    if family != "none":
        stored["height"] = round(float(min(max(reading["height_m"], 0.3), 3.0)), 2)
        stored["colour"] = reading["colour"]
    return stored


def main() -> None:
    from ...observe.panoramas import Panoramas
    from ..api import Area, Stub
    from ..kinds import KINDS
    from ..sources import Sources
    from ..store import read_description, write_description

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("at", help="E,N,RADIUS in EPSG:3877")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--dry", action="store_true", help="read and print, store nothing")
    parser.add_argument("--facts", help="a world store whose fence facts are read instead of discovering them")
    args = parser.parse_args()
    kind, panos = KINDS["fence"], Panoramas()
    strips = config.work_root() / "objects" / "fence-strips"
    e, n, radius = map(float, args.at.split(","))
    if args.facts:
        # The facts a build left in a world store: the same fences without reading registers and the scan again
        # (a host that lacks the area's scan tiles can still read its fences).
        stubs = [Stub(row["id"], tuple(row["box"]), row["sources"], row["facts"])
                 for row in (json.loads(path.read_bytes()) for path in sorted(Path(args.facts, "facts", "fence").glob("*.json")))
                 if abs(row["facts"]["at"][0] - e) <= radius and abs(row["facts"]["at"][1] - n) <= radius]
    else:
        stubs = kind.discover(Area(e, n, radius), Sources())
    stubs = [stub for stub in stubs if stub.facts.get("line") and read_description(stub.id) is None]
    stubs.sort(key=lambda stub: -len(stub.facts["line"]))
    cost = 0.0
    for stub in stubs[: args.limit]:
        description = kind.describe(stub, None)
        strip = fencecrop.strip(description["line"], panos, strips / (stub.id.replace("/", "_") + ".jpg"), description["height"])
        if strip is None:
            print(f"{stub.id}: no panorama in reach")
            continue
        reading, meta = read(strip, description)
        cost += meta["cost_usd"] or 0.0
        stored = observed(description, reading, strip, meta)
        print(f"{stub.id}: {description['family']} {description['height']} m {description.get('colour')} -> "
              f"{reading['family']} {reading['height_m']} m {reading['colour']} ({reading['confidence']}; {reading['note']}) "
              f"{meta['seconds']} s, {meta['cost_usd']} USD" + ("" if stored else "; not stored"))
        if stored and not args.dry:
            write_description(stored, BY, reading["note"][:200])
    print(f"notional cost {cost:.2f} USD")


if __name__ == "__main__":
    main()
