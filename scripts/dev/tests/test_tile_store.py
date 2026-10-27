"""Tile store keys, release and per-tile entries (tile_store.py, tile_compose.py) on a throwaway git repository
and data root."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tile_compose  # noqa: E402
import tile_store  # noqa: E402

CORE = ["t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1"]


def listed(tiles):
    return json.dumps(
        {"level": 0, "tiles": tiles, "core_bbox_en": [23459500.0, 6704000.0, 23460000.0, 6704500.0]}
    )


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


class TileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo, self.data = root / "repo", root / "data"
        for path in (self.repo / "pipeline", self.repo / "game", self.data):
            path.mkdir(parents=True)
        git(self.repo, "init", "-q", "-b", "main")
        (self.data / "playable-tiles.json").write_text(listed(CORE))

    def tearDown(self):
        self.temp.cleanup()

    def commit(self, name, text):
        (self.repo / name).write_text(text)
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name)
        return git(self.repo, "rev-parse", "HEAD")

    def test_key_follows_tile_inputs_and_data_but_not_game_code(self):
        first = self.commit("pipeline/a.py", "a = 1\n")
        game = self.commit("game/main.ts", "x\n")
        self.assertEqual(
            tile_store.key(self.repo, first, self.data), tile_store.key(self.repo, game, self.data)
        )
        pipeline = self.commit("pipeline/a.py", "a = 2\n")
        self.assertNotEqual(
            tile_store.key(self.repo, game, self.data),
            tile_store.key(self.repo, pipeline, self.data),
        )
        before = tile_store.key(self.repo, pipeline, self.data)
        (self.data / "playable-tiles.json").write_text(listed([*CORE, "t0_2_0"]))
        self.assertNotEqual(before, tile_store.key(self.repo, pipeline, self.data))

    def test_put_keep_and_prune(self):
        commit = self.commit("pipeline/a.py", "a = 1\n")
        tiles = Path(self.temp.name) / "dist/tiles"
        (tiles / "visual").mkdir(parents=True)
        (tiles / "manifest.json").write_text("{}")
        (tiles / "visual/t0_0_0.glb").write_bytes(b"glb")
        full = tile_store.key(self.repo, commit, self.data)
        self.assertIsNone(tile_store.entry(self.data, full))
        seeded = {"time": 5.0, "parts": tile_store.landing_class.data_parts(self.data)}
        entry = tile_store.put(
            self.data, tiles, full, commit, "integrator", ["t0_0_0"], seeded=seeded
        )
        self.assertEqual((entry / "visual/t0_0_0.glb").read_bytes(), b"glb")
        data = tile_store._data(self.data)
        self.assertEqual(
            tile_store.base_release(self.repo, commit, self.data, data)["dir"], str(entry)
        )
        self.assertEqual(
            tile_store.landing_class.data_delta(seeded["parts"], tile_store.base_parts(entry)), []
        )
        self.assertEqual(tile_store.base_time(entry), 5.0)
        record = tile_store.entry(self.data, full)
        self.assertEqual(
            (record["commit"], record["source"], record["tiles"]),
            (commit, "integrator", ["t0_0_0"]),
        )
        # An existing entry is immutable: a second put keeps the first bytes.
        (tiles / "visual/t0_0_0.glb").write_bytes(b"changed")
        tile_store.put(self.data, tiles, full, commit, "integrator")
        self.assertEqual((entry / "visual/t0_0_0.glb").read_bytes(), b"glb")
        index = (self.data / "tile-store/index.jsonl").read_text().splitlines()
        self.assertEqual(len(index), 1)
        for n in range(3):
            tile_store.put(self.data, tiles, f"k{n}", commit, "integrator")
        (self.data / "tile-store/tiles/t0_0_0").mkdir(parents=True)
        tile_store.prune(self.data, keep=2)
        kept = sorted(p.name for p in (self.data / "tile-store").iterdir() if p.is_dir())
        self.assertEqual(kept, ["k1", "k2", "tiles"])

    def test_prune_keeps_the_newest_entry_of_every_listed_tile(self):
        commit = self.commit("pipeline/a.py", "a = 1\n")
        (self.data / "playable-tiles.json").write_text(listed([*CORE, "t0_2_0"]))
        tiles = Path(self.temp.name) / "dist/tiles"
        tiles.mkdir(parents=True)

        def build(key, ids):
            visual = {t: {"level": 0} for t in ids} | {"t1_0_0": {"level": 1}}
            (tiles / "manifest.json").write_text(json.dumps({"visual": visual}))
            entry = tile_store.put(self.data, tiles, key, commit, "integrator")
            os.utime(entry, (len(key), len(key)))  # mtime orders entries: k1 < k22 < k333 ...

        build("k1", [*CORE, "t0_2_0"])  # the only build that carries t0_2_0, then three without it
        build("k22", CORE)
        build("k333", CORE)
        build("k4444", CORE)
        tile_store.prune(self.data, keep=2)
        kept = sorted(p.name for p in (self.data / "tile-store").iterdir() if p.is_dir())
        self.assertEqual(kept, ["k1", "k333", "k4444"])

    def test_a_release_key_follows_the_data_it_was_built_from(self):
        commit = self.commit("pipeline/a.py", "a = 1\n")
        release = self.data / "live-releases/r"
        for root in (self.data / "facade-approvals/assembly", release / "textures/assembly"):
            root.mkdir(parents=True)
            (root / "MANIFEST.json").write_text('{"bound": []}')
        (release / "layers").mkdir()
        (release / "tiles").mkdir()
        (self.data / "build").mkdir()
        (self.data / "build/layers").symlink_to(release / "layers")
        (release / "live-build.json").write_text(json.dumps({"commit": commit, "tiles": CORE}))
        self.assertEqual(
            tile_store.key(self.repo, commit, self.data, release),
            tile_store.key(self.repo, commit, self.data),
        )
        (self.data / "live").symlink_to(release)
        before = tile_store.base_release(self.repo, commit, self.data, tile_store._data(self.data))
        self.assertEqual(before["key"], tile_store.key(self.repo, commit, self.data))
        # New facade approvals in the data root: the release no longer matches the commit's key.
        (self.data / "facade-approvals/assembly/MANIFEST.json").write_text('{"bound": ["b_0"]}')
        self.assertNotEqual(
            tile_store.key(self.repo, commit, self.data, release),
            tile_store.key(self.repo, commit, self.data),
        )

        after = tile_store.base_release(self.repo, commit, self.data, tile_store._data(self.data))
        self.assertNotEqual(after["key"], tile_store.key(self.repo, commit, self.data))

    def test_put_without_a_manifest_stores_nothing(self):
        commit = self.commit("pipeline/a.py", "a = 1\n")
        empty = Path(self.temp.name) / "empty"
        empty.mkdir()
        self.assertIsNone(tile_store.put(self.data, empty, "k", commit, "integrator"))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_dist(root, visual, collision, code="abc1234", vehicles=()):
    """A synthetic tile dist: visual {id: (bytes, coarse)}, collision {cell: bytes}; manifest and tileset as the
    assembler writes them (DistTree.commit), and the assembler cache keyed by content."""
    root = Path(root)
    entries, cells = {}, {}
    for tid, (data, coarse) in visual.items():
        level = int(tid[1])
        url = f"visual/{level}/{tid}.glb"
        (root / url).parent.mkdir(parents=True, exist_ok=True)
        (root / url).write_bytes(data)
        entries[tid] = {
            "url": url,
            "sha256": sha(data),
            "bytes": len(data),
            "level": level,
            "tris": 1,
            "h": [0.0, 1.0],
        }
        if coarse:
            entries[tid]["coarse_dtm"] = False
    for cid, data in collision.items():
        tile = tile_compose.C.CollisionTile.parse(cid).visual_parent().id
        url = f"collision/{tile}/{cid}.col"
        (root / url).parent.mkdir(parents=True, exist_ok=True)
        (root / url).write_bytes(data)
        cells[cid] = {
            "url": url,
            "sha256": sha(data),
            "bytes": len(data),
            "tris": 1,
            "prims": 0,
            "h": [0, 1],
            "flags": 0,
        }
    tileset = tile_compose._dumps(tile_compose.build_tileset(entries, f"turku3d {code}"))
    (root / "tileset.json").write_bytes(tileset)
    manifest = {
        "format": "turku3d-manifest",
        "version": 1,
        "code": code,
        "vehicles": list(vehicles),
        "vehicle_models": {},
        "tileset": {"url": "tileset.json", "sha256": sha(tileset), "bytes": len(tileset)},
        "visual": dict(sorted(entries.items())),
        "collision": dict(sorted(cells.items())),
    }
    (root / "manifest.json").write_bytes(tile_compose._dumps(manifest))
    keys = {tid: "k" + e["sha256"] for tid, e in entries.items()}
    keys |= {f"collision:{cid}": "k" + e["sha256"] for cid, e in cells.items()}
    (root / ".assemble-cache.json").write_text(json.dumps(keys))
    return manifest


# The office tile t0_0_0 with its parents, a coarse leaf beside it, an unrelated tile, its cells, a ring cell and a
# far cell; the release has the old bytes, the builder the new ones for t0_0_0, its cells and its parents.
def release_tiles(new=False):
    tag = b"new" if new else b"old"
    visual = {
        "t0_0_0": (b"office " + tag, False),
        "t0_0_1": (b"north", False),
        "t0_1_1": (b"coarse leaf", True),
        "t0_4_4": (b"far", False),
        "t1_0_0": (b"parent " + tag, False),
        "t2_0_0": (b"grandparent " + tag, False),
    }
    collision = {f"c_{i}_{j}": b"cell " + tag for i in range(4) for j in range(4)}
    collision |= {"c_-1_0": b"ring", "c_9_9": b"far cell"}
    return visual, collision


class TileFragmentScopeTests(unittest.TestCase):
    def test_tile_only_keeps_served_world_metadata_and_updates_visuals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            built = write_dist(root / "built", *release_tiles(new=True))
            base = write_dist(root / "base", *release_tiles())
            for name in ("roads", "addresses"):
                source = root / "built" / name / "older.json"
                source.parent.mkdir()
                source.write_text("{}")
                built[name] = {"url": f"{name}/older.json", "sha256": sha(b"{}")}
                base[name] = {"url": f"{name}/complete.json", "sha256": "newer"}
            (root / "built/manifest.json").write_text(json.dumps(built))
            full, full_files = tile_compose.fragment(root / "built", "t0_0_0")
            scoped, files = tile_compose.fragment(root / "built", "t0_0_0", tile_only=True)
            merged, _ = tile_compose.compose_manifest(base, [{"fragment": scoped}])
            for name in ("roads", "addresses"):
                self.assertEqual(full[name], built[name])
                self.assertIn(built[name]["url"], full_files)
                self.assertNotIn(built[name]["url"], files)
                self.assertEqual(merged[name], base[name])
            self.assertEqual(merged["visual"]["t0_0_0"], built["visual"]["t0_0_0"])
            self.assertNotEqual(tile_compose.tile_key(full), tile_compose.tile_key(scoped))


class PerTileTests(unittest.TestCase):
    """put-tile, composition precedence (release, per-tile entry, newer release) and the no-double-swap rule."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo, self.data = root / "repo", root / "data"
        for path in (self.repo / "pipeline", self.data):
            path.mkdir(parents=True)
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "pipeline/a.py").write_text("a = 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "a")
        self.head = git(self.repo, "rev-parse", "HEAD")
        (self.data / "playable-tiles.json").write_text(listed(CORE))
        self.release = write_dist(root / "release", *release_tiles())
        self.built = root / "builder/dist/tiles"
        write_dist(self.built, *release_tiles(new=True))
        (self.built / "ground/t0_0_0/abc").mkdir(parents=True)
        (self.built / "ground/t0_0_0/abc/material.json").write_text("{}")
        self.base = tile_store.put(
            self.data,
            root / "release",
            "base",
            self.head,
            "integrator",
            None,
            seeded={"time": 100.0, "parts": None},
        )

    def tearDown(self):
        self.temp.cleanup()

    def put(self, tile="t0_0_0", **kw):
        origin = kw.pop("origin", None) or tile_compose.provenance(self.repo, self.data)
        return tile_compose.put_tile(
            self.data,
            self.built,
            tile,
            source="tile-add",
            task="t",
            listed=CORE,
            origin=origin,
            **kw,
        )

    def compose(self, base=None):
        base = base or self.base
        return tile_compose.compose(self.repo, self.head, self.data, base, base.name)

    def test_put_tile_carries_the_tile_parents_coarse_leaves_ring_and_provenance(self):
        entry = self.put()
        record = json.loads((entry / "TILE.json").read_text())
        frag = record["fragment"]
        self.assertEqual(sorted(frag["visual"]), ["t0_0_0", "t0_1_1", "t1_0_0", "t2_0_0"])
        self.assertEqual(
            len(frag["collision"]), 17
        )  # its 16 cells and the ring cell, not the far one
        self.assertEqual((entry / "visual/0/t0_0_0.glb").read_bytes(), b"office new")
        self.assertTrue((entry / "ground/t0_0_0/abc/material.json").is_file())
        self.assertFalse((entry / "visual/0/t0_4_4.glb").exists())
        self.assertEqual(
            (record["commit"], record["mainline"], record["task"]), (self.head, self.head, "t")
        )
        self.assertEqual(self.put(), entry)  # same input keys: the entry is kept
        self.assertEqual(
            len((self.data / "tile-store/tiles/index.jsonl").read_text().splitlines()), 1
        )
        with self.assertRaises(ValueError):
            self.put("t0_1_1")  # a coarse leaf is not a finished tile

    def test_put_tiles_offers_only_tiles_whose_bytes_differ_from_the_release(self):
        put = tile_compose.put_tiles(
            self.data,
            self.built,
            ["t0_0_0", "t0_0_1", "t0_1_1"],
            source="tile-build",
            task="t",
            listed=CORE,
            repo=self.repo,
            against=self.base,
        )
        self.assertEqual([Path(p).parent.name for p in put], ["t0_0_0"])

    def test_road_only_change_gets_a_new_tile_entry(self):
        first = self.put()
        road = self.built / "roads/updated.json"
        road.parent.mkdir(parents=True, exist_ok=True)
        road.write_bytes(b"new road graph")
        manifest_path = self.built / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["roads"] = {
            "url": "roads/updated.json",
            "sha256": sha(road.read_bytes()),
            "bytes": road.stat().st_size,
        }
        manifest_path.write_bytes(tile_compose._dumps(manifest))
        second = self.put()
        self.assertNotEqual(first, second)
        self.assertEqual((second / "roads/updated.json").read_bytes(), b"new road graph")
        self.assertFalse(
            tile_compose.same_bytes(
                json.loads((second / "TILE.json").read_text())["fragment"],
                json.loads((self.base / "manifest.json").read_text()),
            )
        )

    def test_compose_lays_a_newer_entry_over_the_release(self):
        self.put()
        result = self.compose()
        self.assertEqual((result["source"], result["tiles"]), ("composed", ["t0_0_0"]))
        served = Path(result["dir"])
        manifest = json.loads((served / "manifest.json").read_text())
        self.assertEqual(manifest["visual"]["t0_0_0"]["sha256"], sha(b"office new"))
        self.assertEqual(manifest["visual"]["t0_4_4"]["sha256"], sha(b"far"))
        self.assertEqual((served / "visual/0/t0_0_0.glb").read_bytes(), b"office new")
        self.assertEqual((served / "collision/t0_0_0/c_1_1.col").read_bytes(), b"cell new")
        self.assertEqual(manifest["tileset"]["sha256"], sha((served / "tileset.json").read_bytes()))
        self.assertEqual(
            self.compose()["dir"], result["dir"]
        )  # same base and entries: the same entry

    def test_compose_leaves_out_unlisted_superseded_absorbed_and_foreign_code_entries(self):
        self.put("t0_4_4")
        self.assertEqual(self.compose()["skipped"]["t0_4_4"], "not listed")
        entry = self.put()
        newer = Path(self.temp.name) / "newer"
        write_dist(newer, *release_tiles())
        later = json.loads((entry / "TILE.json").read_text())["time"] + 1
        superseding = tile_store.put(
            self.data,
            newer,
            "newer",
            self.head,
            "integrator",
            None,
            seeded={"time": later, "parts": None},
        )
        result = self.compose(superseding)
        self.assertEqual(
            (result["dir"], result["skipped"]["t0_0_0"]), (str(superseding), "superseded")
        )
        shutil.rmtree(entry)
        foreign = {"commit": "x", "code": "edited", "mainline": None}
        self.put(origin=foreign)
        self.assertEqual(
            self.compose()["skipped"]["t0_0_0"], "no entry built with main's tile code"
        )

    def test_served_base_is_main_s_build_else_the_newest_build_of_its_history(self):
        root = Path(self.temp.name)
        live = root / "data/live-releases/L"
        shutil.copytree(root / "release", live / "tiles")
        (live / "live-build.json").write_text(json.dumps({"commit": self.head, "data_time": 10.0}))
        (self.data / "live").symlink_to(live)
        found = tile_store.served(self.repo, self.head, self.data)
        self.assertEqual((found["base"]["source"], found["served"]["source"]), ("live", None))
        # Main moves on with a tile code change: the integrator's newer build of its parent beats live; a build of
        # a commit outside main's history never counts.
        git(self.repo, "checkout", "-q", "-b", "side")
        (self.repo / "pipeline/a.py").write_text("a = 3\n")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "side")
        side = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", "main")
        (self.repo / "pipeline/a.py").write_text("a = 2\n")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "b")
        head = git(self.repo, "rev-parse", "HEAD")
        built = tile_store.put(
            self.data,
            root / "release",
            "built",
            self.head,
            "integrator",
            None,
            seeded={"time": 20.0, "parts": None},
        )
        tile_store.put(
            self.data,
            root / "release",
            "other",
            side,
            "integrator",
            None,
            seeded={"time": 30.0, "parts": None},
        )
        self.base.rename(
            self.base.with_name("gone")
        )  # the setUp entry is not a build of this history's data
        self.assertEqual(tile_store.served(self.repo, head, self.data)["base"]["dir"], str(built))
        own = tile_store.put(
            self.data,
            root / "release",
            tile_store.key(self.repo, head, self.data),
            head,
            "integrator",
        )
        self.assertEqual(tile_store.served(self.repo, head, self.data)["base"]["dir"], str(own))

    def test_the_converged_release_is_served_without_a_second_swap(self):
        self.put()
        composed = json.loads((Path(self.compose()["dir"]) / "manifest.json").read_text())
        # The integrator's release of the same inputs: the builder's bytes, assembled the same way.
        converged = tile_store.put(
            self.data,
            self.built,
            "converged",
            self.head,
            "integrator",
            None,
            seeded={"time": 50.0, "parts": None},
        )
        result = self.compose(converged)
        self.assertEqual((result["dir"], result["skipped"]["t0_0_0"]), (str(converged), "absorbed"))
        release = json.loads((converged / "manifest.json").read_text())
        # What the open tab compares (game/src/world/releaseSwap.ts releaseDelta): nothing differs, nothing reloads.
        for kind in ("visual", "collision"):
            self.assertEqual(
                {k: e["sha256"] for k, e in composed[kind].items()},
                {k: e["sha256"] for k, e in release[kind].items()},
            )
        self.assertEqual(composed["tileset"]["sha256"], release["tileset"]["sha256"])


if __name__ == "__main__":
    unittest.main()
