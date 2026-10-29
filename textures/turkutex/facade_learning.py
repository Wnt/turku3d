"""Building-balanced empirical facade distributions with a local era-weighted prior.

Training and validation use independent building IDs. Only explicit observations
are targets; layout-prior dimensions are labelled as such by the caller. Inferred
facades never become donors. The runtime has no training or network dependency.
"""

import math
import random
import re
from collections import defaultdict

from .facade_program import stable_seed
from .facade_sections import material

NUMERIC = {
    "bay_m",
    "window_width",
    "window_height",
    "windows_per_bay",
    "shop_share",
    "course_share",
    "ground_scale",
    "top_scale",
}
SCALES = {
    "construction_year": 35,
    "levels": 3,
    "log_area": 1.5,
    "compactness": 0.3,
    "frontage_m": 25,
}
CATEGORIES = ("use", "material_class", "district", "street_class", "corner")


def targets(wall):
    """No lower-bound counts, hidden bays or prose guesses enter complete-count labels."""
    form = wall.get("structure", {})
    if form.get("render_deferred"):
        sections = form.get("facade_observations", {}).get("sections", [])
        mats = [m for s in sections if (m := material(s.get("material")))]
        result = {"material": max(sorted(set(mats)), key=mats.count)} if mats else {}
        for key, allowed in (
            ("head", {"flat", "segmental", "round", "pointed"}),
            ("surround", {"none", "frame", "pediment", "rustication"}),
        ):
            values = [s[key] for s in sections if isinstance(s.get(key), str) and s[key] in allowed]
            if values:
                result[key] = max(sorted(set(values)), key=values.count)
        # Positive section evidence remains useful even when placement/count is unknown.
        # Absence of a mention is never a negative balcony/shop observation.
        descriptions = [str(s.get("kind", "")) for s in sections]
        if any(
            re.search(r"\b(balcon(?:y|ies)|loggias?)\b", text, re.I)
            and not re.search(r"no |without |uncertain", text, re.I)
            for text in descriptions
        ):
            result["balcony"] = True
        for section in sections:
            text = str(section.get("kind", "")).lower()
            if "ribbon" in text and ("window" in text or "glazing" in text):
                result["rhythm"] = "ribbon"
            if str(section.get("position", "")).lower() in ("base", "ground"):
                if re.search(r"shop|retail|storefront", text):
                    result["ground"] = "shops"
                elif re.search(r"door|entrance", text) and not re.search(
                    r"uncertain|unknown", text
                ):
                    result["ground"] = "doors"
            for field, allowed in (
                ("ornament", {"none", "low", "medium", "high"}),
                ("cornice", {"none", "thin", "projecting", "ornate"}),
            ):
                if isinstance(section.get(field), str) and section[field] in allowed:
                    result[field] = section[field]
        return result
    if not form.get("floors"):
        return {}
    rows = form["floors"]
    upper = [r for r in rows if r["role"] != "base"] or rows
    complete = [r for r in upper if r["bay_count"] and "unknown" not in r["bays"]]
    result = {k: form[k] for k in ("material", "ornament", "cornice") if form.get(k) is not None}
    result["balcony"] = bool(form.get("balconies"))
    if len(rows) > 1:
        heights = [
            {"short": 0.55, "regular": 1, "tall": 1.3}[r.get("height_class", "regular")]
            for r in rows
        ]
        result["ground_scale"] = heights[0] * len(rows) / sum(heights)
        result["top_scale"] = heights[-1] * len(rows) / sum(heights)
        result["course_share"] = len(form["string_courses"]) / (len(rows) - 1)
    if complete:
        count = sum(r["bay_count"] for r in complete) / len(complete)
        result["bay_m"] = wall["width_m"] / count
        multiplicities = [
            v for r in complete for v in r.get("opening_counts", [1] * r["bay_count"])
        ]
        result["windows_per_bay"] = sum(multiplicities) / len(multiplicities)
        result["rhythm"] = (
            "ribbon"
            if any(r.get("rhythm") == "ribbon" for r in complete)
            else "paired"
            if result["windows_per_bay"] > 1.3
            else "regular"
        )
        for key in ("head", "surround"):
            values = [
                v
                for r in complete
                for v in (r[key] if isinstance(r[key], list) else [r[key]])
                if v is not None
            ]
            if values:
                result[key] = max(sorted(set(values)), key=values.count)
        sizes = [
            c
            for r in complete
            for c in (
                r.get("opening_class", "regular")
                if isinstance(r.get("opening_class"), list)
                else [r.get("opening_class", "regular")]
            )
        ]
        result["window_width"] = sum(
            {"small": 0.2, "regular": 0.48, "wide": 0.76, "tall": 0.48}[c] for c in sizes
        ) / len(sizes)
        result["window_height"] = sum(
            {"small": 0.28, "regular": 0.55, "wide": 0.55, "tall": 0.8}[c] for c in sizes
        ) / len(sizes)
    ground = [
        r for r in rows if r["role"] == "base" and r["bay_count"] and "unknown" not in r["bays"]
    ]
    if ground:
        bays = [b for r in ground for b in r["bays"]]
        result["shop_share"] = bays.count("shop") / len(bays)
        result["ground"] = (
            "shops"
            if "shop" in bays
            else "doors"
            if any(k in bays for k in ("door", "arched portal"))
            else "plain"
            if set(bays) == {"blank"}
            else "windows"
        )
    return result


def fit(buildings):
    donors = []
    for building in sorted(buildings, key=lambda b: b["building_id"]):
        fields = defaultdict(list)
        for wall in building["walls"]:
            for key, val in targets(wall).items():
                fields[key].append(val)
        # A building has one vote, regardless of how many walls/alternates were photographed.
        distributions = {
            k: ([sum(v) / len(v)] if k in NUMERIC else sorted(set(v), key=str))
            for k, v in fields.items()
        }
        if distributions:
            donors.append(
                dict(
                    id=str(building["building_id"]),
                    attributes=building["attributes"],
                    fields=distributions,
                )
            )
    return dict(
        version=1,
        method="building-balanced conditional empirical distribution plus distance/era prior",
        donors=donors,
        observed_ids=sorted(str(b["building_id"]) for b in buildings),
    )


def distance(a, b):
    terms = [
        min(3, abs(float(a[k]) - float(b[k])) / scale)
        for k, scale in SCALES.items()
        if a.get(k) is not None and b.get(k) is not None
    ]
    terms += [
        float(str(a[k]) != str(b[k]))
        for k in CATEGORIES
        if a.get(k) not in (None, "unknown") and b.get(k) not in (None, "unknown")
    ]
    return sum(terms) / len(terms) if terms else 1.0


def weights(building_id, attrs, model, local=True):
    ranked = sorted(
        (
            (distance(attrs, d["attributes"]), d)
            for d in model["donors"]
            if d["id"] != str(building_id)
        ),
        key=lambda p: (p[0], p[1]["id"]),
    )
    selected = {d["id"]: [d, math.exp(-3 * dist)] for dist, d in ranked[:32]}
    if local and attrs.get("E") is not None and attrs.get("N") is not None:
        for _, donor in ranked:
            a = donor["attributes"]
            if a.get("E") is None or a.get("N") is None:
                continue
            metres = math.hypot(attrs["E"] - a["E"], attrs["N"] - a["N"])
            if metres > 250:
                continue
            year, other = attrs.get("construction_year"), a.get("construction_year")
            era = math.exp(-abs(year - other) / 30) if year and other else 0.25
            same = any(attrs.get(k) and attrs[k] == a.get(k) for k in ("street", "block"))
            weight = 6 * era * (1 if same else 0.5) / (1 + metres / 35) ** 2
            if donor["id"] not in selected:
                selected[donor["id"]] = [donor, 0]
            selected[donor["id"]][1] += weight
    return [selected[k] for k in sorted(selected)]


def predict(building_id, attrs, model, *, sampled=False, local=True):
    distributions = defaultdict(list)
    for donor, weight in weights(building_id, attrs, model, local):
        for key, vals in donor["fields"].items():
            distributions[key].extend((val, weight / len(vals)) for val in vals)
    result = {}
    for key, values in distributions.items():
        total = sum(w for _, w in values)
        if sampled:
            rng = random.Random(stable_seed(building_id, "learned:" + key))
            draw = rng.choices([v for v, _ in values], [w for _, w in values])[0]
            mean = sum(v * w for v, w in values) / total if key in NUMERIC else None
            result[key] = (
                0.75 * mean + 0.25 * draw if key in NUMERIC and key != "windows_per_bay" else draw
            )
        elif key in NUMERIC:
            result[key] = sum(v * w for v, w in values) / total
        else:
            votes = defaultdict(float)
            for val, weight in values:
                votes[val] += weight
            result[key] = max(sorted(votes, key=str), key=votes.get)
    return result


def baseline(building_id, attrs):
    from .facade_archetypes import recipe

    r = recipe(building_id, attrs)
    historic = r["archetype"] in (
        "neo_renaissance",
        "jugend",
        "civic",
        "empire",
        "nordic_classicism",
    )
    head = random.Random(stable_seed(building_id, "head_shape")).choice(
        ("flat", "segmental", "round") if historic else ("flat",)
    )
    return dict(
        bay_m=r["bay"],
        window_width=r["window"],
        window_height=r["height"],
        windows_per_bay=1,
        rhythm=r["rhythm"],
        head=head,
        surround="frame" if r["surrounds"] > 0.06 else "none",
        ground=r["ground"],
        shop_share=float(r["ground"] == "shops"),
        material={"plaster": "render", "concrete": "concrete_panel"}.get(
            r["material"], r["material"]
        ),
        ornament="medium" if r["surrounds"] > 0.1 else "low" if r["surrounds"] > 0.04 else "none",
        cornice="ornate"
        if r["cornice"] > 0.37
        else "projecting"
        if r["cornice"] > 0.2
        else "thin"
        if r["cornice"] > 0.04
        else "none",
        course_share=float(r["courses"] > 0.05),
        ground_scale=1.25 if r["ground"] == "shops" else 1.0,
        top_scale=1.0,
        balcony=r["balcony"],
    )


def apply(program, model):
    if str(program["building_id"]) in model.get("observed_ids", []) or str(
        program["attributes"].get("register_id")
    ) in model.get("observed_ids", []):
        return program
    from .facade_program import value

    attrs = program["attributes"]
    seed = attrs.get("register_id") or attrs.get("osm_id") or program["building_id"]
    p = predict(seed, attrs, model, sampled=True)
    if not p:
        return program
    program["learned_prediction"] = p
    v = lambda x: value(x, "learned reading distribution; sampled layout prior")  # noqa: E731
    floors = program["storeys"]["value"]
    for wall in program["walls"]:
        width, height = wall["width_m"], wall["height_m"]
        count = (
            max(1, round(width / max(1, p.get("bay_m", 3))))
            if width >= 1.6 and not attrs.get("blank")
            else 0
        )
        bay = width / max(1, count)
        wall["bays"].update(
            count=count,
            pitch_m=bay,
            centers_m=[(i + 0.5) * bay for i in range(count)],
            rhythm=p.get("rhythm", "regular"),
        )
        ground = attrs.get("ground_floor") or p.get("ground", "windows")
        wall["base"].update(
            variant=v(ground), shopfront=v(ground == "shops"), glazing_share=v(p.get("shop_share"))
        )
        wall["wall"]["material"] = v(p.get("material", "render"))
        fh = height / floors if floors else height
        wall["windows"].update(
            size_m=v([bay * p.get("window_width", 0.48), fh * p.get("window_height", 0.55)]),
            head_shape=v(
                {
                    "segmental": "segmental-arch",
                    "round": "round-arch",
                    "pointed": "pointed-arch",
                }.get(p.get("head"), "flat")
            ),
        )
        wall["trim"].update(
            cornice=v(
                {"none": 0, "thin": 0.12, "projecting": 0.3, "ornate": 0.45}.get(
                    p.get("cornice"), 0
                )
            ),
            string_courses=v(0.12 if p.get("course_share", 0) > 0.5 else 0),
            window_surrounds=v(0 if p.get("surround") == "none" else 0.12),
            ornament_level=v(p.get("ornament")),
        )
        wall["learned_layout"] = {
            k: p[k]
            for k in (
                "windows_per_bay",
                "surround",
                "balcony",
                "ground_scale",
                "top_scale",
                "course_share",
            )
            if k in p
        }
    return program
