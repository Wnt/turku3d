"""Machine-readable copy of docs/specs/materials.md (materials v1).

The spec file is the source of truth. ``materials_v1.json`` (next to this file) and
``game/src/shared/col/materials.ts`` are generated from it with

    python -m turku3d.col.materials --write

and a test (tests/test_col_materials.py) fails when either copy is stale.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from turku3d import config as C

MATERIAL_TABLE_VERSION = 1
SPEC_PATH = C.SPECS_DIR / "materials.md"
JSON_PATH = Path(__file__).with_name("materials_v1.json")
TS_PATH = C.REPO_ROOT / "game" / "src" / "shared" / "col" / "materials.ts"

CLASSES = ("solid", "query", "sensor")
FLAG_NAMES = ("CURB", "STAIR", "NOWALK", "FIX_EDGES")
FLAG_BITS = {n: 1 << k for k, n in enumerate(FLAG_NAMES)}

# Rapier interaction-group bits suggested in materials.md (16-bit membership / filter).
GROUPS = {
    "STATIC_SOLID": 1 << 0,
    "STATIC_QUERY": 1 << 1,
    "STATIC_SENSOR": 1 << 2,
    "CHARACTER": 1 << 4,
    "VEHICLE": 1 << 5,
    "DYNAMIC": 1 << 6,
}


@dataclass(frozen=True)
class Material:
    id: int
    name: str
    cls: str
    friction: float
    audio: str
    flags: tuple[str, ...]

    @property
    def flag_bits(self) -> int:
        return sum(FLAG_BITS[f] for f in self.flags)


_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`([a-z0-9_]+)`\s*\|\s*(\w+)\s*\|\s*([0-9.]+)\s*\|\s*(\w+)\s*\|\s*([A-Z_, ]*)\|",
    re.M,
)


def parse_spec(text: str | None = None) -> list[Material]:
    text = SPEC_PATH.read_text() if text is None else text
    out = []
    for i, name, cls, fric, audio, flags in _ROW.findall(text):
        fl = tuple(f.strip() for f in flags.split(",") if f.strip())
        for f in fl:
            if f not in FLAG_BITS:
                raise ValueError(f"materials.md: unknown flag {f!r} on {name}")
        if cls not in CLASSES:
            raise ValueError(f"materials.md: bad class {cls!r} on {name}")
        out.append(Material(int(i), name, cls, float(fric), audio, fl))
    ids = [m.id for m in out]
    if ids != list(range(len(ids))):
        raise ValueError("materials.md: IDs must be contiguous from 0")
    return out


@lru_cache(maxsize=1)
def table() -> tuple[Material, ...]:
    """Materials v1 from the generated JSON (falls back to parsing the spec)."""
    if JSON_PATH.exists():
        d = json.loads(JSON_PATH.read_text())
        return tuple(Material(m["id"], m["name"], m["cls"], m["friction"], m["audio"], tuple(m["flags"]))
                     for m in d["materials"])
    return tuple(parse_spec())


def by_name() -> dict[str, Material]:
    return {m.name: m for m in table()}


def mat_id(name: str) -> int:
    return by_name()[name].id


def is_shippable(mid: int) -> bool:
    return 0 < mid < len(table())


def material_class(mid: int) -> str:
    """Collision class; unknown IDs behave as solid (materials.md rules)."""
    t = table()
    return t[mid].cls if 0 <= mid < len(t) else "solid"


def has_flag(mid: int, flag: str) -> bool:
    t = table()
    return 0 <= mid < len(t) and flag in t[mid].flags


def render_json(mats: list[Material]) -> str:
    d = {
        "generated_from": "docs/specs/materials.md",
        "material_table_version": MATERIAL_TABLE_VERSION,
        "flag_bits": FLAG_BITS,
        "groups": GROUPS,
        "materials": [{**asdict(m), "flags": list(m.flags)} for m in mats],
    }
    return json.dumps(d, indent=1) + "\n"


def render_ts(mats: list[Material]) -> str:
    lines = [
        "// GENERATED from docs/specs/materials.md by `python -m turku3d.col.materials --write`.",
        "// Do not edit by hand; tests fail when this file is stale.",
        "",
        f"export const MATERIAL_TABLE_VERSION = {MATERIAL_TABLE_VERSION};",
        "",
        "export type CollisionClass = 'solid' | 'query' | 'sensor';",
        "",
        "export const MAT_FLAG = {",
        *[f"  {n}: {b}," for n, b in FLAG_BITS.items()],
        "} as const;",
        "",
        "/** Rapier interaction-group bits (16-bit membership / filter), materials.md. */",
        "export const GROUP = {",
        *[f"  {n}: {b}," for n, b in GROUPS.items()],
        "} as const;",
        "",
        "export interface MaterialInfo {",
        "  id: number;",
        "  name: string;",
        "  cls: CollisionClass;",
        "  friction: number;",
        "  audio: string;",
        "  /** Bit set of MAT_FLAG. */",
        "  flags: number;",
        "}",
        "",
        "export const MATERIALS: readonly MaterialInfo[] = [",
    ]
    for m in mats:
        fl = " | ".join(f"MAT_FLAG.{f}" for f in m.flags) or "0"
        lines.append(f"  {{ id: {m.id}, name: '{m.name}', cls: '{m.cls}', friction: {m.friction}, "
                     f"audio: '{m.audio}', flags: {fl} }},")
    lines += [
        "];",
        "",
        "export const MAT = {",
        *[f"  {m.name}: {m.id}," for m in mats],
        "} as const;",
        "",
        "/** Collision class of a material id; unknown ids behave as solid (materials.md). */",
        "export function materialClass(id: number): CollisionClass {",
        "  return MATERIALS[id]?.cls ?? 'solid';",
        "}",
        "",
        "export function materialHasFlag(id: number, flag: number): boolean {",
        "  return ((MATERIALS[id]?.flags ?? 0) & flag) !== 0;",
        "}",
        "",
        "/** 0 (unknown), unassigned ids and 200-255 must not appear in a shipped tile. */",
        "export function isShippableMaterial(id: number): boolean {",
        "  return id > 0 && id < MATERIALS.length;",
        "}",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    mats = parse_spec()
    if "--write" in argv:
        JSON_PATH.write_text(render_json(mats))
        TS_PATH.parent.mkdir(parents=True, exist_ok=True)
        TS_PATH.write_text(render_ts(mats))
        print(f"wrote {JSON_PATH} and {TS_PATH} ({len(mats)} materials)")
        return 0
    stale = []
    if not JSON_PATH.exists() or JSON_PATH.read_text() != render_json(mats):
        stale.append(str(JSON_PATH))
    if not TS_PATH.exists() or TS_PATH.read_text() != render_ts(mats):
        stale.append(str(TS_PATH))
    if stale:
        print("stale (run with --write):", *stale, sep="\n  ")
        return 1
    print("materials copies are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
