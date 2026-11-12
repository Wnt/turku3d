"""describe: the effective description of a building, the automatic fit with the stored description laid over it.

The merge rule is the one in docs/specs/building-description.md: `shell` and `register` always come from the
sources; `materials` and `opening_types` merge by name; `wall_defaults` merges field by field; every other
top-level field of the stored description replaces the automatic one.
"""

from . import priors
from .description import SCHEMA_ID


def automatic(key: str, shell: dict, register: dict) -> dict:
    """What can be said of a building nobody has looked at: its shell, its register facts and a style."""
    doc = {
        "schema": SCHEMA_ID,
        "id": f"building/{key}",
        "tier": "auto",
        "provenance": [
            {"source": "turku-lod2", "key": shell.get("source", key), "by": "building.describe"}
        ],
        "register": register,
        "shell": shell,
    }
    doc["style"] = {"type": priors.style_type(doc), "conf": {"*": 0.2}}
    return doc


def effective(key: str, shell: dict, register: dict, stored: dict | None) -> dict:
    doc = automatic(key, shell, register)
    if not stored:
        return doc
    for field, value in stored.items():
        if field in ("shell", "register", "schema", "id"):
            continue
        if field in ("materials", "opening_types"):
            doc[field] = {**doc.get(field, {}), **value}
        elif field == "wall_defaults":
            doc[field] = priors.merged(doc.get(field, {}), value)
        elif field == "provenance":
            doc[field] = doc["provenance"] + value
        else:
            doc[field] = value
    return doc
