"""Last-good serving must improve availability without reviving obsolete collision."""

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


@pytest.mark.parametrize("change", ["code", "inputs", "missing"])
def test_obsolete_or_missing_collision_never_serves_even_after_failure(tmp_path, change):
    old = publish(tmp_path, "manifest.json", structural="obsolete" if change == "code" else "structure",
                  inputs="obsolete" if change == "inputs" else "raw")
    if change == "missing":
        (tmp_path / TILE.id / next(iter(old["collision"].values()))["url"]).unlink()
    b = builder(tmp_path)
    assert retained_fragment(b, TILE.id)["collision"] == {}
    assert retained_fragment(b, TILE.id)["collision_pending"]
    import time
    b.failed[TILE.id] = ("replacement failed", time.time())
    assert b.request(TILE.id, 0)["fragment"]["collision"] == {}
    b.stop()


def test_new_collision_pairs_with_retained_full_and_latest_full_wins(tmp_path):
    old = publish(tmp_path, "manifest.json", structural="obsolete")
    remember_full(tmp_path / TILE.id)
    first = publish(tmp_path, "collision.json", code="new", kind="first")
    b = builder(tmp_path)
    fallback = b.request(TILE.id, 0)["fragment"]
    assert fallback["visual"] == old["visual"]
    assert fallback["collision"] == first["collision"]
    assert fallback["stale"] and fallback["pass_kind"] == "full"
    # Even after several grace intervals, last-full and its assets remain reachable.
    import time
    prune_tile(tmp_path / TILE.id, now=time.time() + 3600)
    assert retained_fragment(b, TILE.id)["visual"] == old["visual"]
    full = publish(tmp_path, "manifest.json", code="new")
    assert retained_fragment(b, TILE.id) == full
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
        if current_physics:
            assert body["collision"] == first["collision"]
            assert body["collision_bundle"]["bytes"] == sum(e["bytes"] for e in first["collision"].values())
            assert TILE.id in b.upgrade_queue
        else:
            assert body["collision"] == {} and body["collision_pending"]
            assert "collision_bundle" not in body
            assert TILE.id in b.priority
        assert not {"code", "inputs", "seconds"} & body.keys()
        assert TILE.id in b.journal.rows
    b.stop()


def test_unknown_input_identity_never_authorizes_stale_collision(tmp_path):
    publish(tmp_path, "manifest.json")
    b = builder(tmp_path)
    b.input_key = lambda _: None
    assert retained_fragment(b, TILE.id)["collision"] == {}
    assert retained_fragment(b, TILE.id)["collision_pending"]
    b.stop()


def test_first_pass_visual_survives_structural_deploy_before_new_collision(tmp_path):
    old = publish(tmp_path, "collision.json", structural="obsolete", kind="first")
    b = builder(tmp_path)
    value = b.request(TILE.id, 0)["fragment"]
    assert value["visual"] == old["visual"]
    assert value["collision_pending"] and value["collision"] == {}
    assert TILE.id in b.priority
    b.stop()


def test_full_preview_does_not_need_obsolete_collision_or_roads_payloads(tmp_path):
    old = publish(tmp_path, "manifest.json", structural="obsolete")
    for entry in old["collision"].values():
        (tmp_path / TILE.id / entry["url"]).unlink()
    b = builder(tmp_path)
    value = b.request(TILE.id, 0)["fragment"]
    assert value["visual"] == old["visual"]
    assert value["pass_kind"] == "full" and value["collision"] == {}
    assert "vehicles" not in value and "roads" not in value
    b.stop()
