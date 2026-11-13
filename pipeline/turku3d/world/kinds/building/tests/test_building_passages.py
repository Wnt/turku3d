"""Passages and arcades: the map's covered way along a facade opens the ground storey."""

import numpy as np
import shapely
from test_building_compile import gabled_shell
from turku3d.world.kinds.building import compile as compile_module
from turku3d.world.kinds.building import describe, passages


def test_a_covered_footway_along_the_front_is_an_arcade_with_piers_soffit_and_open_ends():
    # A 24 m front from (0, 0) to (24, 0); the building lies north of it, 9 m deep, 18 m high, ground 10.3 m.
    shell = gabled_shell((0.0, 0.0), (24.0, 0.0), depth=9.0, eaves=18.0, ridge=3.0)
    outline = shapely.Polygon([(0, 0), (24, 0), (24, 9), (0, 9)])
    footway = shapely.LineString([(-3.0, 1.4), (27.0, 1.4)])  # mapped 1.4 m inside the front, as on Linnankatu
    beside = shapely.LineString([(-3.0, -3.0), (27.0, -3.0)])  # a covered way that passes outside: no passage
    tags = {"highway": "footway", "covered": "yes", "footway": "sidewalk"}
    shell["passages"] = passages.facts(outline, [(footway, tags, "osm:way/1"), (beside, tags, "osm:way/2")], [])
    assert [p["refs"] for p in shell["passages"]] == [["osm:way/1"]]
    doc = describe.effective("test", shell, {"year": 1961, "storeys": 6, "use": "1050"}, None)
    output = compile_module.compile(doc)

    def survey(part):
        at = part.positions.astype(float) + np.asarray(output.origin)
        return np.column_stack([at[:, 0] + compile_module.ORIGIN_E, compile_module.ORIGIN_N - at[:, 2], at[:, 1]])

    walls = [(survey(p), p.triangles, p.normals) for p in output.parts if p.name in ("walls", "plinth")]
    corners = np.vstack([at[t] for at, t, _ in walls])  # (n, 3 corners, E N H)
    normals = np.vstack([n[t[:, 0]] for _, t, n in walls])
    normals = np.column_stack([normals[:, 0], -normals[:, 2], normals[:, 1]])  # E N H

    def faces_in(e0, e1, n0, n1, h0, h1):
        inside = (
            (corners[..., 0] > e0) & (corners[..., 0] < e1) & (corners[..., 1] > n0) & (corners[..., 1] < n1)
            & (corners[..., 2] > h0) & (corners[..., 2] < h1)
        )
        return inside.all(axis=1)

    # Between the first two piers nothing stands in the front plane or behind it up to the back wall at 2.9 m.
    assert not faces_in(1.0, 3.4, -0.05, 2.8, 10.6, 12.8).any()
    # The front's ground storey stands 2.9 m back: the back wall faces the street.
    back = faces_in(0.5, 23.5, 2.85, 2.95, 10.0, 13.5) & (normals[:, 1] < -0.9)
    assert back.any()
    # Piers on the facade line, about 4 m apart (the old arcade prior): seven over 24 m.
    pier_faces = faces_in(-0.1, 24.1, -0.05, 0.05, 9.5, 13.4) & (normals[:, 1] < -0.9)
    centres = np.sort(corners[pier_faces][..., 0].mean(axis=1))
    assert 1 + (np.diff(centres) > 1.0).sum() == 7
    # A soffit faces down over the arcade, 3 m over the floor or under the first floor.
    soffit = (normals[:, 2] < -0.9) & faces_in(-0.1, 24.1, -0.1, 3.0, 12.0, 13.5)
    assert soffit.any()
    # Both ends are open (the side walls are cut through, a player walks on along the street).
    assert not faces_in(-0.05, 0.05, 0.3, 2.6, 10.6, 12.6).any()
    assert not faces_in(23.95, 24.05, 0.3, 2.6, 10.6, 12.6).any()
