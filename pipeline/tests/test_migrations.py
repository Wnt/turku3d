"""Tile migrations: the generator's in-place chain equals the farm's migration of a base product."""

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from turku3d import migrations
from turku3d.migrations import core, registry

DEV = Path(__file__).resolve().parents[2] / "scripts/dev"
sys.path.insert(0, str(DEV))
spec = importlib.util.spec_from_file_location("tile_migrate", DEV / "tile_migrate.py")
tile_migrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tile_migrate)

TILE = "t0_0_0"
BASE_REV, BASE_GEN, CODE_REV = "a" * 40, "b" * 64, "c" * 40


def sha(data):
    return hashlib.sha256(data).hexdigest()


def base_product(generation_root: Path) -> dict:
    """A small finished product: visual GLB with a shared texture, 16 cells, roads, a model."""
    tile_dir, shared = generation_root / TILE, generation_root / "shared"
    (tile_dir / "col").mkdir(parents=True)
    shared.mkdir(parents=True, exist_ok=True)
    texture = b"ktx2-texture"
    (shared / f"{sha(texture)}.ktx2").write_bytes(texture)
    vis = core.glb_pack({"asset": {"version": "2.0"}, "images": [{"uri": f"../shared/{sha(texture)}.ktx2"}]},
                        b"\1\2\3\4")
    (tile_dir / f"vis.{sha(vis)}.glb").write_bytes(vis)
    model = core.glb_pack({"asset": {"version": "2.0"}, "meshes": [{"name": "house"}]}, b"\5\6\7\x08")
    (shared / f"{sha(model)}.glb").write_bytes(model)
    roads = b'{"edges": []}'
    (tile_dir / f"roads.{sha(roads)}.json").write_bytes(roads)
    collision = {}
    for x in range(4):
        for z in range(4):
            body = f"cell {x} {z}".encode()
            url = f"col/c_{x}_{z}.{sha(body)}.col"
            (tile_dir / url).write_bytes(body)
            collision[f"c_{x}_{z}"] = dict(url=url, sha256=sha(body), bytes=len(body))
    manifest = dict(
        coarse=True, tile=TILE, pass_kind="full", built="2026-11-03T09:20:42Z",
        visual={TILE: dict(url=f"vis.{sha(vis)}.glb", sha256=sha(vis), bytes=len(vis), tris=1)},
        collision=collision, roads=dict(url=f"roads.{sha(roads)}.json", sha256=sha(roads), bytes=len(roads)),
        vehicle_models={}, buildingPlacements=dict(
            version=1, models=[dict(id=sha(model), uri=f"../shared/{sha(model)}.glb", bytes=len(model))]),
        lights=dict(count=0), building_keys=["b2", "b1"])
    (tile_dir / "manifest.json").write_bytes(core.manifest_bytes(manifest))
    (tile_dir / "stage-times.json").write_text("{}")
    return manifest


def tint_visual(product):
    """A payload edit: rewrite the visual GLB and re-home its texture in the shared store."""
    entry = product.manifest["visual"][product.tile]
    document, binary = core.glb_chunks(product.read(entry["url"]))
    texture = product.read(document["images"][0]["uri"])
    document["images"][0]["uri"] = product.put("../shared/{hash}.ktx2", texture + b"-tinted")
    document["extras"] = {"tint": sorted(product.manifest["building_keys"])}
    product.replace(entry, core.glb_pack(document, binary))


def keep_roads(product):
    """Byte-identical rewrite: the existing payload stays linked."""
    product.replace(product.manifest["roads"], product.read(product.manifest["roads"]["url"]))


TINT = core.Migration("test-tint", 1, tint_visual)
ROADS = core.Migration("test-roads", 1, keep_roads)


@pytest.fixture
def chain(monkeypatch):
    def use(*steps):
        monkeypatch.setattr(registry, "CHAIN", tuple(steps))
        return steps
    return use


def farm(tmp_path):
    root = tmp_path / "farm"
    base = root / "products" / BASE_GEN
    manifest = base_product(base)
    (base / TILE / "ready.json").write_text("{}")
    (root / "revisions" / CODE_REV / "repo").mkdir(parents=True)
    (root / "revisions" / BASE_REV).mkdir(parents=True)
    (root / "revisions" / BASE_REV / "revision.json").write_text(
        json.dumps(dict(revision=BASE_REV, generation=BASE_GEN, commit=BASE_REV)))
    meta = tile_migrate.derive(root, BASE_REV, CODE_REV)
    return root, meta, manifest


def payloads(generation_root: Path, manifest: dict) -> dict:
    product = core.Product(generation_root / TILE, manifest)
    return {p: (generation_root / TILE / p).read_bytes() for p in core.references(product)}


def test_generator_chain_equals_farm_migration_of_the_base_product(tmp_path, chain):
    chain(registry.CHAIN[0], TINT, ROADS)
    root, meta, base_manifest = farm(tmp_path)
    built = tmp_path / "generator"
    shutil.copytree(root / "products" / BASE_GEN, built)
    (built / TILE / "manifest.json").unlink()
    generated = migrations.apply_in_place(built / TILE, base_manifest)

    result = tile_migrate.migrate_tile(root, meta, TILE)
    migrated_root = root / "products" / meta["generation"]
    stored = json.loads((migrated_root / TILE / "manifest.json").read_bytes())
    assert result["state"] == "migrated" and result["applied"] == ["chain-record@1", "test-tint@1", "test-roads@1"]
    assert payloads(migrated_root, stored) == payloads(built, generated)
    assert stored.pop("migration_base") == dict(
        revision=BASE_REV, generation=BASE_GEN,
        manifest_sha256=sha((root / "products" / BASE_GEN / TILE / "manifest.json").read_bytes()))
    assert core.manifest_bytes(stored) == core.manifest_bytes(generated)
    assert stored["migrations"] == [dict(name="chain-record", version=1), dict(name="test-tint", version=1),
                                    dict(name="test-roads", version=1)]
    # Replaced tile payloads leave the generator's product; the base is never touched.
    assert not (built / TILE / base_manifest["visual"][TILE]["url"]).exists()
    assert (root / "products" / BASE_GEN / TILE / base_manifest["visual"][TILE]["url"]).exists()


def test_farm_migration_links_unchanged_payloads_and_validates(tmp_path, chain):
    chain(registry.CHAIN[0], TINT, ROADS)
    root, meta, base_manifest = farm(tmp_path)
    tile_migrate.migrate_tile(root, meta, TILE)
    base, migrated = root / "products" / BASE_GEN, root / "products" / meta["generation"]
    stored = json.loads((migrated / TILE / "manifest.json").read_bytes())
    for cell in stored["collision"].values():
        assert (migrated / TILE / cell["url"]).stat().st_ino == (base / TILE / cell["url"]).stat().st_ino
    road = stored["roads"]["url"]
    assert road == base_manifest["roads"]["url"]
    assert (migrated / TILE / road).stat().st_ino == (base / TILE / road).stat().st_ino
    model = stored["buildingPlacements"]["models"][0]["uri"]
    assert (migrated / TILE / model).stat().st_ino == (base / TILE / model).stat().st_ino
    assert stored["visual"][TILE]["url"] != base_manifest["visual"][TILE]["url"]
    assert len(list((migrated / "shared").glob("*.ktx2"))) == 1  # Only the referenced texture.
    assert tile_migrate.Farm.validate(migrated, TILE, stored) == 20
    ready = json.loads((migrated / TILE / "ready.json").read_text())
    assert (ready["generation"], ready["written"]) == (meta["generation"], 2)
    assert tile_migrate.migrate_tile(root, meta, TILE)["state"] == "kept"


def test_a_snapshot_with_other_transform_code_is_refused(tmp_path, chain):
    chain(registry.CHAIN[0], TINT)
    root, meta, _ = farm(tmp_path)
    meta["migrations"][1]["code_hash"] = "0" * 64
    with pytest.raises(RuntimeError, match="differs"):
        tile_migrate.migrate_tile(root, meta, TILE)


def test_chain_resumes_from_a_finished_prefix_generation(tmp_path, chain):
    chain(registry.CHAIN[0], TINT)
    root, first, _ = farm(tmp_path)
    tile_migrate.migrate_tile(root, first, TILE)
    chain(registry.CHAIN[0], TINT, ROADS)
    (root / "revisions" / ("d" * 40) / "repo").mkdir(parents=True)
    second = tile_migrate.derive(root, BASE_REV, "d" * 40)
    assert second["parents"] == [BASE_GEN, first["migrations"][0]["generation"], first["generation"]]
    result = tile_migrate.migrate_tile(root, second, TILE)
    assert (result["source_generation"], result["applied"]) == (first["generation"], ["test-roads@1"])
    resumed = (root / "products" / second["generation"] / TILE / "manifest.json").read_bytes()
    shutil.rmtree(root / "products" / first["generation"])
    shutil.rmtree(root / "products" / second["generation"])
    assert tile_migrate.migrate_tile(root, second, TILE)["source_generation"] == BASE_GEN
    assert (root / "products" / second["generation"] / TILE / "manifest.json").read_bytes() == resumed


def test_generation_identity_follows_order_version_and_code():
    base = "0" * 64
    record = registry.CHAIN[0]
    one = core.chain_generations(base, [record, TINT])
    assert one == core.chain_generations(base, [record, TINT])
    assert one[-1] != core.chain_generations(base, [TINT, record])[-1]
    assert one[-1] != core.chain_generations(base, [record, core.Migration("test-tint", 2, tint_visual)])[-1]
    assert one[-1] != core.chain_generations("1" * 64, [record, TINT])[-1]
    files = {p.relative_to(core.PACKAGE).as_posix() for p in core.closure([core.module_file(
        ["turku3d", "coarse", "build"])])}
    assert {"coarse/build.py", "coarse/__init__.py", "__init__.py", "migrations/__init__.py",
            "stage_cache.py", "config.py"} <= files
    record_files = {p.relative_to(core.PACKAGE).as_posix() for p in core.closure([core.module_file(
        ["turku3d", "migrations", "chain_record"])])}
    assert record_files == {"__init__.py", "migrations/__init__.py", "migrations/core.py",
                            "migrations/chain_record.py"}


def test_payload_names_must_be_content_addressed(tmp_path):
    base_product(tmp_path)
    product = core.Product(tmp_path / TILE, json.loads((tmp_path / TILE / "manifest.json").read_bytes()))
    with pytest.raises(ValueError):
        product.put("vis.glb", b"x")
    with pytest.raises(ValueError):
        product.put("../../elsewhere/{hash}.glb", b"x")
    with pytest.raises(ValueError):
        product.replace(dict(url="vis.glb", bytes=1), b"x")


def test_parity_compares_a_generator_build_with_the_migrated_base(tmp_path, chain):
    chain(registry.CHAIN[0], TINT)
    root, meta, base_manifest = farm(tmp_path)
    tile_migrate.migrate_tile(root, meta, TILE)
    generated_gen, generated_rev = "e" * 64, "f" * 40
    built = root / "products" / generated_gen
    shutil.copytree(root / "products" / BASE_GEN, built)
    manifest = migrations.apply_in_place(built / TILE, dict(
        base_manifest, built="2026-11-04T00:00:00Z", lights=dict(count=0, seconds=1.5)))
    (built / TILE / "manifest.json").write_bytes(core.manifest_bytes(manifest))
    (root / "revisions" / generated_rev).mkdir()
    (root / "revisions" / generated_rev / "revision.json").write_text(
        json.dumps(dict(revision=generated_rev, generation=generated_gen)))
    result = tile_migrate.parity(root, meta, generated_rev, TILE)
    assert result["identical"], result
    assert result["differing_provenance_keys"] == ["built", "migration_base"]
    manifest["lights"] = dict(count=1)
    (built / TILE / "manifest.json").write_bytes(core.manifest_bytes(manifest))
    assert tile_migrate.parity(root, meta, generated_rev, TILE)["differing_content_keys"] == ["lights"]


def test_watching_run_checks_only_new_base_tiles(tmp_path, chain, monkeypatch):
    chain(registry.CHAIN[0])
    root, meta, _ = farm(tmp_path)
    calls = []
    monkeypatch.setattr(tile_migrate, "ProcessPoolExecutor", lambda max_workers: _Inline())
    monkeypatch.setattr(tile_migrate, "_worker", lambda args: calls.append(args[2]) or dict(
        tile=args[2], state="migrated"))
    passes = iter([0, 1])
    def sleep(_):
        if next(passes):
            raise KeyboardInterrupt
        shutil.copytree(root / "products" / BASE_GEN / TILE, root / "products" / BASE_GEN / "t0_1_0")
    monkeypatch.setattr(tile_migrate.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        tile_migrate.run(root, meta, 2, watch=1)
    assert calls == [TILE, "t0_1_0"]


class _Inline:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def map(self, function, items, chunksize=1):
        return map(function, items)
