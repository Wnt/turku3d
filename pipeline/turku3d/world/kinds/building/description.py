"""Read and check building descriptions (docs/specs/building-description.md).

python -m turku3d.world.kinds.building.description FILE...   validate, and list what each wall leaves to the priors
"""

import json
import sys
from functools import cache
from pathlib import Path

SCHEMA_ID = "turku-description/building/1"
SCHEMA_PATH = Path(__file__).resolve().parents[5] / "docs/specs/building-description.schema.json"


@cache
def _validator():
    import jsonschema

    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def errors(doc: dict) -> list[str]:
    """Schema violations and broken references, as readable lines; empty when the description is valid."""
    out = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '.'}: {e.message}"
        for e in sorted(_validator().iter_errors(doc), key=lambda e: list(e.absolute_path))
    ]
    if out:
        return out
    types = doc.get("opening_types", {})
    materials = doc.get("materials", {})
    provenance = {entry["key"] for entry in doc.get("provenance", [])}
    walls = [w for w in doc.get("walls", [])]
    ids = [w.get("id") for w in walls if w.get("id")]
    if len(ids) != len(set(ids)):
        out.append("walls: duplicate wall id")

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}/{key}"
                if (
                    key in ("window", "door", "type")
                    and isinstance(value, str)
                    and _is_type_ref(path, key)
                ):
                    if value not in types:
                        out.append(f"{here}: opening type {value!r} is not defined")
                elif key == "row" and isinstance(value, list):
                    for i, cell in enumerate(value):
                        if cell is not None and cell not in types:
                            out.append(f"{here}/{i}: opening type {cell!r} is not defined")
                elif key in MATERIAL_FIELDS and isinstance(value, str):
                    if value not in materials:
                        out.append(f"{here}: material {value!r} is not defined")
                elif key == "src":
                    for name in [value] if isinstance(value, str) else value:
                        if name not in provenance:
                            out.append(f"{here}: provenance {name!r} is not defined")
                elif key == "like" and value not in ids:
                    out.append(f"{here}: wall {value!r} is not defined")
                elif key == "wall" and isinstance(value, str) and value not in ids:
                    out.append(f"{here}: wall {value!r} is not defined")
                walk(value, here)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}/{i}")

    walk({k: v for k, v in doc.items() if k not in ("materials", "provenance", "shell")}, "")
    for wall in [doc.get("wall_defaults", {}), *walls]:
        bays = wall.get("bays")
        count = (
            bays.get("count") if isinstance(bays, dict) else len(bays) if bays is not None else None
        )
        for i, storey in enumerate(wall.get("storeys", [])):
            row = storey.get("row")
            if row is not None and count is not None and len(row) != count:
                out.append(
                    f"wall {wall.get('id', 'defaults')} storey {i}: row has {len(row)} cells for {count} bays"
                )
        for i, opening in enumerate(wall.get("openings", [])):
            if "v_m" not in opening:
                out.append(
                    f"wall {wall.get('id', 'defaults')} openings/{i}: v_m is required outside a storey"
                )
    return out


MATERIAL_FIELDS = {"material", "roof_material", "stallriser", "lining", "coping"}


def _is_type_ref(path: str, key: str) -> bool:
    """`type` names an opening type only in a placed opening, and `door` is a position inside a shopfront."""
    parts = path.split("/")
    if key == "type":
        return len(parts) >= 2 and parts[-2] == "openings"
    return not (key == "door" and parts[-1] == "shopfront")


def load(path) -> dict:
    doc = json.loads(Path(path).read_text())
    problems = errors(doc)
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems[:8]))
    return doc


def expand_storeys(storeys: list[dict]) -> list[dict]:
    """The storey list with `count` unrolled: the indexes that rows, balconies and anchors refer to."""
    out = []
    for storey in storeys:
        one = {k: v for k, v in storey.items() if k != "count"}
        out.extend(dict(one) for _ in range(storey.get("count", 1)))
    return out


WALL_FIELDS = ("material", "plinth", "storeys", "bays")


def unobserved(doc: dict) -> list[str]:
    """What the description leaves to the priors, per wall."""
    out = []
    if not doc.get("walls"):
        out.append("no described wall: every facade uses wall_defaults and priors")
    for wall in doc.get("walls", []):
        missing = [f for f in WALL_FIELDS if f not in wall and "like" not in wall]
        if missing:
            out.append(f"wall {wall.get('id', '?')}: {', '.join(missing)} from priors")
    if "roof" not in doc:
        out.append("roof: material and eaves from priors")
    return out


def main(argv=None) -> int:
    failed = 0
    for name in argv if argv is not None else sys.argv[1:]:
        doc = json.loads(Path(name).read_text())
        problems = errors(doc)
        failed += bool(problems)
        print(f"{name}: {'INVALID' if problems else 'ok'}")
        for line in problems or unobserved(doc):
            print(f"  {line}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
