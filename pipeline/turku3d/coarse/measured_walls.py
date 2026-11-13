"""Measured wall colours: one paint colour per building from the city-wide panorama colour run.

The run (``turku3d.observe colour`` on the W2 loop branch) segments each building's wall in its
street panoramas and divides the wall pixels by the visit's white point, so a value is a paint
estimate, not a lit pixel. Against 76 buildings whose walls a vision reading describes, values
with confidence >= 0.5 and lightness L* >= 45 are within CIE76 dE 10 of the read colour on 38
(median 9.9) and closer than the palette on 62, where the register's material palette is within
dE 10 on 4 (median 24.8). Below confidence 0.5, and darker than L* 45 (walls in shade, crops of
a neighbour: 8 buildings, median dE 42 against the palette's 35), the measurement is no better
than the palette, so those values are not exported.

The dataset is private derived data: it lives in the shared data root
(``derived/measured-walls/measured-walls.json``) and in the farm's source overlay, never in git.
``coarse.register`` reads it as a register fact; a description, a reading or a reviewed patch
still replaces the colour, and an OSM ``building:colour`` keeps priority over it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

from turku3d import config as C

SCHEMA = "turku3d-measured-walls-1"
DATASET = Path("derived/measured-walls/measured-walls.json")
MIN_CONFIDENCE = 0.5
MIN_LIGHTNESS = 45.0  # CIELAB L*
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def lightness(colour: str) -> float:
    """CIELAB L* of an sRGB hex colour (D65)."""
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        for c in (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    ]
    y = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    return 116 * (y ** (1 / 3) if y > 216 / 24389 else (24389 / 27 * y + 16) / 116) - 16


def dataset_path(root: Path | None = None) -> Path:
    return (root or C.data_root()) / DATASET


def source_paths(root: Path | None = None) -> list[Path]:
    """The dataset file when the data root (or the farm's source overlay) carries it."""
    path = dataset_path(root)
    return [path] if path.is_file() else []


def collect(lines) -> dict[str, dict]:
    """Confident wall colours per building from measurement lines; a later line replaces an earlier one."""
    walls = {}
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        wall = (row.get("measured") or {}).get("wall") or {}
        key, colour = str(row.get("building") or ""), str(wall.get("colour") or "")
        confidence = float((wall.get("conf") or {}).get("*", 0) or 0)
        if not key or not HEX.match(colour):
            continue
        if confidence < MIN_CONFIDENCE or lightness(colour) < MIN_LIGHTNESS:
            walls.pop(key, None)
            continue
        walls[key] = dict(colour=colour.lower(), conf=round(confidence, 3), n=int(wall.get("n") or 0))
    return dict(sorted(walls.items()))


def export(source: Path) -> dict:
    data = source.read_bytes()
    walls = collect(data.decode().splitlines())
    return dict(
        schema=SCHEMA,
        min_confidence=MIN_CONFIDENCE,
        min_lightness=MIN_LIGHTNESS,
        source=dict(lines=data.count(b"\n"), sha256=hashlib.sha256(data).hexdigest()),
        count=len(walls),
        walls=walls,
    )


def write(dataset: dict, path: Path) -> Path:
    if "raw" in Path(path).parts:
        raise ValueError("measured wall colours are derived data, never written under raw/")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    temp.write_text(json.dumps(dataset, separators=(",", ":"), sort_keys=True) + "\n")
    temp.replace(path)
    return path


def read(path: Path) -> dict[str, str]:
    """Building key -> measured wall colour (#rrggbb)."""
    dataset = json.loads(Path(path).read_text())
    if dataset.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not a {SCHEMA} dataset")
    return {
        key: wall["colour"]
        for key, wall in dataset["walls"].items()
        if wall.get("conf", 0) >= MIN_CONFIDENCE
        and HEX.match(wall.get("colour", ""))
        and lightness(wall["colour"]) >= MIN_LIGHTNESS
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="measurement lines (JSONL) of the colour run")
    parser.add_argument("--out", type=Path, help="default: $TURKU3D_DATA/derived/measured-walls/measured-walls.json")
    args = parser.parse_args(argv)
    dataset = export(args.source)
    path = write(dataset, args.out or dataset_path())
    print(json.dumps(dict(path=str(path), count=dataset["count"], source=dataset["source"])))


if __name__ == "__main__":
    main()
