"""Small synthetic fixtures for per-tile layer and collision selection."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_layers


class LiveLayerTests(unittest.TestCase):
    def test_changed_collision_tiles_are_selected_individually(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "prior/tiles/collision/t0_0_0"
            new = root / "work/dist/tiles/collision/t0_0_0"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            for name in ("c_0_0.col", "c_0_1.col"):
                (old / name).write_bytes(b"same")
                (new / name).write_bytes(b"same")
            (new / "c_0_1.col").write_bytes(b"changed")
            self.assertEqual(
                live_layers.affected_collision_files(root / "prior", root / "work", ("t0_0_0",)),
                (new / "c_0_1.col",),
            )
            (new / "c_0_1.col").write_bytes(b"same")
            self.assertEqual(
                len(
                    live_layers.affected_collision_files(root / "prior", root / "work", ("t0_0_0",))
                ),
                2,
            )

    def test_layer_delta_maps_collision_and_visual_to_only_affected_tiles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "release/layers/ground"
            new = root / "work/build/layers/ground"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            for directory in (old, new):
                (directory / "t0_0_0.vis.glb").write_bytes(b"same")
                (directory / "t0_1_0.vis.glb").write_bytes(b"same")
                (directory / "c_4_1.col.npz").write_bytes(b"same")
            (new / "t0_1_0.vis.glb").write_bytes(b"changed")
            (new / "c_4_1.col.npz").write_bytes(b"changed")
            core = live_layers.C.CORE_TILES
            self.assertEqual(
                live_layers.affected_tiles(root / "release", root / "work", core), ("t0_1_0",)
            )
            (new / "unmapped.bin").write_bytes(b"unknown")
            self.assertEqual(
                live_layers.affected_tiles(root / "release", root / "work", core), core
            )

    def test_per_tile_records_map_to_their_tile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "release/layers/props"
            new = root / "work/build/layers/props"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            for directory in (old, new):
                (directory / "t0_0_0.records.json").write_text("same")
                (directory / "instances.json").write_text("[]")
            (new / "t0_0_1.records.json").write_text("changed")
            (new / "instances.json").write_text("[1]")
            core = live_layers.C.CORE_TILES
            self.assertEqual(
                live_layers.affected_tiles(root / "release", root / "work", core), ("t0_0_1",)
            )

    def test_five_listed_tiles_select_new_and_changed_tiles_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "release/layers/ground"
            new = root / "work/build/layers/ground"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            listed = (*live_layers.C.CORE_TILES, "t0_2_0")
            for directory in (old, new):
                for tile in live_layers.C.CORE_TILES:
                    (directory / f"{tile}.vis.glb").write_bytes(b"same")
            # The new tile's ground and a margin collider east of it (unlisted t0_3_0).
            (new / "t0_2_0.vis.glb").write_bytes(b"new")
            (new / "c_12_0.col.npz").write_bytes(b"margin")
            (new / "c_7_7.col.npz").write_bytes(b"seam")
            (new / "PLAYABLE_TILES.json").write_text(json.dumps({"tiles": list(listed)}))
            selected = live_layers.affected_tiles(root / "release", root / "work", listed)
            # The new tile, its west neighbour's seam row, and t0_1_1 for its changed collider.
            self.assertEqual(selected, ("t0_1_0", "t0_1_1", "t0_2_0"))
            # Recorded in a release that already listed it, an unchanged new tile is not rebuilt.
            (new / "c_7_7.col.npz").unlink()
            (new / "t0_2_0.vis.glb").unlink()
            (old.parents[1] / "live-build.json").write_text(json.dumps({"tiles": list(listed)}))
            self.assertEqual(
                live_layers.affected_tiles(root / "release", root / "work", listed), ()
            )
