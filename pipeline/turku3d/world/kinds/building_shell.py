"""Building sources and the stand-in building kind of the walking skeleton.

`discover` is the source side of the `building` kind and stays: one entity per LoD2 building, with the facts of
docs/specs/building-description.md that always come from sources (`shell`, `register`). `describe` and `compile`
here are the stand-in until the generator of `w2-buildings` (world/kinds/building/) takes the registry row: the
LoD2 wall and roof polygons as they are, coloured from the register's facade material and year.
"""

from __future__ import annotations

import re

import numpy as np
import shapely

from ..api import Area, Material, Output, Part, Stub, envelope
from ..frame import to_engine
from ..geometry import merge, seed, triangulate_planar

SURFACES = {"WallSurface": "wall", "RoofSurface": "roof", "GroundSurface": "ground", "ClosureSurface": "closure"}
TOUCH = 0.3  # buildings closer than this share a party wall
# Register footprints (the city's base map) stand in where no LoD2 building covers them: buildings and outbuildings.
OUTBUILDING = "66001"
FOOTPRINT_CLASSES = ("66000", OUTBUILDING)
MIN_FOOTPRINT = 6.0  # square metres
COVERED = 0.3  # share of a footprint under LoD2 buildings above which it is the same building
STOREY_HEIGHT, ROOF_ALLOWANCE, OUTBUILDING_HEIGHT = 3.0, 0.8, 2.7
# Linear RGB wall colours by register facade material, a few per material: picked per building by its id.
WALLS = {
    "puu": ("wood-siding", [(0.62, 0.50, 0.24), (0.55, 0.20, 0.13), (0.42, 0.47, 0.36), (0.66, 0.62, 0.50), (0.35, 0.42, 0.50)]),
    "tiili": ("brick", [(0.36, 0.13, 0.08), (0.42, 0.19, 0.11), (0.50, 0.33, 0.20)]),
    "betoni": ("concrete", [(0.55, 0.54, 0.50), (0.62, 0.58, 0.50), (0.48, 0.48, 0.47)]),
    "kivi": ("plaster", [(0.60, 0.52, 0.40), (0.58, 0.56, 0.50)]),
    "metallilevy": ("metal-cladding", [(0.45, 0.47, 0.50), (0.30, 0.33, 0.36)]),
    "lasi": ("glass", [(0.20, 0.27, 0.32)]),
}
OLD_PLASTER = [(0.66, 0.55, 0.36), (0.62, 0.47, 0.34), (0.60, 0.58, 0.50), (0.55, 0.38, 0.28), (0.52, 0.54, 0.46)]
NEW_PLASTER = [(0.66, 0.64, 0.58), (0.60, 0.57, 0.48), (0.56, 0.52, 0.44)]


def ring_points(ring) -> list[list[float]]:
    """A shapely ring as open [E, N, H] rows in millimetres."""
    coords = np.asarray(ring.coords)[:-1]
    if coords.shape[1] == 2:
        coords = np.column_stack([coords, np.zeros(len(coords))])
    return np.round(coords, 3).tolist()


def year_of(attrs: dict) -> int | None:
    for name in ("yearOfConstruction", "valmistunut"):
        match = re.search(r"(1[5-9]\d\d|20\d\d)", attrs.get(name, ""))
        if match:
            return int(match.group(1))
    return None


def number(text: str | None) -> float | None:
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def footprint_shell(outline, storeys: float | None, outbuilding: bool, sources) -> tuple[list[dict], float, float] | None:
    """A register footprint raised to a flat-roofed box where the city has no LoD2 model (new buildings, most
    outbuildings): (surfaces, base, top), or None where there is no ground height. Storeys give the height."""
    surfaces, base, top = [], np.inf, -np.inf
    height = OUTBUILDING_HEIGHT if outbuilding and not storeys else (storeys or 1.0) * STOREY_HEIGHT + ROOF_ALLOWANCE
    for part in shapely.get_parts(outline):
        if part.geom_type != "Polygon" or part.area < 1.0:
            continue
        part = shapely.geometry.polygon.orient(part, 1.0)  # exterior counter-clockwise seen from above
        ground = sources.terrain.heights(*np.asarray(part.exterior.coords)[:-1].T)
        if not np.isfinite(ground).all():
            continue
        low, high = float(ground.min()), float(ground.max()) + height
        base, top = min(base, low), max(top, high)
        for ring in (part.exterior, *part.interiors):
            points = np.asarray(ring.coords)
            for a, b in zip(points[:-1], points[1:]):
                surfaces.append({"kind": "wall", "rings": [[[*a, low], [*b, low], [*b, high], [*a, high]]]})
        level = lambda ring, h: np.round(np.column_stack([np.asarray(ring.coords)[:-1], np.full(len(ring.coords) - 1, h)]), 3).tolist()  # noqa: E731
        surfaces.append({"kind": "roof", "rings": [level(part.exterior, high), *(level(r, high) for r in part.interiors)]})
        surfaces.append({"kind": "ground", "rings": [level(part.exterior, low)[::-1], *(level(r, low)[::-1] for r in part.interiors)]})
    for surface in surfaces:
        surface["rings"] = [np.round(np.asarray(ring, dtype=float), 3).tolist() for ring in surface["rings"]]
    return (surfaces, base, top) if surfaces else None


def roof_colour(outline, raster) -> str | None:
    """The colour of a roof in the orthophoto: the median of its lit pixels away from the eaves, without tree crowns
    and deep shadow (the old game's rule). None where trees hide the roof or there is no imagery."""
    inner = outline.buffer(-0.75)
    if inner.is_empty or inner.area < 0.25 * outline.area:
        inner = outline
    e0, n0, e1, n1 = inner.bounds
    step = max(raster.px_m, 0.4)
    es, ns = np.meshgrid(np.arange(e0 + step / 2, e1, step), np.arange(n0 + step / 2, n1, step))
    inside = shapely.contains_xy(inner, es, ns)
    if inside.sum() < 12:
        return None
    rgb, valid = raster.at(es[inside], ns[inside])
    samples = rgb[valid].astype(float)
    if len(samples) < 12:
        return None
    r, g, b = samples.T
    clean = samples[(samples.max(axis=1) > 5) & ~((g > r * 1.2) & (g > b * 1.15))]
    if len(clean) < 0.4 * len(samples):
        return None
    luma = clean @ [0.2126, 0.7152, 0.0722]
    lit = (luma >= np.percentile(luma, 75) * 0.6) & (luma <= np.percentile(luma, 98))
    if lit.any():
        clean = clean[lit]
    red, green, blue = (int(round(v)) for v in np.median(clean, axis=0))
    return f"#{red:02x}{green:02x}{blue:02x}"


# Premises that show to the street as a glazed ground storey: every shop, and these services.
STREET_AMENITIES = {"restaurant", "cafe", "bar", "pub", "fast_food", "bank", "pharmacy", "ice_cream", "nightclub",
                    "post_office", "cinema", "bureau_de_change", "biergarten"}


def street_premises(sources, box) -> np.ndarray:
    """(E, N) of the open map's shops and street-front services in the box: one point a premise."""
    try:
        frame = sources.osm.features(box)
    except Exception:  # no map table (offline): no building is known to hold a shop
        return np.zeros((0, 2))
    rows = frame[frame["shop"].notna() | frame["amenity"].isin(STREET_AMENITIES)]
    points = [shape if shape.geom_type == "Point" else shape.representative_point() for shape in rows.geometry]
    return np.array([[point.x, point.y] for point in points]).reshape(-1, 2)


def discover(area: Area, sources) -> list[Stub]:
    """One stub per building that touches the area: `shell` and `register` as the building description has them.
    A building is a LoD2 building, or a register footprint that no LoD2 building covers."""
    # Neighbours of a building at the rim lie outside the circle: read a margin, select by the circle.
    box = area.bbox(60.0)
    try:
        aerial = sources.ortho.image(box, 0.2)  # the level the ground kind reads: one set of cached chunks
    except Exception:  # no imagery (offline, outside the city's coverage): roofs keep the colour of their style
        aerial = None
    listed = sources.wfs.features("kanta:Rakennus", box)
    register = {feature.attrs["rakennustunnus"]: feature.attrs for feature in listed if feature.attrs.get("rakennustunnus")}
    found = []
    for feature in sources.wfs.features("bldg:Building_LOD2", box):
        surfaces, ground = [], []
        for geom in feature.geoms:
            kind = SURFACES.get(geom.surface_type)
            if geom.role != "lod2MultiSurface" or geom.type != "Polygon" or not kind:
                continue
            surfaces.append({"kind": kind, "rings": [ring_points(geom.shape.exterior), *(ring_points(r) for r in geom.shape.interiors)]})
            if kind == "ground":
                ground.append(shapely.make_valid(shapely.force_2d(geom.shape)))
        walls = [np.array(s["rings"][0]) for s in surfaces if s["kind"] == "wall"]
        if not walls:
            continue
        points = np.concatenate(walls)
        outline = shapely.unary_union(ground) if ground else shapely.MultiPoint(points[:, :2]).convex_hull
        if outline.is_empty:
            continue
        attrs = feature.attrs
        prt = attrs.get("pysyvarakennustunnus")
        key = prt or "gml-" + re.sub(r"[^A-Za-z0-9._-]", "-", feature.gml_id or feature.key)
        row = register.get(prt, {})
        refs = [{"source": "turku-lod2", "key": feature.gml_id or feature.key}]
        if row:
            refs.append({"source": "turku-kanta-rakennus", "key": prt})
        found.append({
            "id": f"building/{key}", "source": "turku-lod2", "surfaces": surfaces, "outline": outline, "refs": refs,
            "base": float(points[:, 2].min()), "top": float(points[:, 2].max()),
            "address": " ".join(filter(None, [attrs.get("osoitenimi_fi"), attrs.get("osoitenumero")])), "year": year_of(attrs),
            "storeys": number(attrs.get("kerrosluku") or attrs.get("storeysAboveGround")),
            "use": attrs.get("käyttötarkoitus") or attrs.get("function"), "material": row.get("julkisivumateriaali"),
            "height": number(attrs.get("measuredHeight"))})
    modelled = {b["id"] for b in found}
    covered = shapely.unary_union([b["outline"] for b in found]) if found else shapely.Polygon()
    premises = street_premises(sources, box)
    seen = set()
    for feature in listed:
        attrs = feature.attrs
        key = attrs.get("rakennustunnus") or "kanta-" + re.sub(r"[^A-Za-z0-9._-]", "-", feature.gml_id or feature.key)
        if attrs.get("kplkoodi") not in FOOTPRINT_CLASSES or f"building/{key}" in modelled or key in seen:
            continue
        polygons = [shapely.make_valid(shapely.force_2d(g.shape)) for g in feature.geoms if g.type == "Polygon"]
        outline = shapely.unary_union(polygons) if polygons else shapely.Polygon()
        if outline.area < MIN_FOOTPRINT or outline.intersection(covered).area > COVERED * outline.area:
            continue
        if shapely.distance(outline, shapely.Point(area.e, area.n)) > area.radius + 60.0:
            continue  # only the selected buildings and their possible neighbours need ground heights
        storeys = number(attrs.get("kerrosluku"))
        shell = footprint_shell(outline, storeys, attrs.get("kplkoodi") == OUTBUILDING, sources)
        if shell is None:
            continue
        seen.add(key)
        found.append({
            "id": f"building/{key}", "source": "turku-kanta-footprint", "surfaces": shell[0], "outline": outline,
            "refs": [{"source": "turku-kanta-rakennus", "key": attrs.get("rakennustunnus") or feature.gml_id or feature.key}],
            "base": shell[1], "top": shell[2], "address": " ".join(filter(None, [attrs.get("teksti"), attrs.get("osoitenumero")])),
            "year": year_of({"valmistunut": attrs.get("kottovuosi") or ""}), "storeys": storeys,
            "use": "outbuilding" if attrs.get("kplkoodi") == OUTBUILDING else None, "material": attrs.get("julkisivumateriaali"),
            "height": None})
    tree = shapely.STRtree([b["outline"] for b in found])
    centre, stubs = shapely.Point(area.e, area.n), []
    for index, building in enumerate(found):
        outline = building["outline"]
        if shapely.distance(outline, centre) > area.radius:
            continue
        terrain = []
        for part in shapely.get_parts(outline):
            if part.geom_type == "Polygon":
                edge = shapely.get_coordinates(shapely.segmentize(part.exterior, 2.0))[:-1]
                heights = sources.terrain.heights(edge[:, 0], edge[:, 1])
                if np.isfinite(heights).all():
                    terrain.append(np.round(np.column_stack([edge, heights]), 3).tolist())
        neighbours = []
        for other in tree.query(outline, predicate="dwithin", distance=TOUCH):
            if other != index:
                near = found[other]
                largest = max((p for p in shapely.get_parts(near["outline"]) if p.geom_type == "Polygon"), key=lambda p: p.area, default=None)
                if largest is not None:
                    neighbours.append({"id": near["id"], "footprint": np.round(np.asarray(largest.exterior.coords)[:-1], 3).tolist(),
                                       "base_h": round(near["base"], 3), "top_h": round(near["top"], 3)})
        storeys, height = building["storeys"], building["height"]
        year_near = None
        if not building["year"]:
            # The old game's rule for an undated building: the years of its dated neighbours, nearer ones first
            # (weight 1 / (5 + d)^2); 60 m is the margin read round the area, so the rim sees what the middle sees.
            near = [(found[other]["year"], 1.0 / (5.0 + shapely.distance(outline, found[other]["outline"])) ** 2)
                    for other in tree.query(outline, predicate="dwithin", distance=60.0)
                    if other != index and found[other]["year"]]
            if near:
                near.sort()
                half, run = sum(weight for _, weight in near) / 2, 0.0
                for year, weight in near:
                    run += weight
                    if run >= half:
                        year_near = int(year)
                        break
        facts = {
            "shell": {"source": building["source"], "surfaces": building["surfaces"], "terrain": terrain,
                      "neighbours": sorted(neighbours, key=lambda n: n["id"])},
            "register": {name: value for name, value in {
                "address": building["address"], "year": building["year"],
                "storeys": int(storeys) if storeys and storeys >= 1 else None,
                "use": building["use"], "material": building["material"],
                "height_m": height if height and height > 0 else None,
                "roof_colour": roof_colour(outline, aerial) if aerial is not None else None,
                "year_near": year_near,
                "premises": inside(premises, outline),
            }.items() if value not in (None, "", [])},
        }
        stubs.append(Stub(building["id"], tuple(round(v, 3) for v in outline.bounds), building["refs"], facts))
    return stubs


def inside(points: np.ndarray, outline) -> list:
    """The points on or within 1.5 m of an outline, at most twelve, to the decimetre."""
    if not len(points):
        return []
    low, high = np.array(outline.bounds[:2]) - 1.5, np.array(outline.bounds[2:]) + 1.5
    near = points[((points >= low) & (points <= high)).all(axis=1)]
    if not len(near):
        return []
    held = near[shapely.dwithin(shapely.points(near), outline, 1.5)]
    return sorted(np.round(held, 1).tolist())[:12]


class ShellBuilding:
    """The stand-in: `turku-description/building/1` with only what the sources give, compiled as the bare shell."""

    name = "building"

    def discover(self, area: Area, sources) -> list[Stub]:
        return discover(area, sources)

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        description = {**envelope(self.name, 1, stub, stored, "w2-core"), **stub.facts}
        for name, value in (stored or {}).items():
            if name not in ("schema", "id", "tier", "provenance", "shell", "register"):
                description[name] = value
        return description

    def compile(self, description: dict) -> Output:
        shell, register = description["shell"], description.get("register", {})
        pick = seed(description["id"])
        family, palette = WALLS.get(register.get("material", ""), (None, None))
        if not family:
            family, palette = "plaster", OLD_PLASTER if register.get("year", 1960) < 1940 else NEW_PLASTER
        looks = {"wall": (family, palette[pick % len(palette)], 0.9), "roof": ("roof-sheet", (0.10, 0.10, 0.11), 0.55)}
        drawn = [s for s in shell["surfaces"] if s["kind"] in looks]
        every = np.concatenate([np.array(r) for s in drawn for r in s["rings"]])
        ground = [p[2] for line in shell.get("terrain", []) for p in line]
        base = min([float(every[:, 2].min()), *ground])
        origin_e, origin_n = np.round(every[:, :2].mean(axis=0), 2)
        origin = to_engine(float(origin_e), float(origin_n), round(base, 2))
        meshes: dict[str, list] = {"wall": [], "roof": []}
        for surface in drawn:
            local = []
            for ring in surface["rings"]:
                x, y, z = to_engine(*np.array(ring).T)
                local.append(np.column_stack([x - origin[0], y - origin[1], z - origin[2]]))
            mesh = triangulate_planar(local)
            if mesh is None:
                continue
            points, faces, _normal, uv = mesh
            if surface["kind"] == "roof":
                uv = np.column_stack([points[:, 0] + origin[0], points[:, 2] + origin[2]])
            meshes[surface["kind"]].append((points, faces, uv))
        parts = []
        for kind, name in (("wall", "walls"), ("roof", "roof")):
            if meshes[kind]:
                family, color, roughness = looks[kind]
                positions, faces, uvs = merge(meshes[kind])
                parts.append(Part(name, positions, faces, Material(family, {"color": list(color)}, tuple(color), roughness), uvs=uvs))
        return Output(origin, parts)
