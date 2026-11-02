"""Content-addressed draft visual packaging, independent of generator source versions."""

import hashlib
import json
import os
import shutil
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

from turku3d import config as C
from turku3d.facade_assets import stored_request
from turku3d.facade_kit import code_hash as kit_hash
from turku3d.facade_kit import ensure_kit, parameter_hash
from turku3d.package.facades import glb_buildings
from turku3d.stage_cache import StageCache, source_key

BRIDGE = Path(C.REPO_ROOT / "pipeline/turku3d/package/visual.mjs")
# Geometry/material dependencies only. Service, fetch scheduling, audits and benchmarks
# must not invalidate finished world tiles when their orchestration changes.
PRODUCERS = (
    "boats",
    "boat_inputs",
    "walkable",
    "gardens",
    "garden_inputs",
    "build",
    "basic",
    "area",
    "collision",
    "collision_key",
    "banks",
    "water",
    "parked",
    "packaging",
    "stages",
    "inputs",
    "input_keys",
    "sources",
    "surfaces",
    "ground_finishes",
    "street_profiles",
    "street_survey",
    "bridges",
    "bridge_access",
    "bridge_details",
    "bridge_join",
    "bridge_survey",
    "kerbs",
    "stairs",
    "stair_survey",
    "retaining",
    "tunnels",
    "openings",
    "portals",
    "road_top",
    "forests",
    "canopy_trees",
    "canopy_context",
    "fences",
    "furniture",
    "tree_inputs",
    "geometry",
    "extrusion",
    "roofs",
    "register",
    "stored_facades",
)


def package_sources():
    return sorted(p for p in BRIDGE.parent.glob("*.mjs") if not p.name.endswith(".test.mjs"))


def producer_paths():
    from turku3d.facade_programs import source_paths

    props = Path(__file__).parents[1] / "props"
    return sorted(
        [
            *source_paths(),
            Path(__file__).parents[1] / "wall_surfaces.py",
            Path(__file__).parents[1] / "wall_assembly.py",
            *[Path(__file__).with_name(name + ".py") for name in PRODUCERS],
            Path(__file__).parents[1] / "stage_cache.py",
            Path(__file__).parents[1] / "spatial.py",
            *sorted((Path(__file__).parents[1] / "roads").glob("*.py")),
            *package_sources(),
            *sorted((C.REPO_ROOT / "game/public/basis").glob("basis_transcoder.*")),
            Path(__file__).parents[1] / "buildings/source_surfaces.py",
            Path(__file__).parents[1] / "ground/materials.py",
            Path(__file__).parents[1] / "bridges/build.py",
            *sorted((Path(__file__).parents[1] / "parked").glob("*.py")),
            *sorted((Path(__file__).parents[1] / "parked/assets").glob("*")),
            props / "__init__.py",
            props / "models.py",
            props / "lights.py",
            props / "orientation.py",
            Path(C.__file__),
            *[
                Path(__file__).parents[1] / "landmarks" / name
                for name in ("appearance.py", "atlas.py", "descriptions.py", "procedural.py")
            ],
            Path(__file__).parents[1] / "layers.py",
            Path(__file__).parents[1] / "facade_assets.py",
            Path(__file__).parents[1] / "package/gltf.py",
            Path(__file__).parents[1] / "ground/water.py",
            *sorted((Path(__file__).parents[1] / "col").glob("*.py")),
            *sorted((Path(__file__).parents[1] / "col").glob("*.mjs")),
            Path(__file__).parents[1] / "col/materials_v1.json",
            *sorted((Path(__file__).parents[1] / "vegetation").glob("*.py")),
            *sorted((Path(__file__).parents[1] / "structures").glob("*.py")),
            C.REPO_ROOT / "pipeline/env/package-lock.json",
        ]
    )


def code_hash() -> str:
    """Producer identity: the coarse sources plus the shared visual packager they call."""
    h = hashlib.sha256(kit_hash().encode())
    # __file__ can enter through repo's symlink while C.REPO_ROOT is resolved.
    # Absolute-prefix ordering must not change identity for identical source bytes.
    for p in sorted(producer_paths(), key=lambda p: p.as_posix().rsplit("/pipeline/", 1)[-1]):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


@lru_cache(maxsize=2)
def _current_code(signature):
    return code_hash()


def current_code():
    """Reuse content hashes between requests; edits still invalidate by file identity."""
    from turku3d import facade_kit

    paths = [
        *producer_paths(),
        Path(facade_kit.__file__),
        Path(facade_kit.renderer().__file__),
        *facade_kit.palette_sources(),
    ]
    return _current_code(tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths))


class Packager:
    """One persistent ``node visual.mjs --serve`` per process: a build worker packages every tile
    without starting node and loading glTF-Transform again (about 0.3 s CPU, seconds of wall time
    on a loaded host). A packager that died is started again on the next tile."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def package(self, requests: Path) -> None:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self.proc = subprocess.Popen(
                    ["node", str(BRIDGE), "--serve"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    text=True,
                )
            self.proc.stdin.write(f"{requests}\n")
            self.proc.stdin.flush()
            answer = self.proc.stdout.readline().strip()
        if answer != "ok":
            message = answer.removeprefix("error ")
            if answer.startswith("error "):
                message = json.loads(message)
            raise RuntimeError(f"visual packaging failed\n{message or 'the packager exited'}")


PACKAGER = Packager()


def _package_visual(
    tile: C.VisualTile,
    layer_files: list[Path],
    stage: Path,
    work: Path,
    library: Path,
    *,
    first_pass: bool = False,
    building_keys=(),
) -> tuple[bytes, dict]:
    request = dict(
        tile=tile.id,
        level=0,
        inputs=[str(p) for p in layer_files],
        output=str(stage / "out" / "tile.glb"),
        stats=str(stage / "out" / "stats.json"),
        library=str(library),
        facades=str(stage / "no-facades"),
        facadeBindings=[],
        facadeDensity={},
        groundBinding=None,
        textureCache=str(stage.parent / ".texture-cache"),
        sharedTextureCache=str(work / ".compiled-textures"),
        coarseTerrain=False,
        draftTextures=True,
        auditRoot=str(work / ".visual-audits"),
        facadeKit=None if first_pass else ensure_kit(work / "shared"),
        firstPass=first_pass,
    )
    request.update(stored_request(building_keys))
    (stage / "out").mkdir(parents=True, exist_ok=True)
    req = stage / "requests.json"
    req.write_text(json.dumps([request]))
    PACKAGER.package(req)
    stats = json.loads((stage / "out" / "stats.json").read_text())
    shared = stage / "shared"
    if shared.exists():
        (work / "shared").mkdir(parents=True, exist_ok=True)
        for asset in shared.iterdir():
            dest = work / "shared" / asset.name
            if not dest.exists():
                shutil.copyfile(asset, dest)
    return (stage / "out" / "tile.glb").read_bytes(), stats


def package_visual(tile, layer_files, stage, work, library, *, first_pass=False):
    """Reuse a packed visual when emitted geometry, material files and packaging code match."""
    from turku3d.tile_layer import identities

    from .build import _write

    files = {f"input/{i}": p for i, p in enumerate(layer_files)}
    for directory, _, names in os.walk(library, followlinks=True):
        for name in sorted(names):
            path = Path(directory) / name
            files["library/" + str(path.relative_to(library))] = path
    building_keys = set().union(*(glb_buildings(p.read_bytes()) for p in layer_files))
    stored = stored_request(building_keys)
    for identity in stored["facadeBindings"]:
        building, side = identity.rsplit("_", 1)
        directory = Path(stored["facades"]) / building
        sidecar = directory / f"{side}.json"
        files["facades/" + identity] = sidecar
        for name in json.loads(sidecar.read_text())["files"].values():
            files[f"facades/{building}/{name}"] = directory / name
    kit = None if first_pass else ensure_kit(work / "shared")
    if kit:
        for slot, path in kit["maps"].items():
            files["kit/" + slot] = Path(path)
        kit = {k: v for k, v in kit.items() if k != "maps"}
    cache = StageCache(work / ".stages" / tile.id, retain=True)

    def package():
        visual, stats = _package_visual(
            tile,
            layer_files,
            stage,
            work,
            library,
            first_pass=first_pass,
            building_keys=building_keys,
        )
        assets = {
            name: (stage / "shared" / name).read_bytes() for name in stats.get("shared_images", [])
        }
        return visual, stats, assets

    visual, stats, assets = cache.run(
        "package",
        source_key(
            *package_sources(),
            *sorted((C.REPO_ROOT / "game/public/basis").glob("basis_transcoder.*")),
            Path(__file__).parents[1] / "stage_cache.py",
            C.REPO_ROOT / "pipeline/env/package-lock.json",
            _package_visual,
        ),
        [tile.id, first_pass, identities(files), kit, parameter_hash(building_keys)],
        package,
    )
    for name, data in assets.items():
        destination = work / "shared" / name
        if not destination.exists():
            _write(destination, data)
    cache.report(stage / "package-times.json")
    return visual, stats
