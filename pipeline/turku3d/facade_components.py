"""Bounded reading-to-component adapter. Named extents/depths are explicit layout priors.

Never infer complete counts from lower bounds, hidden fragments, glazing panes or
an unconfirmed association. Source readings and their existing paint stay intact.
"""

import re

import numpy as np

KINDS = (
    ("loggia", r"loggia|recessed balcon"),
    ("glazed_balcony", r"glazed balcon|balcon.*glaz"),
    ("balcony", r"balcon"),
    ("veranda", r"veranda"),
    ("stairs", r"external.*stair|exterior.*stair|outside.*stair"),
    ("arcade", r"arcade|colonnade|behind.*columns|recessed.*columns"),
    ("passage", r"through.*passage|vehicle passage|vehicular passage|open.*passage"),
    ("bay", r"risalit|projecting bay|projecting.*volume|projecting.*wing"),
    ("recess", r"recessed (?:entry|entrance|frontage|glazed frontage)"),
    ("artwork", r"clock|mural|signboard|raised lettering"),
)
ROW_KEYS = (
    "rows",
    "row_count",
    "rows_per_stack",
    "floor_count",
    "whole_wall_rows",
    "whole_wall_row_count",
    "whole_wall_floor_count",
    "balcony_levels",
    "complete_balcony_levels_visible",
    "whole_level_count",
    "rows_visible",
    "visible_rows",
    "complete_glazing_rows_visible",
    "balcony_levels_visible",
)
COUNT_KEYS = (
    "stacks",
    "balcony_stacks",
    "whole_stack_count",
    "column_count",
    "count",
    "bay_count",
    "whole_bay_count",
    "whole_wall_bay_count",
    "bays",
    "bays_per_row",
)
DEPTH = dict(
    bay=0.45,
    recess=0.65,
    balcony=1.1,
    glazed_balcony=1.1,
    loggia=1.1,
    veranda=1.4,
    stairs=1.2,
    arcade=1.8,
    passage=0,
    artwork=0.04,
)


def integer(section, keys, default=None):
    for key in keys:
        value = section.get(key)
        if type(value) is int and 0 < value <= 32:
            return value
    return default


def kind_of(text):
    return next((k for k, pattern in KINDS if re.search(pattern, text.lower())), None)


NUMBERS = {
    word: i
    for i, word in enumerate(
        (
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
        )
    )
}
NUMBER = r"(?:[1-9]|1[0-2]|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"


def counted(text, noun):
    match = re.search(
        rf"\b({NUMBER}) (?:complete |visible |adjacent |vertical |projecting )*(?:{noun})\b", text
    )
    if match:
        value = match[1]
        return int(value) if value.isdigit() else NUMBERS[value]
    return None


def components(form, floors):
    """Return descriptors plus abstentions, without modifying the photographic form."""
    from turkutex.facade_sections import bounds

    reason = str(form.get("render_deferred", ""))
    if re.search(r"historical.*(absent|unconfirmed)|association remains uncertain", reason, re.I):
        return [], ["unconfirmed current wall association"]
    result, pending = [], []
    source = form.get("facade_observations", {}).get("sections", [])
    expanded = []
    for section in source:
        bays = section.get("bays")
        extent = section.get("bounds") or bounds(section.get("position"), floors)
        if isinstance(bays, list) and extent:
            for i, bay in enumerate(bays):
                if isinstance(bay, str) and kind_of(bay):
                    x, y, w, h = extent
                    expanded.append(
                        {
                            **section,
                            "kind": bay,
                            "bays": 1,
                            "bounds": [x + w * i / len(bays), y, w / len(bays), h],
                        }
                    )
        else:
            expanded.append(section)
    for index, section in enumerate(expanded):
        text = str(section.get("kind", "")).lower()
        features = " ".join(map(str, section.get("features", []))).lower()
        feature_kind = kind_of(features)
        kind = kind_of(text) or feature_kind
        if kind is None:
            continue
        position = str(section.get("position", ""))
        extent = section.get("bounds") or bounds(position, floors)
        if (
            section.get("belongs_to_facade") is False
            or extent is None
            or re.search(r"clipped|fragment|hidden|occluded|alternate|lower.bound|\bor\b", text)
            or "at least" in text
            or any("lower_bound" in key for key in section)
        ):
            pending.append(f"section {index}: {kind}: unresolved extent/count/plane")
            continue
        if (
            len(extent) != 4
            or not np.isfinite(extent).all()
            or min(extent) < 0
            or min(extent[2:]) <= 0
            or extent[0] + extent[2] > 1.001
            or extent[1] + extent[3] > 1.001
        ):
            pending.append(f"section {index}: invalid bounds")
            continue
        if kind == "artwork" and "bounds" not in section:
            pending.append(f"section {index}: artwork needs a located rectangle")
            continue
        rows = integer(section, ROW_KEYS)
        count = integer(section, COUNT_KEYS)
        combined = text + " " + features
        if kind in {"balcony", "glazed_balcony", "loggia"}:
            # Text counts are admitted only with an explicit component noun; no
            # window count, pane count or "at least" phrase becomes a balcony count.
            if "at least" not in combined:
                rows = rows or counted(
                    combined, r"(?:balcony )?levels|rows of (?:glazed )?balconies"
                )
                count = count or counted(
                    combined, r"(?:balcony )?stacks|stacks of .*balconies|balcony bays"
                )
            if not kind_of(text) and "stack" in features:
                if "left" in features and "right" not in features.split("stack")[0]:
                    extent = [extent[0], extent[1], extent[2] * 0.33, extent[3]]
                elif "central" in features or "centre" in features:
                    extent = [extent[0] + extent[2] * 0.33, extent[1], extent[2] * 0.34, extent[3]]
        if kind in {"balcony", "glazed_balcony", "loggia"}:
            if rows is None:
                if re.search(r"\bone\b|\bsingle\b", text) or (
                    section.get("count") == 1 and "stack" not in text
                ):
                    rows = 1
                else:
                    pending.append(f"section {index}: {kind}: missing complete level count")
                    continue
            # A located vertical strip is one stack; a whole-wall band is not.
            if count is None and (
                extent[2] < 0.5
                or re.search(
                    r"\bone\b|\bsingle\b|continuous|spanning frontage|horizontal.*bands", text
                )
            ):
                count = 1
            if count is None:
                pending.append(f"section {index}: {kind}: missing complete stack count")
                continue
        if kind == "stairs" and (not section.get("rise_m") or not section.get("run_m")):
            pending.append(f"section {index}: stairs need rise and run")
            continue
        depth = section.get("depth_m", DEPTH[kind])
        if not isinstance(depth, (float, int)) or not np.isfinite(depth) or not 0 <= depth <= 4:
            pending.append(f"section {index}: invalid depth")
            continue
        result.append(
            dict(
                section=index,
                kind=kind,
                bounds=list(extent),
                rows=rows or 1,
                count=count or 1,
                depth_m=max(0.04, depth),
                dimensions_source="reading"
                if "bounds" in section and "depth_m" in section
                else "named-section and conservative dimension priors",
                head=section.get("head", "round" if "arch" in text else "flat"),
                rise_m=section.get("rise_m"),
                run_m=section.get("run_m"),
                direction=-1 if "left" in features else 1,
            )
        )
    return result, pending
