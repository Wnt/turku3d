"""Conservative adapter for sectioned readings; qualitative extents are layout priors.

No pixel coordinates, lower-bound counts, panes, or storeys are promoted to complete
opening counts. Unknown geometry stays on the existing treatment. Source forms are
never mutated. Unsupported text is returned for the coverage audit.
"""

import re

MATERIAL_WORDS = {
    "render": ("render", "plaster", "stucco"),
    "brick": ("brick",),
    "wood": ("wood", "timber", "wooden"),
    "concrete_panel": ("concrete",),
    "stone": ("stone", "granite", "fieldstone"),
    "metal": ("metal", "corrugated", "steel", "aluminium"),
    "glass": ("glass", "glazed", "glazing"),
}


def material(text):
    text = str(text or "").lower()
    if re.search(r"\b(or|uncertain|unknown)\b", text):
        return None
    found = [
        k for k, words in MATERIAL_WORDS.items() if any(re.search(rf"\b{w}\b", text) for w in words)
    ]
    return found[0] if len(found) == 1 else None


def bounds(position, floors):
    """Resolve named sections, not an inferred complete wall from a visible fragment."""
    p = str(position or "").lower()
    if re.search(r"roof|foreground|behind|alternate|fragment|clipped|stairs|return", p):
        return None
    x, y, w, h = 0.0, 0.0, 1.0, 1.0
    if "left" in p:
        w = 0.33
    elif "right" in p:
        x, w = 0.67, 0.33
    elif "centre" in p or "center" in p:
        x, w = 0.33, 0.34
    if "edge" in p or "strip" in p and not any(k in p for k in ("upper", "lower", "top", "base")):
        w = min(w, 0.12)
        if "right" in p:
            x = 1 - w
    ground = 1 / max(1, floors or 3)
    if any(k in p for k in ("base", "ground", "lower", "basement")):
        h = ground
    elif "top" in p or "attic" in p:
        y, h = 1 - ground, ground
    elif "upper" in p:
        y, h = ground, 1 - ground
    elif "middle" in p:
        y, h = ground, max(ground, 1 - 2 * ground)
    elif not any(k in p for k in ("wall", "whole", "main", "left", "right", "centre", "center")):
        return None
    return [x, y, w, h]


def integer(section, keys):
    for key in keys:
        value = section.get(key)
        if type(value) is int and 0 < value <= 60:
            return value
    return None


def sections(form, floors):
    result, unsupported = [], []
    observation = form.get("facade_observations", {})
    # Explicit association and current-existence failures cannot authorize painting.
    if re.search(
        r"historical.*(absent|unconfirmed)|association remains uncertain",
        form.get("render_deferred", ""),
        re.I,
    ):
        return [], ["unconfirmed current wall association"]
    for index, section in enumerate(observation.get("sections", [])):
        extent = bounds(section.get("position"), floors)
        if not extent or section.get("belongs_to_facade") is False:
            unsupported.append(f"section {index}: unresolved extent or separate plane")
            continue
        text = " ".join(
            [str(section.get("kind", "")), *map(str, section.get("features", []))]
        ).lower()
        mat = material(section.get("material"))
        if mat is None and "material" not in section:
            kind_text = str(section.get("kind", "")).lower()
            if re.search(
                r"wall|cladding|siding|brickwork|render|timber", kind_text
            ) and not re.search(r"rail|frame|surround|soffit|balcon", kind_text):
                mat = material(kind_text)
        kind = None
        if re.search(r"\b(blank|opaque|blind)\b", text):
            kind = "blank"
        elif re.search(r"shutter|garage door|service door|boarded", text):
            kind = "opaque door"
        elif re.search(r"portal|arched.*entrance", text):
            kind = "arched portal"
        elif re.search(r"\b(shop|retail|storefront)\b", text):
            kind = "shop"
        elif re.search(r"\b(window|windows|glazing|glazed)\b", text):
            kind = "window"
        elif re.search(r"\b(door|entrance)\b", text):
            kind = "door"
        # Separate structures are never collapsed onto the wall or made into windows.
        if re.search(r"balcon|loggia|passage|colonnade|stair|veranda|projecting|recessed", text):
            kind = None
            unsupported.append(f"section {index}: depth/structure retained")
        count = integer(
            section,
            (
                "whole_wall_bay_count",
                "whole_bay_count",
                "full_bay_count",
                "bay_count",
                "bays_per_row",
                "windows_per_row",
                "count_per_row",
                "window_count",
                "opening_count",
                "count",
            ),
        )
        rows = integer(section, ("rows", "row_count", "floor_count", "whole_wall_row_count"))
        if kind == "blank":
            count, rows = 1, 1
        elif rows is None and extent[3] <= 1 / max(1, floors or 3) + 0.01:
            rows = 1
        # Uncounted descriptions can supply a material, never an invented grid.
        if count is None or rows is None:
            kind = None
        head = section.get("head", "flat")
        if head not in ("flat", "segmental", "round", "pointed"):
            head = "flat"
        surround = section.get("surround", "none")
        if surround not in ("none", "frame", "pediment", "rustication"):
            surround = "none"
        if mat or kind:
            result.append(
                dict(
                    index=index,
                    bounds=extent,
                    material=mat,
                    kind=kind,
                    count=count,
                    rows=rows,
                    head=head,
                    surround=surround,
                )
            )
        if not kind:
            unsupported.append(f"section {index}: incomplete opening layout")
        if section.get("features"):
            unsupported.append(f"section {index}: free-text details retained, not fully rendered")
    return result, unsupported
