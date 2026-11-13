"""describe: the effective description of a building, the automatic fit with the stored description laid over it.

The merge rule is the one in docs/specs/building-description.md: `shell` and `register` always come from the
sources; `materials` and `opening_types` merge by name; `wall_defaults` merges field by field; every other
top-level field of the stored description replaces the automatic one.
"""

from . import archetypes, priors
from .description import SCHEMA_ID


def automatic(key: str, shell: dict, register: dict, measurements: dict | None = None) -> dict:
    """What can be said of a building nobody has looked at: its shell, its register facts, a style, and what was
    measured of it without anybody reading it (`measured`: wall and roof colours sampled from photographs)."""
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
    if measurements:
        doc["measured"] = measurements
    doc["style"] = {"type": priors.style_type(doc), "conf": {"*": 0.2}}
    doc["materials"] = {"wall": {**priors.register_wall(doc), "conf": {"*": 0.2}}}
    doc["wall_defaults"] = {"material": "wall"}
    doc["opening_types"] = priors.automatic_openings(doc)
    return measured(doc)


def measured(doc: dict) -> dict:
    """A roof nobody gave a material takes the roofing the orthophoto shows; a house nobody gave chimneys has
    the one its kind and age usually have."""
    roof = doc.get("roof", {})
    if "material" not in roof:
        roofing = priors.measured_roof(doc)
        if roofing:
            doc["roof"] = roof = {**roof, "material": roofing}
    if "chimneys" not in roof and doc.get("tier", "auto") == "auto":
        chimney = archetypes.chimney(doc, priors.style_type(doc))
        if chimney:
            doc["roof"] = {**roof, "chimneys": [chimney]}
    return doc


def effective(key: str, shell: dict, register: dict, stored: dict | None) -> dict:
    doc = automatic(key, shell, register, (stored or {}).get("measured"))
    if not stored:
        return doc
    doc.pop("roof", None)  # measured again below, on the style the stored description gives
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
    return measured(doc)
