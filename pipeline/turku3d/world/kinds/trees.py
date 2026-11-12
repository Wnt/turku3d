"""Stand-in tree kind of the walking skeleton: the city's tree registers per 62.5 m cell, as instanced stand-in trees.

`w2-street` replaces this module (species models, lidar heights and crowns, trees outside the registers).
"""

from __future__ import annotations

import numpy as np

from ..api import Area, Instances, Material, Output, Part, Stub, envelope
from ..frame import CELL, cell_at, cell_box_en, cell_key, to_engine
from ..geometry import seed

LAYERS = (("GIS:Puut", "Kasvilaji"), ("kanta:Lehtipuu", "suku"), ("kanta:Havupuu", "suku"))
SIZES = {"pieni": 6.0, "keskikokoinen": 11.0, "suuri": 17.0}
BARK = Material("bark", {}, (0.07, 0.06, 0.05), 0.95)
LEAVES = Material("foliage", {"season": "summer"}, (0.07, 0.13, 0.035), 0.9)
NEEDLES = Material("foliage", {"season": "evergreen"}, (0.035, 0.075, 0.035), 0.9)


def icosphere() -> tuple[np.ndarray, np.ndarray]:
    t = (1 + 5 ** 0.5) / 2
    v = np.array([[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t],
                  [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]], dtype=float)
    f = np.array([[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2],
                  [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11],
                  [6, 2, 10], [8, 6, 7], [9, 8, 1]])
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    mid = {}
    points, faces = list(v), []
    for a, b, c in f:
        ids = []
        for p, q in ((a, b), (b, c), (c, a)):
            key = (min(p, q), max(p, q))
            if key not in mid:
                m = points[p] + points[q]
                mid[key] = len(points)
                points.append(m / np.linalg.norm(m))
            ids.append(mid[key])
        faces += [[a, ids[0], ids[2]], [b, ids[1], ids[0]], [c, ids[2], ids[1]], ids]
    return np.array(points), np.array(faces)


def tree_asset(variant: int, conifer: bool) -> list[Part]:
    """A unit-height tree (1 m tall, base at the origin): a tapered trunk and a lumpy crown."""
    rng = np.random.default_rng(1000 + variant + (50 if conifer else 0))
    sides, trunk_top, radius = 6, 0.9 if conifer else 0.45, 0.02 if conifer else 0.025
    angle = np.arange(sides) * 2 * np.pi / sides
    ring = np.column_stack([np.cos(angle), np.zeros(sides), np.sin(angle)])
    trunk = np.concatenate([ring * radius, ring * radius * 0.6 + [0, trunk_top, 0]])
    quads = [[k, k + sides, (k + 1) % sides] for k in range(sides)] + [[(k + 1) % sides, k + sides, (k + 1) % sides + sides] for k in range(sides)]
    sphere, faces = icosphere()
    if conifer:
        crown = sphere * [0.16, 0.42, 0.16] * (1 + 0.12 * rng.standard_normal((len(sphere), 1)))
        crown[:, [0, 2]] *= np.clip(1.25 - (crown[:, [1]] + 0.42) / 0.84, 0.25, 1.2)
        crown += [0, 0.57, 0]
    else:
        lumps = 1 + 0.16 * rng.standard_normal((len(sphere), 1))
        crown = sphere * lumps * [0.30 + 0.05 * rng.random(), 0.29, 0.30 + 0.05 * rng.random()] + [0, 0.70, 0]
    return [Part("trunk", trunk, np.array(quads), BARK), Part("crown", crown, faces, NEEDLES if conifer else LEAVES)]


class Trees:
    name = "trees"

    def discover(self, area: Area, sources) -> list[Stub]:
        box = area.bbox(CELL)
        cells: dict[tuple[int, int], list[dict]] = {}
        seen: list[tuple[float, float]] = []
        for layer, species_field in LAYERS:
            added = []
            for feature in sources.wfs.features(layer, box):
                for geom in feature.geoms:
                    if geom.type != "Point":
                        continue
                    e, n = round(geom.shape.x, 2), round(geom.shape.y, 2)
                    # The base map repeats many register trees; keep the first source's tree within 2 m.
                    if any(abs(e - a) < 2 and abs(n - b) < 2 for a, b in seen):
                        continue
                    added.append((e, n))
                    cells.setdefault(cell_at(e, n), []).append({
                        "e": e, "n": n, "source": layer, "species": feature.attrs.get(species_field, ""),
                        "size": feature.attrs.get("pituus", ""), "planted": feature.attrs.get("Istutusvuosi", ""),
                        "conifer": layer == "kanta:Havupuu" or feature.attrs.get("Kasviryhma") == "Havupuu"})
            seen += added
        stubs = []
        for (i, j), trees in sorted(cells.items()):
            cell = cell_box_en(i, j)
            if not area.touches(cell):
                continue
            trees.sort(key=lambda t: (t["e"], t["n"]))
            heights = sources.terrain.heights([t["e"] for t in trees], [t["n"] for t in trees])
            trees = [{**t, "ground": round(float(h), 3)} for t, h in zip(trees, heights) if np.isfinite(h)]
            if trees:
                refs = [{"source": f"turku-wfs:{layer}", "key": cell_key(i, j)} for layer, _ in LAYERS]
                stubs.append(Stub(f"trees/{cell_key(i, j)}", cell, refs, {"cell": [i, j], "trees": trees}))
        return stubs

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        description = {**envelope(self.name, 0, stub, stored, "w2-core stand-in"), **stub.facts}
        for name, value in (stored or {}).items():
            if name not in ("schema", "id", "tier", "provenance"):
                description[name] = value
        return description

    def compile(self, description: dict) -> Output:
        i, j = description["cell"]
        trees = description["trees"]
        origin = (i * CELL, float(np.floor(min(t["ground"] for t in trees))), j * CELL)
        groups: dict[tuple[int, bool], list[list[float]]] = {}
        for tree in trees:
            pick = seed(f"{tree['e']:.2f},{tree['n']:.2f}")
            rng = np.random.default_rng(pick)
            planted = int(tree["planted"]) if tree["planted"].isdigit() else 0
            height = SIZES.get(tree["size"]) or (min(16.0, 4.0 + 0.35 * (2026 - planted)) if planted > 1900 else 12.0)
            height *= 0.85 + 0.3 * rng.random()
            width = height * (0.9 + 0.3 * rng.random())
            x, y, z = to_engine(tree["e"], tree["n"], tree["ground"])
            groups.setdefault((pick % 3, bool(tree["conifer"])), []).append(
                [x - origin[0], y - origin[1], z - origin[2], rng.random() * 2 * np.pi, width, height, width])
        instances = [Instances(tree_asset(variant, conifer), np.round(np.array(rows), 3))
                     for (variant, conifer), rows in sorted(groups.items())]
        return Output(origin, [], instances)
