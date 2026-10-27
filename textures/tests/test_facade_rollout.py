import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageFilter
from turkutex import building_jobs as jobs
from turkutex import facade_rollout as rollout
from turkutex.streetview import refs as refs_module
from turkutex.streetview.facades import Facade, save_facades


def facade_texture(size=(384, 192)) -> Image.Image:
    """Synthetic wall: plaster noise with a grid of dark windows."""
    rng = np.random.default_rng(1)
    pixels = 170 + rng.normal(0, 6, (size[1], size[0], 1)).repeat(3, -1)
    for x in range(20, size[0] - 30, 48):
        for y in range(20, size[1] - 40, 64):
            pixels[y : y + 36, x : x + 22] = 40
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))


def motion_blurred(image: Image.Image) -> Image.Image:
    pixels = np.asarray(image, dtype=np.float32)
    blurred = sum(np.roll(pixels, (k // 2, k), axis=(0, 1)) for k in range(-20, 21)) / 41
    return Image.fromarray(blurred.astype(np.uint8)).filter(ImageFilter.GaussianBlur(2))


def screen(image: Image.Image) -> dict:
    return refs_module.screen_pixels(np.asarray(image))


def test_screen_rejects_black_and_blurred_views_and_keeps_sharp_ones():
    sharp = facade_texture()
    assert refs_module.screen_problem(screen(sharp)) is None
    assert "blurred" in refs_module.screen_problem(screen(motion_blurred(sharp)))
    uncovered = sharp.copy()
    uncovered.paste((0, 0, 0), (0, 0, 384, 60))
    assert "pure black" in refs_module.screen_problem(screen(uncovered))


def refs_dir(tmp_path, views):
    """One wall whose views are (source, score, image) triples."""
    wall = Facade(
        "wall",
        np.array([0.0, 0.0]),
        np.array([12.0, 0.0]),
        6.0,
        props={"building_id": "b", "facade_index": 0, "street_facing": True},
    )
    path = tmp_path / "facades.geojson"
    save_facades([wall], path)
    d = tmp_path / "refs" / "wall"
    d.mkdir(parents=True)
    ids = []
    for n, (source, score, image) in enumerate(views):
        vid = f"wall_v{n}_{source}"
        files = {k: f"{vid}_{k}.jpg" for k in ("crop", "outline", "rectified")}
        for name in files.values():
            image.save(d / name, quality=95)
        (d / f"{vid}.json").write_text(
            json.dumps(
                {"view_id": vid, "files": files, "part": "full", "score": score, "source": source}
            )
        )
        ids.append(vid)
    (d / "refs.json").write_text(json.dumps({"views": ids, "facade": wall.to_feature()}))
    return wall, path, tmp_path / "refs"


def test_valid_look_around_view_leads_even_when_google_scores_higher(tmp_path):
    sharp = facade_texture()
    wall, path, refs = refs_dir(tmp_path, [("google", 0.9, sharp), ("lookaround", 0.5, sharp)])
    views, reason = jobs.screened_full_views(wall, refs)
    assert reason is None
    assert [v["source"] for v in views] == ["lookaround", "google"]
    job = jobs.generate_jobs(path, refs)[0]
    assert job["meta"]["views_used"][0] == "wall_v1_lookaround"
    assert job["images"][-1].endswith("wall_v1_lookaround_outline.jpg")


def test_blurred_look_around_falls_back_to_google_then_mapillary(tmp_path):
    sharp, blurred = facade_texture(), motion_blurred(facade_texture())
    black = Image.new("RGB", (384, 192))
    wall, _, refs = refs_dir(
        tmp_path,
        [("lookaround", 0.9, blurred), ("google", 0.8, black), ("mapillary", 0.2, sharp)],
    )
    views, reason = jobs.screened_full_views(wall, refs)
    assert [v["source"] for v in views] == ["mapillary"]


def test_wall_without_valid_view_is_skipped_with_reason(tmp_path):
    wall, path, refs = refs_dir(tmp_path, [("lookaround", 0.9, motion_blurred(facade_texture()))])
    views, reason = jobs.screened_full_views(wall, refs)
    assert views == [] and "blurred" in reason
    assert jobs.generate_jobs(path, refs) == []


def test_reference_stage_renders_fallback_views_in_source_order(tmp_path, monkeypatch):
    wall = Facade("wall", np.array([0.0, 0.0]), np.array([12.0, 0.0]), 6.0)
    metas = [
        SimpleNamespace(id=f"{source}{n}", source=source, date="2026", ground_h=None)
        for source, n in (("google", 1), ("lookaround", 1), ("lookaround", 2), ("mapillary", 1))
    ]
    scores = {"google1": 0.9, "lookaround1": 0.95, "lookaround2": 0.4, "mapillary1": 0.3}
    good = {"google1"}
    rendered = []

    def evaluate(cache, f, m, base, params):
        return [
            {
                "meta": m,
                "part": "full",
                "score": scores[m.id],
                "section_m": [0, 12],
                "distance_m": 10,
                "view_angle_deg": 0,
                "signed_angle_deg": 0,
                "occluded_frac": None,
            }
        ]

    def render(cache, e, out, view_id, params):
        rendered.append(e["meta"].id)
        ok = e["meta"].id in good
        return {
            "view_id": view_id,
            "source": e["meta"].source,
            "pano_id": e["meta"].id,
            "part": "full",
            "screen": {"black_fraction": 0.0 if ok else 0.9, "sharpness": 1.0},
        }

    monkeypatch.setattr(refs_module, "candidates", lambda *args: metas)
    monkeypatch.setattr(refs_module, "evaluate", evaluate)
    monkeypatch.setattr(refs_module, "render_view", render)
    params = refs_module.SelectParams(max_views=1)
    summary = refs_module.facade_refs(None, wall, tmp_path, params, ("lookaround", "google"))
    # The best-scored Look Around view is black: the other Look Around view is tried before
    # Google, and Mapillary is never rendered once Google passes.
    assert rendered == ["lookaround1", "lookaround2", "google1"]
    assert summary["fallback"]["needed"] and summary["fallback"]["found"].endswith("google1")


def inventory_feature(building, index, start, end, height=10.0, street=True):
    return {
        "type": "Feature",
        "id": f"{building}/facade/{index}",
        "geometry": {"type": "LineString", "coordinates": [start, end]},
        "properties": {
            "building_id": building,
            "facade_index": index,
            "base_height": 2.0,
            "top_height": 2.0 + height,
            "normal": [0.0, 0.0, -1.0],
            "street_facing": street,
        },
    }


def test_queue_ranks_core_walls_then_rings_and_excludes_generated_maps(tmp_path):
    core = rollout.config.OFFICE_BLOCK.core.to_en()
    e, n = (core.emin + core.emax) / 2, (core.nmin + core.nmax) / 2
    features = [
        inventory_feature("near", 0, [e, n], [e + 6, n]),  # core, 6 m
        inventory_feature("near", 1, [e + 30, n], [e + 32, n], height=3.0),  # core, 2 m
        inventory_feature("near", 2, [e - 40, n], [e - 30, n]),  # core, generated already
        inventory_feature("far", 0, [core.emax + 70, n], [core.emax + 80, n]),  # ring 2
        inventory_feature("far", 1, [core.emax + 10, n], [core.emax + 20, n]),  # ring 1
        inventory_feature("far", 2, [core.emax + 10, n + 9], [core.emax + 11, n + 9]),  # 1:10
        inventory_feature("far", 3, [core.emax + 5, n], [core.emax + 25, n], street=False),
    ]
    inventory = tmp_path / "inventory.geojson"
    inventory.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    assembly = tmp_path / "assembly"
    (assembly / "facades/near").mkdir(parents=True)
    (assembly / "facades/near/2.json").write_text(
        json.dumps({"building_id": "near", "facade_index": 2, "generated_qa": {}})
    )
    (assembly / "facades/near/0.json").write_text(
        json.dumps({"building_id": "near", "facade_index": 0})  # photo-derived map
    )
    queue = rollout.build_queue(inventory, assembly, (e, n))
    walls = queue["walls"]
    assert [w["facade_id"] for w in walls] == ["near_0", "near_1", "far_1", "far_2", "far_0"]
    assert [w["group"] for w in walls] == ["core_ge4", "core_rest", "ring_1", "ring_1", "ring_2"]
    assert walls[3]["deferred"].startswith("extreme aspect")
    assert queue["excluded_generated"] == ["near_2"]
    assert rollout.take_batch(queue, 1, 2) == ["near_0", "near_1"]
    assert rollout.take_batch(queue, 2, 2) == ["far_1", "far_0"]  # deferred wall skipped
    assert rollout.take_batch(queue, 1, 5) == ["near_0", "near_1"]  # recorded batches are stable
    # Named walls (a style source per building) may repeat earlier walls, even deferred ones.
    assert rollout.take_batch(queue, 3, 25, ["far_2", "near_2"]) == ["far_2", "near_2"]
    assert rollout.take_batch(queue, 3, 25, ["near_0"]) == ["far_2", "near_2"]
    with pytest.raises(ValueError, match="not street-facing walls"):
        rollout.take_batch(queue, 4, 25, ["nowhere_9"])
    rollout.subset(inventory, ["far_1", "near_0"], tmp_path / "batch.geojson")
    batch = json.loads((tmp_path / "batch.geojson").read_text())["features"]
    assert [f["properties"]["building_id"] for f in batch] == ["far", "near"]


def grid_answer(fid, visible=True):
    return {
        "id": f"{fid}.refgrid",
        "ok": True,
        "answer": {
            "facade_id": fid,
            "wall_visible": visible,
            "problem": "" if visible else "pavement only",
            "storeys": 4,
            "window_columns": 7,
            "ground_openings": 5,
            "plinth": True,
            "cornice": False,
            "cladding": "pink granite",
        },
    }


def test_reference_grid_gates_generation_and_becomes_a_hard_constraint(tmp_path):
    sharp = facade_texture()
    wall, path, refs = refs_dir(tmp_path, [("lookaround", 0.9, sharp), ("google", 0.8, sharp)])
    grid_jobs = jobs.reference_grid_jobs(path, refs)
    assert [j["id"] for j in grid_jobs] == ["wall.refgrid"]
    assert grid_jobs[0]["meta"]["kind"] == "reference_grid"
    assert grid_jobs[0]["images"][-1].endswith("wall_v0_lookaround_outline.jpg")
    grids = tmp_path / "refgrid.jsonl"
    grids.write_text(json.dumps(grid_answer("wall")) + "\n")
    prompt = jobs.generate_jobs(path, refs, grids_out=grids)[0]["vars"]["grid_instruction"]
    assert "4 storeys" in prompt and "exactly 7 window openings" in prompt
    assert "5 ground-floor openings" in prompt and "no distinct cornice" in prompt
    grids.write_text(json.dumps(grid_answer("wall", visible=False)) + "\n")
    assert jobs.generate_jobs(path, refs, grids_out=grids) == []
    views, grid, reason = jobs.reference_status(wall, refs, jobs.latest(grids))
    assert views == [] and reason == "reference does not show the wall: pavement only"
