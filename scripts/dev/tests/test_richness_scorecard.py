"""Scorecard geometry and tile reading on synthetic data: no network, no real tiles."""

import json
import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import richness_scorecard as rs  # noqa: E402


def glb(js: dict, bin_data: bytes = b"") -> bytes:
    data = json.dumps(js).encode()
    data += b" " * ((-len(data)) % 4)
    bin_data += b"\0" * ((-len(bin_data)) % 4)
    chunks = struct.pack("<II", len(data), 0x4E4F534A) + data
    if bin_data:
        chunks += struct.pack("<II", len(bin_data), 0x004E4942) + bin_data
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks


def prop_tile(origin, props, bollards=(), paint=None):
    """A minimal tile GLB: one batched prop mesh (one square per prop), instanced bollards and
    optionally one paint triangle; positions are tile-local under a translated root."""
    pos, idx, objects = [], [], []
    for n, (kind, ident, x, z) in enumerate(props):
        base = len(pos)
        pos += [(x - 0.1, 0, z - 0.1), (x + 0.1, 0, z - 0.1), (x, 2, z + 0.1)]
        idx += [base, base + 1, base + 2]
        objects.append({"start": 3 * n, "count": 3, "prop_type": kind, "object_id": ident})
    p = np.asarray(pos, np.float32).tobytes()
    i = np.asarray(idx, np.uint32).tobytes()
    t = np.asarray(bollards, np.float32).reshape(-1, 3).tobytes()
    blob = p + i + t
    views = [
        {"buffer": 0, "byteOffset": 0, "byteLength": len(p)},
        {"buffer": 0, "byteOffset": len(p), "byteLength": len(i)},
        {"buffer": 0, "byteOffset": len(p) + len(i), "byteLength": max(len(t), 4)},
    ]
    accessors = [
        {"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3"},
        {"bufferView": 1, "componentType": 5125, "count": len(idx), "type": "SCALAR"},
        {"bufferView": 2, "componentType": 5126, "count": len(bollards), "type": "VEC3"},
    ]
    nodes = [
        {"translation": list(origin), "children": [1, 2]},
        {"mesh": 0, "extras": {"kind": "prop", "objects": objects}},
        {
            "mesh": 0,
            "extras": {"kind": "prop", "object_id": "bollard"},
            "extensions": {"EXT_mesh_gpu_instancing": {"attributes": {"TRANSLATION": 2}}},
        },
    ]
    meshes = [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}]
    if paint is not None:
        q = np.asarray(paint, np.float32).tobytes()
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(q)})
        accessors.append({"bufferView": 3, "componentType": 5126, "count": 3, "type": "VEC3"})
        r = np.asarray([0, 1, 2], np.uint32).tobytes()
        views.append({"buffer": 0, "byteOffset": len(blob) + len(q), "byteLength": len(r)})
        accessors.append({"bufferView": 4, "componentType": 5125, "count": 3, "type": "SCALAR"})
        blob += q + r
        meshes.append({"primitives": [{"attributes": {"POSITION": 3}, "indices": 4}]})
        nodes.append({"mesh": 1, "extras": {"kind": "markings", "material_key": "road_paint"}})
        nodes[0]["children"].append(3)
    js = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    return glb(js, blob)


class RadiusTests(unittest.TestCase):
    def test_points_on_the_circle_count(self):
        pts = [(50, 0), (0, -50), (35.4, 35.4), (50.01, 0), (100, 100)]
        self.assertEqual(
            rs.within_radius(pts, (0, 0), 50).tolist(), [True, True, False, False, False]
        )

    def test_segment_through_the_disc(self):
        self.assertAlmostEqual(rs.segment_in_circle((-100, 0), (100, 0), (0, 0), 50), 100)
        self.assertAlmostEqual(rs.segment_in_circle((0, 0), (100, 0), (0, 0), 50), 50)
        self.assertAlmostEqual(rs.segment_in_circle((-10, 0), (10, 0), (0, 0), 50), 20)
        self.assertEqual(rs.segment_in_circle((-100, 60), (100, 60), (0, 0), 50), 0)
        self.assertAlmostEqual(rs.segment_in_circle((-100, 30), (100, 30), (0, 0), 50), 80)

    def test_polyline_length_in_disc(self):
        line = [(-100, 0), (0, 0), (0, 100)]
        self.assertAlmostEqual(rs.polyline_in_circle(line, (0, 0), 50), 100)


class CorridorTests(unittest.TestCase):
    def setUp(self):
        self.c = rs.Corridor(np.array([(0, 0), (100, 0), (200, 0)]), 10)

    def test_length_and_per_100m(self):
        self.assertAlmostEqual(self.c.length, 200)
        self.assertEqual(rs.per_100m(6, self.c.length), 3)
        self.assertIsNone(rs.per_100m(1, 0))

    def test_both_sides_and_flat_caps(self):
        pts = [(50, 9), (50, -9), (150, 10), (150, 10.1), (-0.1, 0), (200.1, 0), (0, 5), (200, -5)]
        self.assertEqual(
            self.c.contains(pts).tolist(), [True, True, True, False, False, False, True, True]
        )

    def test_sides(self):
        # Looking east along +x: north (negative z) is on the left.
        left, right = self.c.sides(np.array([(10, -5), (20, -5), (30, 5), (40, 50)]))
        self.assertEqual((left, right), (2, 1))

    def test_bent_street(self):
        c = rs.Corridor(np.array([(0, 0), (100, 0), (100, 100)]), 10)
        self.assertAlmostEqual(c.length, 200)
        self.assertEqual(
            c.contains([(95, 50), (105, 50), (50, 5), (120, 50)]).tolist(),
            [True, True, True, False],
        )

    def test_needs_two_points(self):
        with self.assertRaises(ValueError):
            rs.Corridor(np.array([(0, 0)]), 5)


class SeamTests(unittest.TestCase):
    def test_tile_ids_and_half_open_squares(self):
        self.assertEqual(rs.tile_of(0, 0), "t0_0_0")
        self.assertEqual(rs.tile_of(-0.01, 249.99), "t0_-1_0")
        self.assertEqual(rs.tile_of(250, -250), "t0_1_-1")
        self.assertEqual(rs.tile_square("t0_-1_2"), (-250, 500, 0, 750))
        on_edge = np.array([(250, 10)])
        self.assertEqual(rs.in_square(on_edge, rs.tile_square("t0_0_0")).tolist(), [False])
        self.assertEqual(rs.in_square(on_edge, rs.tile_square("t0_1_0")).tolist(), [True])

    def test_clip_polyline_to_square(self):
        segs = rs.clip_polyline([(-50, 100), (300, 100)], (0, 0, 250, 250))
        self.assertEqual(len(segs), 1)
        (ax, az), (bx, bz) = segs[0]
        self.assertAlmostEqual(ax, 0)
        self.assertAlmostEqual(bx, 250)
        self.assertEqual(rs.clip_polyline([(-50, 300), (300, 300)], (0, 0, 250, 250)), [])

    def test_merge_counts_seam_objects_once(self):
        a, b = rs.TileData("t0_0_0"), rs.TileData("t0_1_0")
        lamp = rs.Item("lamp", "lamp", "osm:1", 250.0, 10.0)
        a.items, b.items = (
            [lamp],
            [
                rs.Item("lamp", "lamp", "osm:1", 250.0, 10.0),
                rs.Item("lamp", "lamp", "osm:2", 260, 10),
            ],
        )
        # Both tiles carry the same street across the seam; each counts only its own square.
        a.roads = [(rs.CAR, np.array([(200.0, 10.0), (300.0, 10.0)]))]
        b.roads = [(rs.CAR, np.array([(200.0, 10.0), (300.0, 10.0)]))]
        a.paint = np.array([[249.0, 10.0, 1.0], [251.0, 10.0, 1.0]])
        b.paint = np.array([[251.0, 10.0, 1.0]])
        items, paint, roads = rs.merge([a, b])
        self.assertEqual(sorted(i.ident for i in items), ["osm:1", "osm:2"])
        self.assertAlmostEqual(sum(rs.polyline_in_circle(s, (250, 10), 50) for _, s in roads), 100)
        self.assertEqual(len(paint), 2)

    def test_needed_tiles_cover_disc_and_corridor(self):
        cam = {
            "pos": [245, 0, 5],
            "street": {"polyline": [[245, 5], [245, 300]], "half_width_m": 10},
        }
        self.assertEqual(
            rs.needed_tiles(cam), ["t0_0_-1", "t0_0_0", "t0_0_1", "t0_1_-1", "t0_1_0", "t0_1_1"]
        )


class TileReadingTests(unittest.TestCase):
    def tiles(self, root: Path):
        """Two neighbouring tiles; the same lamp sits on their seam in both, as a G1 producer might."""
        specs = {
            "t0_0_0": (
                (0, 0, 0),
                [
                    ("lamp", "osm:9", 249.9, 120.0),
                    ("sign", "s1", 230.0, 120.0),
                    ("bench", "b1", 210.0, 110.0),
                ],
                [(240.0, 0.0, 130.0)],
                [(220, 3, 120), (222, 3, 120), (220, 3, 122)],
            ),
            "t0_1_0": (
                (250, 0, 0),
                [("lamp", "osm:9", -0.1, 120.0), ("bin", "k1", 10.0, 115.0)],
                [],
                None,
            ),
        }
        for tile, (origin, props, bollards, paint) in specs.items():
            d = root / tile
            d.mkdir()
            (d / "vis.glb").write_bytes(prop_tile(origin, props, bollards, paint))
            nodes = [0, 120, 260, 120] if tile == "t0_0_0" else [250, 120, 300, 120]
            (d / "roads.json").write_text(
                json.dumps({"nodes": nodes, "edges": [[0, 1, rs.CAR, 1]]})
            )
            (d / "manifest.json").write_text(
                json.dumps(
                    {
                        "coarse": True,
                        "tile": tile,
                        "generation": "g",
                        "visual": {tile: {"url": "vis.glb"}},
                        "roads": {"url": "roads.json"},
                        "vehicles": [
                            {
                                "object_id": f"car-{tile}",
                                "x": 245.0 if tile == "t0_0_0" else 255.0,
                                "y": 0,
                                "z": 125.0,
                                "model": "van",
                            }
                        ],
                    }
                )
            )

    def test_scorecard_on_a_local_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.tiles(root)
            source = rs.Source(tiles=root)
            t0 = source.load("t0_0_0")
            kinds = sorted((i.category, i.kind) for i in t0.items)
            self.assertIn(("furniture", "bollard"), kinds)
            self.assertIn(("sign_pole", "sign"), kinds)
            lamp = next(i for i in t0.items if i.category == "lamp")
            self.assertAlmostEqual(
                lamp.x, 249.9, places=4
            )  # the client's rangeBase: mean of the lowest vertices
            cam = {
                "id": "seam",
                "pos": [250.0, 1.7, 120.0],
                "street": {
                    "name": "s",
                    "polyline": [[200, 120], [300, 120]],
                    "half_width_m": 12,
                    "kerb_parking": True,
                },
            }
            targets = {"per_100m": {"lamps": {"seam": 2}, "parked_cars": {"seam": [1, 3]}}}
            r = rs.score_camera(
                cam,
                [t0, source.load("t0_1_0")],
                targets,
                {"moving_cars": [[251, 121], [400, 0]], "walkers": [[260, 125]]},
            )
            self.assertEqual(r["radius"]["lamps"], 1)  # one lamp although both tiles carry it
            self.assertEqual(r["radius"]["sign_poles"], 1)
            self.assertEqual(r["radius"]["furniture_by_kind"], {"bench": 1, "bin": 1, "bollard": 1})
            self.assertEqual(r["radius"]["parked_cars"], 2)
            self.assertEqual((r["radius"]["moving_cars"], r["radius"]["walkers"]), (1, 1))
            self.assertAlmostEqual(r["radius"]["road_car_m"], 100.0, places=1)
            self.assertAlmostEqual(r["radius"]["paint_m2"], 2.0, places=3)
            per = r["per_100m"]["per_100m"]
            self.assertEqual(per["lamps"], 1.0)
            self.assertEqual(per["parked_cars"], 2.0)
            self.assertEqual(r["targets"]["per_100m.lamps"]["status"], "below")
            self.assertEqual(r["targets"]["per_100m.parked_cars"]["status"], "ok")

    def test_parked_target_needs_kerb_parking(self):
        cam = {
            "id": "x",
            "pos": [0, 0, 0],
            "street": {"polyline": [[0, 0], [100, 0]], "half_width_m": 5},
        }
        result = {"radius": {}, "per_100m": {"kerb_parking": False, "per_100m": {"parked_cars": 0}}}
        out = rs.compare(cam, result, {"per_100m": {"parked_cars": {"x": [8, 12]}}})
        self.assertTrue(out["per_100m.parked_cars"]["status"].startswith("n/a"))

    def test_external_buffer_is_resolved(self):
        pos = np.asarray([(0, 0, 0), (1, 0, 0), (0, 0, 1)], np.float32).tobytes()
        js = {
            "buffers": [{"byteLength": len(pos), "uri": "../shared/x.bin"}],
            "bufferViews": [{"buffer": 0, "byteLength": len(pos)}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
        }
        g = rs.Glb(glb(js), resolve=lambda uri: pos if uri == "../shared/x.bin" else None)
        self.assertEqual(g.accessor(0).tolist(), [[0, 0, 0], [1, 0, 0], [0, 0, 1]])
        with self.assertRaises(ValueError):
            rs.Glb(glb(js)).accessor(0)

    def test_ground_height_under_a_point(self):
        t = rs.TileData("t0_0_0")
        t.ground = [np.array([[[0, 2, 0], [10, 2, 0], [0, 12, 10]]], float)]
        self.assertAlmostEqual(rs.ground_height([t], 2, 5), 7.0)
        self.assertIsNone(rs.ground_height([t], 20, 20))

    def test_quaternion_rotation(self):
        m = rs.local_matrix(
            {
                "rotation": [0, math.sin(math.pi / 4), 0, math.cos(math.pi / 4)],
                "translation": [1, 0, 0],
            }
        )
        p = rs.transform(m, [(1, 0, 0)])[0]
        self.assertTrue(np.allclose(p, (1, 0, -1)))


if __name__ == "__main__":
    unittest.main()
