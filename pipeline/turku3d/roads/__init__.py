"""Street routing graph from OSM highways (minimap and route guidance).

The graph is in the engine frame (x east, z south, metres). Nodes are way endpoints and
vertices shared by two or more ways; an edge is the polyline between two nodes along one way.
Edge rows are ``[a, b, flags, length, x1, z1, ..., xk, zk]`` with the interior vertices only.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

FORMAT = "turku3d-roadgraph"
VERSION = 1
FOOT, CAR, ONEWAY = 1, 2, 4
CLASS_SHIFT = 3
CLASSES = ("path", "service", "street", "major")
PRECISION = 0.1  # metres; shared OSM nodes project to identical coordinates

CAR_CLASS = {
    "motorway": 3,
    "trunk": 3,
    "primary": 3,
    "secondary": 3,
    "motorway_link": 3,
    "trunk_link": 3,
    "primary_link": 3,
    "secondary_link": 3,
    "tertiary": 2,
    "tertiary_link": 2,
    "unclassified": 2,
    "residential": 2,
    "living_street": 2,
    "service": 1,
}
FOOT_ONLY = {"footway", "path", "pedestrian", "steps", "cycleway", "track", "bridleway", "platform"}
NO_FOOT = {"motorway", "motorway_link", "trunk", "trunk_link"}
DENIED = {"no", "private"}


def way_flags(highway: str | None, tags: dict) -> int:
    """FOOT/CAR/ONEWAY/class bits for one OSM way; 0 when it is not part of the graph."""
    if tags.get("area") == "yes" or tags.get("indoor") not in (None, "no"):
        return 0
    if tags.get("access") in DENIED:
        return 0
    flags, cls = 0, 0
    if highway in CAR_CLASS:
        cls = CAR_CLASS[highway]
        motor = tags.get("motor_vehicle", tags.get("motorcar", tags.get("vehicle")))
        if motor not in DENIED:
            flags |= CAR
        if highway not in NO_FOOT:
            flags |= FOOT
    elif highway in FOOT_ONLY:
        flags |= FOOT
    if tags.get("foot") in DENIED:
        flags &= ~FOOT
    elif tags.get("foot") in ("yes", "designated") and flags:
        flags |= FOOT
    if not flags:
        return 0
    oneway = tags.get("oneway", "yes" if tags.get("junction") == "roundabout" else "no")
    if flags & CAR and oneway in ("yes", "1", "true", "-1"):
        flags |= ONEWAY
    return flags | cls << CLASS_SHIFT


def reversed_oneway(tags: dict) -> bool:
    return tags.get("oneway") == "-1"


def osm_ways(path: Path, bbox):
    """(engine x/z vertices, flags) of every OSM highway in the parquet at `path`, clipped to `bbox`."""
    from shapely import box, get_parts

    from turku3d.spatial import frame as spatial_frame

    frame = spatial_frame(path, bbox)
    frame = frame[frame["highway"].notna()]
    clip = box(*bbox.as_tuple())
    for row in frame.itertuples():
        line = row.geometry_local
        if line.geom_type != "LineString" or not line.intersects(clip):
            continue
        tags = json.loads(row.tags)
        flags = way_flags(row.highway, tags)
        if not flags:
            continue
        for part in get_parts(line.intersection(clip)):
            if part.geom_type != "LineString" or part.is_empty:
                continue
            coords = [(x, z) for x, z, *_ in part.coords]
            yield (coords[::-1] if reversed_oneway(tags) else coords), flags


def _key(x: float, z: float) -> tuple[int, int]:
    return round(x / PRECISION), round(z / PRECISION)


def _coord(k: int) -> float:
    return round(k * PRECISION, 1)


def build_graph(
    ways: Iterable[tuple[list[tuple[float, float]], int]],
    bbox,
    area: str,
    *,
    connected: bool = True,
) -> dict:
    """``ways`` are (engine x/z vertices, flags) already clipped to ``bbox`` (xmin, zmin, xmax, zmax).

    ``connected`` keeps each mode only on its largest network; a draft tile's graph (``turku3d.coarse``)
    keeps every piece, since its fragments join the neighbouring tiles' graphs in the game.
    """
    parts = []
    for coords, flags in ways:
        keys = []
        for x, z in coords:
            k = _key(x, z)
            if not keys or keys[-1] != k:
                keys.append(k)
        if len(keys) >= 2 and flags:
            parts.append((keys, flags))
    uses = defaultdict(int)
    for keys, _ in parts:
        # Two visits (two ways, or one way crossing itself) or an endpoint make a node.
        for k in keys[1:-1]:
            uses[k] += 1
        uses[keys[0]] += 2
        uses[keys[-1]] += 2
    raw = []
    for keys, flags in parts:
        start = 0
        for i in range(1, len(keys)):
            if uses[keys[i]] >= 2:
                raw.append((keys[start : i + 1], flags))
                start = i
    if connected:
        raw = _largest_components(raw)
    index: dict[tuple[int, int], int] = {}
    nodes, edges = [], []
    for keys, flags in sorted(raw, key=lambda item: (item[0][0], item[0][-1], item[0], item[1])):
        ids = []
        for k in (keys[0], keys[-1]):
            if k not in index:
                index[k] = len(index)
                nodes.extend((_coord(k[0]), _coord(k[1])))
            ids.append(index[k])
        length = _length(keys)
        interior = [_coord(v) for k in keys[1:-1] for v in k]
        edges.append([ids[0], ids[1], flags, round(length, 1), *interior])
    graph = {
        "format": FORMAT,
        "version": VERSION,
        "frame": "engine-xz",
        "area": area,
        "bbox": [round(float(v), 1) for v in bbox],
        "bits": {
            "foot": FOOT,
            "car": CAR,
            "oneway": ONEWAY,
            "class_shift": CLASS_SHIFT,
            "classes": list(CLASSES),
        },
        "nodes": nodes,
        "edges": edges,
    }
    validate(graph)
    return graph


def _length(keys) -> float:
    return sum(math.dist(keys[i], keys[i + 1]) for i in range(len(keys) - 1)) * PRECISION


def _root(parent: dict, k):
    while parent.setdefault(k, k) != k:
        parent[k] = parent[parent[k]]
        k = parent[k]
    return k


def _largest_components(raw):
    """Keep each mode only on its largest connected (undirected) network."""
    for mode in (FOOT, CAR):
        parent: dict = {}
        for keys, flags in raw:
            if flags & mode:
                parent[_root(parent, keys[0])] = _root(parent, keys[-1])
        size: dict = defaultdict(float)
        for keys, flags in raw:
            if flags & mode:
                size[_root(parent, keys[0])] += _length(keys)
        if not size:
            continue
        keep = max(size, key=lambda root: (size[root], root))
        drop = ~mode & ~(ONEWAY if mode == CAR else 0)
        raw = [
            (keys, flags if not flags & mode or _root(parent, keys[0]) == keep else flags & drop)
            for keys, flags in raw
        ]
    return [(keys, flags) for keys, flags in raw if flags & (FOOT | CAR)]


def validate(graph: dict) -> None:
    if graph.get("format") != FORMAT or graph.get("version") != VERSION:
        raise ValueError(f"expected {FORMAT} v{VERSION}")
    if graph.get("frame") != "engine-xz":
        raise ValueError("road graph must use the engine-xz frame")
    nodes, edges = graph["nodes"], graph["edges"]
    if len(nodes) % 2:
        raise ValueError("nodes must be flat x/z pairs")
    count = len(nodes) // 2
    for edge in edges:
        a, b, flags, length = edge[:4]
        if not (0 <= a < count and 0 <= b < count) or len(edge) % 2:
            raise ValueError(f"invalid edge {edge[:4]}")
        if not flags & (FOOT | CAR) or not length > 0:
            raise ValueError(f"edge without mode or length {edge[:4]}")


def encode(graph: dict) -> bytes:
    return (json.dumps(graph, separators=(",", ":")) + "\n").encode()


def publish(layer: Path, tree) -> bool:
    """Copy the layer's graph to a content-addressed dist URL; True when the entry changed.

    Superseded graphs stay on disk like other content-addressed dist files, so a failed
    assembly never leaves the committed manifest pointing at a removed file.
    """
    previous = tree.roads
    manifest = layer / "MANIFEST.json"
    if not manifest.is_file():
        tree.roads = None
        return previous is not None
    record = json.loads(manifest.read_text())["params"]["graph"]
    data = (layer / record["file"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError(f"{layer}: road graph hash mismatch; run make refresh")
    graph = json.loads(data)
    validate(graph)
    entry = tree._put(f"roads/{record['sha256'][:16]}.json", data)
    entry.update(nodes=len(graph["nodes"]) // 2, edges=len(graph["edges"]))
    tree.roads = entry
    return previous != entry
