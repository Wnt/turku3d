"""Traffic-sign posts: one post per register sign stack, each plate once, never on the carriageway.

GIS:Liikennemerkit has one row per plate. The furniture stage drew every row as its own post with
one plate, so the register's stacks became clusters of posts at one spot, and plates recorded twice
became visible doubles. In Jonni's edit sessions every sign delete was such a double (two E1 rows
0.02-0.32 m apart), and four of the sign moves took a post off the carriageway to the kerb.

``posts`` groups the tile's sign rows into posts:

1. ``duplicate``: a row with the code of an earlier row within ``DUPLICATE`` is the same plate
   recorded twice and is not drawn (the smaller object id stays).
2. ``stack``: rows within ``POST`` of each other share one post; an additional plate (code ``H…``)
   not already on a post joins the nearest main sign within ``PLATE_REACH``. Plates hang top down:
   main signs in code order, additional plates below them, ``STACK`` apart.
3. ``kerb``: a post inside the surveyed carriageway by at most ``KERB_REACH`` moves just past its
   nearest edge (``roads.signal_posts.kerb_post``, the rule of the signal posts). Island signs
   (``ISLAND``) stay: they mark an island the register drew as carriageway.

The first plate's row is the post; the other plates turn to its facing. ``migrations/signs.py``
(``signs@1``) applies it to the drawn sign batch; the chain runs at the end of every generator build.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import shapely

from turku3d.roads.signal_posts import kerb_post

DUPLICATE = 1.0  # m: the same code this close is one plate recorded twice
POST = 0.5  # m: register plates this close stand on one post
PLATE_REACH = 1.0  # m: an additional plate this close to a main sign hangs under it
STACK = 0.7  # m between plate centres on one post (the plate model is 0.66 m tall)
PLATES = 3  # plates one post carries; a longer stack keeps its lower plates on posts of their own
KERB_REACH = 6.0  # m: a sign deeper inside the carriageway (a gantry, a register gap) stays
# Island signs (keep right / left / either side) mark a traffic island the street register often leaves inside
# the carriageway polygon: they stay where the register put them.
ISLAND = ("D3.1", "D3.2", "D3.3")


@dataclass
class Sign:
    object_id: str
    code: str
    x: float
    z: float
    ref: object = None  # the caller's handle of the drawn row


@dataclass
class Post:
    plates: list[Sign]  # top first; plates[0] is the post's own row
    x: float
    z: float
    rule: str = ""  # "kerb" when the post moved off the carriageway
    merged: list[Sign] = field(default_factory=list)  # duplicates of its plates, not drawn


def additional(code: str) -> bool:
    return code.upper().startswith("H")


def _order(sign: Sign):
    return additional(sign.code), sign.code, sign.object_id


def _cells(items, size):
    cells = defaultdict(list)
    for n, s in enumerate(items):
        cells[(math.floor(s.x / size), math.floor(s.z / size))].append(n)
    return cells


def _near(cells, items, s, size, reach):
    i, j = math.floor(s.x / size), math.floor(s.z / size)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for n in cells.get((i + di, j + dj), ()):
                q = items[n]
                d = math.hypot(q.x - s.x, q.z - s.z)
                if d <= reach:
                    yield n, d


def posts(signs, road: shapely.Geometry | None = None) -> tuple[list[Post], Counter]:
    """The posts of a tile's sign rows, and per-rule counts."""
    stats = Counter()
    kept, duplicates = [], {}
    by_code = defaultdict(list)
    for s in sorted(signs, key=lambda s: s.object_id):
        twin = next((q for q in by_code[s.code] if math.hypot(q.x - s.x, q.z - s.z) <= DUPLICATE), None)
        if twin is not None:
            duplicates.setdefault(id(twin), []).append(s)
            stats["sign_duplicate"] += 1
            continue
        by_code[s.code].append(s)
        kept.append(s)

    parent = list(range(len(kept)))

    def root(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    cells = _cells(kept, PLATE_REACH)
    for n, s in enumerate(kept):
        for m, _ in _near(cells, kept, s, PLATE_REACH, POST):
            if m != n:
                parent[root(m)] = root(n)
    alone = Counter(root(n) for n in range(len(kept)))
    for n, s in enumerate(kept):
        if not additional(s.code) or alone[root(n)] > 1:
            continue
        mains = [(d, m) for m, d in _near(cells, kept, s, PLATE_REACH, PLATE_REACH) if not additional(kept[m].code)]
        if mains:
            parent[root(n)] = root(min(mains)[1])
    groups = defaultdict(list)
    for n, s in enumerate(kept):
        groups[root(n)].append(s)

    result = []
    boundary = None if road is None or road.is_empty else road.boundary
    for members in sorted(groups.values(), key=lambda g: min(s.object_id for s in g)):
        members.sort(key=_order)
        for start in range(0, len(members), PLATES):
            plates = members[start:start + PLATES]
            top = plates[0]
            post = Post(plates, top.x, top.z, merged=[d for s in plates for d in duplicates.get(id(s), ())])
            if len(plates) > 1:
                stats["sign_stacked"] += len(plates) - 1
            if start:
                stats["sign_stack_overflow"] += len(plates)
            if road is not None and not any(s.code in ISLAND for s in plates):
                x, z, rule = kerb_post(top.x, top.z, road, boundary, reach=KERB_REACH)
                if rule == "kerb":
                    post.x, post.z, post.rule = x, z, "kerb"
                    stats["sign_post_kerb"] += 1
                elif rule == "deep":
                    stats["sign_post_deep"] += 1
            stats["sign_posts"] += 1
            result.append(post)
    return result, stats
