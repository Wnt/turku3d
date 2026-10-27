"""Synthetic GitHub Pages deploys: provenance refusal, orphan force-push, site files, notes, region."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pages  # noqa: E402
import pages_region  # noqa: E402

# The cases patch os.environ: tests/run.py runs them one by one in a process of their own.
SERIAL = True
GENERATED = {"source_mode": "generate", "generated_qa": {"ok": True}, "imagegen_calls": 1}
PHOTO = {"method": "rectified-photo-bay-inpaint-v1", "imagegen_calls": 0}
ENABLE_PAGES = pages.enable_pages


def git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


# Four listed tiles under the 500 m parent t1_0_0 and one (t0_2_0) under t1_1_0; the region centre
# (Läntinen Rantakatu 15) and the spawn lie in t0_0_0.
LISTED = ("t0_0_0", "t0_1_0", "t0_0_1", "t0_1_1", "t0_2_0")
GOOD = {"facade_coverage": 0.5, "ground": "ortho-splats", "validator": {"status": "pass"}}


def put(root, url, data):
    path = root / url
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"url": url, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def tile_tree(tiles, quality=None):
    """A release tile tree: visual, collision (one cell per listed tile and a margin cell), ground,
    parked cars, addresses, tileset and quality ledger."""
    quality = {tile: {**GOOD, **(quality or {}).get(tile, {})} for tile in LISTED}
    visual = {}
    for tid in (*LISTED, "t1_0_0", "t1_1_0", "t2_0_0"):
        level = int(tid[1])
        visual[tid] = {
            **put(tiles, f"visual/{level}/{tid}.glb", tid.encode()),
            "level": level,
            "shared_images": [{"url": f"visual/shared/{tid}.ktx2", "sha256": "x"}],
        }
        put(tiles, f"visual/shared/{tid}.ktx2", b"img")
        if level == 0:
            put(tiles, f"ground/{tid}/p/material.json", b"{}")
    put(tiles, "ground/library/l/albedo.array.ktx2", b"lib")
    cells = {f"c_{4 * int(t.split('_')[1])}_{4 * int(t.split('_')[2])}": t for t in LISTED}
    cells["c_-1_0"] = "t0_-1_0"  # meshing margin beside t0_0_0, outside the listed tiles
    cells["c_-1_7"] = "t0_-1_1"  # a margin cell beside no shipped cell
    collision = {
        cid: put(tiles, f"collision/{parent}/{cid}.col", cid.encode())
        for cid, parent in cells.items()
    }
    pool = {
        "format": "turku3d-addresses",
        "tiles": {"t0_0_0": 1, "t0_1_1": 1},
        "addresses": [{"id": "a", "tile": "t0_0_0"}, {"id": "b", "tile": "t0_1_1"}],
    }
    addresses = put(tiles, "addresses/pool.json", json.dumps(pool).encode())
    model = put(tiles, "vehicles/suv-red.glb", b"car")
    van = put(tiles, "vehicles/van-blue.glb", b"van")

    def node(tid, children=()):
        result = {"extras": {"id": tid}, "content": {"uri": visual[tid]["url"]}}
        return {**result, "children": list(children)} if children else result

    tileset = {
        "asset": {"version": "1.0"},
        "root": {
            "children": [
                node(
                    "t2_0_0",
                    [
                        node("t1_0_0", [node(t) for t in LISTED[:4]]),
                        node("t1_1_0", [node("t0_2_0")]),
                    ],
                )
            ]
        },
    }
    manifest = {
        "visual": visual,
        "collision": collision,
        "tileset": put(tiles, "tileset.json", json.dumps(tileset).encode()),
        "spawn": {"position": [170.0, 3.0, 240.0]},
        "vehicles": [
            {"x": 10.0, "z": 10.0, "model": "suv", "color": "red"},
            {"x": 400.0, "z": 400.0, "model": "van", "color": "blue"},
        ],
        "vehicle_models": {"suv/red": model, "van/blue": van},
        "addresses": {**addresses, "count": 2},
    }
    (tiles / "manifest.json").write_text(json.dumps(manifest))
    (tiles / "quality.json").write_text(json.dumps({"tiles": quality}))
    return manifest


class PagesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        identity = {
            "GIT_AUTHOR_NAME": "Pages Test",
            "GIT_AUTHOR_EMAIL": "pages@example.test",
            "GIT_COMMITTER_NAME": "Pages Test",
            "GIT_COMMITTER_EMAIL": "pages@example.test",
        }
        patcher = patch.dict(os.environ, identity)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.published = self.root / "data"
        (self.published / "publish").mkdir(parents=True)
        (self.published / pages.CONFIG).write_text(
            json.dumps({"repo": "Owner/turku-game", "branch": "gh-pages"})
        )
        self.remote = self.root / "remote.git"
        git("init", "-q", "--bare", str(self.remote), cwd=self.root)
        self.calls = []
        for name, value in (
            ("remote_url", lambda repo: str(self.remote)),
            ("enable_pages", self.enable),
        ):
            patcher = patch.object(pages, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def enable(self, repo, branch):
        self.calls.append((repo, branch))
        return pages.site_url(repo)

    def release(self, commit, facades, **fields):
        release = self.published / "live-releases" / commit
        (release / "game").mkdir(parents=True)
        (release / "game/index.html").write_text(commit)
        tile_tree(release / "tiles")
        assembly = release / "textures/assembly"
        (assembly / "facades/b").mkdir(parents=True)
        for index, meta in enumerate(facades):
            (assembly / f"facades/b/{index}.json").write_text(json.dumps(meta))
        bound = [f"b_{index}" for index in range(len(facades))]
        (assembly / "MANIFEST.json").write_text(json.dumps({"bound": bound}))
        record = {"commit": commit * 4, "facades_bound": len(bound), **fields}
        (release / "live-build.json").write_text(json.dumps(record))
        live = self.published / "live"
        live.unlink(missing_ok=True)
        live.symlink_to(release, target_is_directory=True)
        return release

    def test_publish_force_pushes_one_orphan_commit_with_site_files(self):
        self.release("aaaaaaaaaaaa", [GENERATED, GENERATED])
        first = pages.publish(self.published)
        self.release("bbbbbbbbbbbb", [GENERATED])
        result = pages.publish(self.published)
        self.assertEqual(result["url"], "https://owner.github.io/turku-game/")
        self.assertEqual((result["facades"], result["release"]), (1, "bbbbbbbbbbbb" * 4))
        self.assertNotEqual(first["commit"], result["commit"])
        self.assertEqual(git("rev-list", "--count", "gh-pages", cwd=self.remote), "1")
        files = set(git("ls-tree", "-r", "--name-only", "gh-pages", cwd=self.remote).split())
        site = {".nojekyll", "ATTRIBUTION.md", "README.md", "index.html"}
        self.assertEqual({f for f in files if "/" not in f}, site)
        self.assertIn("game/index.html", files)
        self.assertIn("tiles/manifest.json", files)
        self.assertNotIn("tiles/quality.json", files)
        self.assertEqual(sorted(result["tiles"]), sorted(LISTED))
        body = git("log", "-1", "--format=%b", "gh-pages", cwd=self.remote)
        self.assertIn("Shipped tiles (5):", body)
        readme = git("show", "gh-pages:README.md", cwd=self.remote)
        self.assertIn("https://owner.github.io/turku-game/", readme)
        self.assertNotIn("{{", git("show", "gh-pages:index.html", cwd=self.remote))
        message = git("log", "-1", "--format=%s", "gh-pages", cwd=self.remote)
        self.assertRegex(message, r"^Release bbbbbbbbbbbb \d{4}-\d\d-\d\d \d\d:\d\d UTC$")
        self.assertEqual(self.calls, [("Owner/turku-game", "gh-pages")] * 2)

    def test_derived_maps_from_generated_styles_are_published(self):
        derived = {"source_mode": "derived", "derived_from": "k1", "style": {"path": "style.json"}}
        neighbour = {"source_mode": "derived-neighbour", "derived_from": "k1"}
        self.assertTrue(pages.generated(derived))
        self.assertTrue(pages.generated(neighbour))
        self.assertFalse(pages.generated({"source_mode": "derived"}))
        self.assertFalse(
            pages.generated(
                {
                    "source_mode": "derived",
                    "derived_from": "k1",
                    "method": "rectified-photo-bay-inpaint-v1",
                }
            )
        )
        release = self.release("eeeeeeeeeeee", [GENERATED, derived, neighbour])
        self.assertEqual(pages.check_facades(release), 3)

    def test_photo_derived_facades_are_never_published(self):
        self.release("cccccccccccc", [GENERATED, PHOTO])
        with self.assertRaisesRegex(pages.PhotoFacades, "1 bound facades.*b_1"):
            pages.publish(self.published)
        self.assertEqual(git("branch", "--list", cwd=self.remote), "")
        self.assertEqual(self.calls, [])

    def history(self, count):
        """A source repository with count commits; returns their ids, oldest first."""
        source = self.root / "source"
        source.mkdir(exist_ok=True)
        git("init", "-q", "-b", "main", cwd=source)
        for n in range(count):
            (source / "f.txt").write_text(str(n))
            git("add", "f.txt", cwd=source)
            git("commit", "-q", "-m", f"Change {n}", cwd=source)
        return source, git("rev-list", "--reverse", "HEAD", cwd=source).splitlines()

    def message(self):
        return git("log", "-1", "--format=%B", "gh-pages", cwd=self.remote)

    def test_any_built_release_publishes_with_its_failures_as_release_notes(self):
        source, commits = self.history(4)
        advisory = [
            {
                "stage": "chrome",
                "detail": "chrome failed (1); 2 failed",
                "specs": ["webgpu:game/tests/e2e-tile/save-menu.spec.ts"],
            },
            {"stage": "facade-ground-audit", "detail": "facade-ground-audit failed (1); 30 fail"},
        ]
        # No full Chrome suite ran, and advisory checks failed: it is published anyway.
        release = self.release("x", [GENERATED], advisory=advisory, **{"from": commits[0]})
        record = json.loads((release / "live-build.json").read_text())
        (release / "live-build.json").write_text(json.dumps({**record, "commit": commits[2]}))
        result = pages.publish(self.published, repo=source)
        self.assertEqual(result["release"], commits[2])
        message = self.message()
        self.assertIn(f"Commits {commits[0][:12]}..{commits[2][:12]}:", message)
        self.assertIn(f"  {commits[2][:12]} Change 2", message)
        self.assertIn(f"  {commits[1][:12]} Change 1", message)
        self.assertNotIn("Change 0", message)
        self.assertIn("Known broken (advisory checks that failed; published anyway):", message)
        self.assertIn("  chrome: chrome failed (1); 2 failed", message)
        self.assertIn("    webgpu:game/tests/e2e-tile/save-menu.spec.ts", message)
        self.assertIn("  facade-ground-audit: facade-ground-audit failed (1); 30 fail", message)
        # The next release's notes start where this Pages release ended.
        release = self.release("y", [GENERATED], advisory=[], **{"from": commits[0]})
        record = json.loads((release / "live-build.json").read_text())
        (release / "live-build.json").write_text(json.dumps({**record, "commit": commits[3]}))
        pages.publish(self.published, release, repo=source)
        message = self.message()
        self.assertIn(f"Commits {commits[2][:12]}..{commits[3][:12]}:", message)
        self.assertNotIn("Change 1", message)
        self.assertIn("Advisory checks: all green", message)

    def test_bound_count_beyond_the_manifest_is_refused(self):
        release = self.release("dddddddddddd", [GENERATED])
        record = json.loads((release / "live-build.json").read_text())
        (release / "live-build.json").write_text(json.dumps({**record, "facades_bound": 300}))
        with self.assertRaisesRegex(pages.PhotoFacades, "binds 300 facades"):
            pages.check_facades(release)

    def plan(self, quality=None, **region):
        release = self.root / "release"
        manifest = tile_tree(release / "tiles", quality)
        return manifest, pages_region.plan({"region": region}, release)

    def test_region_ships_close_good_connected_tiles_and_names_the_reason_for_the_rest(self):
        _, result = self.plan(
            {
                "t0_1_1": {"facade_coverage": 0.05},
                "t0_0_1": {"facade_coverage": None},  # no street-facing walls: nothing to cover
            },
            radius_m=450,
        )
        self.assertEqual(result["centre"], "t0_0_0")
        self.assertEqual(result["selected"], ["t0_0_0", "t0_0_1", "t0_1_0"])
        self.assertEqual(
            result["excluded"],
            {
                "t0_1_1": ["facade coverage 5% < 20%"],
                "t0_2_0": ["outside the radius (473 m > 450 m)"],
            },
        )
        _, result = self.plan({"t0_1_0": {"ground": "draft"}})
        self.assertEqual(result["excluded"]["t0_1_0"], ["ground draft"])
        self.assertEqual(
            result["excluded"]["t0_2_0"], ["not connected to the centre tile through shipped tiles"]
        )
        _, result = self.plan(
            {"t0_1_0": {"validator": {"status": "fail"}}, "t0_0_1": {"facades": {"other": 2}}}
        )
        self.assertEqual(result["excluded"]["t0_1_0"], ["validator failed"])
        self.assertEqual(result["excluded"]["t0_0_1"], ["2 facade maps are not generated-style"])

    def test_region_refuses_without_the_centre_or_the_spawn(self):
        with self.assertRaisesRegex(pages_region.Refused, "centre tile t0_0_0.*ground untextured"):
            self.plan({"t0_0_0": {"ground": "untextured"}})
        release = self.root / "release"
        manifest = json.loads((release / "tiles/manifest.json").read_text())
        manifest["spawn"]["position"] = [400.0, 3.0, 400.0]
        (release / "tiles/manifest.json").write_text(json.dumps(manifest))
        tiles = {t: GOOD for t in LISTED}
        tiles["t0_1_1"] = {**GOOD, "facade_coverage": 0}
        with self.assertRaisesRegex(pages_region.Refused, "spawn lies in t0_1_1"):
            pages_region.select(manifest, tiles, pages_region.region({}))
        with self.assertRaisesRegex(ValueError, "draft ground never ships"):
            pages_region.region({"region": {"ground": ["draft"]}})
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            pages_region.region({"region": {"radius": 3}})

    def test_pages_tile_tree_keeps_the_selection_playable_and_siblings_as_scenery(self):
        _, result = self.plan({"t0_1_1": {"facade_coverage": 0.0}}, radius_m=400)
        target = self.root / "site/tiles"
        manifest = pages_region.write_tiles(self.root / "release/tiles", target, result)
        # t0_1_1 shares t1_0_0 with selected tiles: visual-only scenery. t0_2_0's parent t1_1_0 has
        # no selected tile, so the parent stands in and t0_2_0 is dropped.
        self.assertEqual(
            sorted(manifest["visual"]),
            ["t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1", "t1_0_0", "t1_1_0", "t2_0_0"],
        )
        self.assertEqual(sorted(manifest["collision"]), ["c_-1_0", "c_0_0", "c_0_4", "c_4_0"])
        self.assertEqual([car["model"] for car in manifest["vehicles"]], ["suv"])
        self.assertEqual(list(manifest["vehicle_models"]), ["suv/red"])
        self.assertEqual(manifest["pages_region"]["excluded"], ["t0_1_1", "t0_2_0"])
        pool = json.loads((target / manifest["addresses"]["url"]).read_text())
        self.assertEqual(
            ([a["id"] for a in pool["addresses"]], pool["tiles"]), (["a"], {"t0_0_0": 1})
        )
        self.assertEqual(manifest["addresses"]["count"], 1)
        tileset = json.loads((target / "tileset.json").read_text())
        parents = tileset["root"]["children"][0]["children"]
        self.assertEqual(
            [[c["extras"]["id"] for c in p.get("children", [])] for p in parents],
            [["t0_0_0", "t0_1_0", "t0_0_1", "t0_1_1"], []],
        )
        for entry in [
            manifest["tileset"],
            manifest["addresses"],
            *manifest["visual"].values(),
            *manifest["collision"].values(),
            *manifest["vehicle_models"].values(),
        ]:
            data = (target / entry["url"]).read_bytes()
            self.assertEqual(
                (len(data), hashlib.sha256(data).hexdigest()), (entry["bytes"], entry["sha256"])
            )
        self.assertTrue((target / "ground/t0_1_1/p/material.json").is_file())
        self.assertTrue((target / "ground/library/l/albedo.array.ktx2").is_file())
        for gone in ("visual/0/t0_2_0.glb", "ground/t0_2_0", "collision/t0_1_1", "quality.json"):
            self.assertFalse((target / gone).exists(), gone)
        notes = pages_region.notes(result)
        self.assertIn("Shipped tiles (3):\n  t0_0_0: facades 50%, ground ortho-splats", notes)
        self.assertIn("  t0_1_1: facade coverage 0% < 20% (facades 0%, ground ortho-splats", notes)

    def test_enable_pages_tolerates_pages_enabled_by_the_first_push(self):
        site = json.dumps({"html_url": "https://owner.github.io/turku-game/"})
        answers = iter(
            (
                subprocess.CompletedProcess([], 1, "", "Not Found (HTTP 404)"),
                subprocess.CompletedProcess([], 1, "", "already enabled. (HTTP 409)"),
                subprocess.CompletedProcess([], 0, site, ""),
            )
        )
        with patch.object(pages, "gh", side_effect=lambda *args: next(answers)):
            url = ENABLE_PAGES("Owner/turku-game", "gh-pages")
        self.assertEqual(url, "https://owner.github.io/turku-game/")

    def test_no_config_means_no_pages_target(self):
        (self.published / pages.CONFIG).unlink()
        self.assertIsNone(pages.config(self.published))


if __name__ == "__main__":
    unittest.main()
