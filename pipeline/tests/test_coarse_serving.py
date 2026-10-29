"""Last-good visuals remain traversable with collision from the same generation."""

import hashlib
import json

import pytest

from turku3d import config as C
from turku3d.coarse.service import Builder
from turku3d.coarse.serving import retained_fragment
from turku3d.coarse.retention import prune_tile, remember_full

TILE = C.VisualTile(0, 0, 0)


def publish(root, name, *, code="old", structural="structure", inputs="raw", kind="full"):
    out = root / TILE.id
    (out / "col").mkdir(parents=True, exist_ok=True)

    def payload(stem, suffix, body):
        digest = hashlib.sha256(body).hexdigest()
        url = f"{stem}.{digest}.{suffix}"
        (out / url).write_bytes(body)
        return dict(url=url, sha256=digest, bytes=len(body))

    value = dict(coarse=True, tile=TILE.id, code=code, collision_code=structural,
                 inputs=inputs, pass_kind=kind,
                 visual={TILE.id: payload("vis", "glb", code.encode())},
                 roads=payload("roads", "json", code.encode()),
                 vehicles=[dict(object_id=code)],
                 collision={c.id: payload("col/" + c.id, "col", structural.encode())
                            for c in TILE.collision_tiles()})
    (out / name).write_text(json.dumps(value))
    return value


def builder(root):
    result = Builder(root, code="new", workers=0, input_key=lambda _: "raw",
                     structural_input_key=lambda _: "raw")
    result.collision_code = lambda: "structure"
    return result


def test_retained_full_serves_immediately_while_upgrade_remains_queued(tmp_path):
    old = publish(tmp_path, "manifest.json")
    b = builder(tmp_path)
    state = b.request(TILE.id, 0)
    assert state["state"] == "built" and state["stale"]
    assert state["fragment"]["visual"] == old["visual"]
    assert state["fragment"]["collision"] == old["collision"]
    assert TILE.id in b.upgrade_queue and TILE.id in b.servable
    assert TILE.id in b.journal.rows  # Serving fallback must not forget the replacement.
    b.upgrade_queue.clear()
    b.building[TILE.id] = 1
    assert b.request(TILE.id, 0)["fragment"]["stale"]
    b.building.clear()
    b.stop()


@pytest.mark.parametrize("change", ["code", "inputs", "unknown"])
def test_retained_collision_survives_structural_change_and_failed_rebuild(tmp_path, change):
    old = publish(tmp_path, "manifest.json", structural="obsolete" if change == "code" else "structure",
                  inputs="obsolete" if change == "inputs" else "raw")
    b = builder(tmp_path)
    if change == "unknown":
        b.input_key = lambda _: None
    value = retained_fragment(b, TILE.id)
    assert value["visual"] == old["visual"] and value["collision"] == old["collision"]
    assert value["stale"] and value["generation"] == "old"
    import time
    b.failed[TILE.id] = ("replacement failed", time.time())
    assert b.request(TILE.id, 0)["fragment"]["collision"] == old["collision"]
    b.stop()


def test_first_pass_cannot_replace_collision_under_retained_full_visuals(tmp_path):
    old = publish(tmp_path, "manifest.json", structural="obsolete")
    remember_full(tmp_path / TILE.id)
    first = publish(tmp_path, "collision.json", code="new", kind="first")
    b = builder(tmp_path)
    fallback = b.request(TILE.id, 0)["fragment"]
    assert fallback["visual"] == old["visual"]
    assert fallback["collision"] == old["collision"] != first["collision"]
    assert fallback["roads"] == old["roads"] != first["roads"]
    assert fallback["vehicles"] == old["vehicles"]
    assert fallback["stale"] and fallback["pass_kind"] == "full"
    assert fallback["generation"] == "old"
    # The pair stays reachable beyond grace periods and a replaced primary pointer.
    import time
    (tmp_path / TILE.id / "manifest.json").unlink()
    prune_tile(tmp_path / TILE.id, now=time.time() + 3600)
    assert retained_fragment(b, TILE.id)["collision"] == old["collision"]
    full = publish(tmp_path, "manifest.json", code="new")
    assert retained_fragment(b, TILE.id) == {**full, "generation": "new"}
    b.stop()


def test_missing_retained_visual_falls_back_to_current_basic(tmp_path):
    old = publish(tmp_path, "manifest.json", structural="obsolete")
    first = publish(tmp_path, "collision.json", code="new", kind="first")
    (tmp_path / TILE.id / old["visual"][TILE.id]["url"]).unlink()
    b = builder(tmp_path)
    assert retained_fragment(b, TILE.id)["visual"] == first["visual"]
    b.stop()


def test_last_full_can_supply_unchanged_collision_without_primary_pointer(tmp_path):
    old = publish(tmp_path, "last-full.json")
    b = builder(tmp_path)
    value = retained_fragment(b, TILE.id)
    assert value["visual"] == old["visual"] and value["collision"] == old["collision"]
    assert value["stale"]
    b.stop()


@pytest.mark.parametrize("current_physics", [True, False])
def test_public_both_endpoints_serve_retained_full_and_schedule_upgrade(tmp_path, current_physics):
    import importlib.util
    import io

    spec = importlib.util.spec_from_file_location("draft_public", C.REPO_ROOT / "tools/remote-drafts/public.py")
    public = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(public)
    old = publish(tmp_path, "manifest.json", structural="obsolete")
    first = publish(tmp_path, "collision.json", code="new", kind="first") if current_physics else None
    b = builder(tmp_path)
    cls = public.public_handler(b, None, {"queue_max": 1, "tile_bounds": [-2, -2, 2, 2]})
    b.priority["t0_1_1"] = 0  # Usable retained output bypasses occupied build admission.
    for endpoint in ("collision", "manifest"):
        handler = object.__new__(cls)
        handler.path = f"/coarse/{TILE.id}/{endpoint}.json"
        handler.command, handler.headers = "GET", {}
        handler.wfile = io.BytesIO()
        handler.send_response = lambda status: setattr(handler, "status", status)
        handler.send_header = lambda *_: None
        handler.end_headers = lambda: None
        handler.do_GET()
        body = json.loads(handler.wfile.getvalue())
        assert handler.status == 200
        assert body["stale"] and body["pass_kind"] == "full"
        assert body["visual"] == old["visual"]
        assert body["collision"] == old["collision"]
        assert body["generation"] == "old"
        assert body["collision_bundle"]["bytes"] == sum(e["bytes"] for e in old["collision"].values())
        if first:
            assert body["collision"] != first["collision"]
            assert TILE.id in b.upgrade_queue
        else:
            assert TILE.id in b.priority
        assert not {"code", "inputs", "seconds"} & body.keys()
        assert TILE.id in b.journal.rows
    b.stop()


def test_first_pass_pair_survives_structural_deploy_before_new_collision(tmp_path):
    old = publish(tmp_path, "collision.json", structural="obsolete", kind="first")
    b = builder(tmp_path)
    value = b.request(TILE.id, 0)["fragment"]
    assert value["visual"] == old["visual"]
    assert value["collision"] == old["collision"] and value["generation"] == "old"
    assert value["stale"] and TILE.id in b.priority
    b.stop()


@pytest.mark.parametrize("missing", ["visual", "collision"])
def test_incomplete_generation_falls_back_to_older_complete_pair(tmp_path, missing):
    old = publish(tmp_path, "last-full.json", code="older", structural="oldest")
    newer = publish(tmp_path, "manifest.json", code="old", structural="obsolete")
    entry = next(iter(newer[missing].values()))
    (tmp_path / TILE.id / entry["url"]).unlink()
    b = builder(tmp_path)
    value = retained_fragment(b, TILE.id)
    assert value["visual"] == old["visual"] and value["collision"] == old["collision"]
    b.stop()


def test_no_complete_collision_means_no_visible_fragment(tmp_path):
    old = publish(tmp_path, "manifest.json", structural="obsolete")
    for entry in old["collision"].values():
        (tmp_path / TILE.id / entry["url"]).unlink()
    b = builder(tmp_path)
    assert retained_fragment(b, TILE.id) is None
    assert "fragment" not in b.request(TILE.id, 0)
    b.stop()
