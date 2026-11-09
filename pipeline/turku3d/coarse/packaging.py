"""Content-addressed draft visual packaging, independent of generator source versions."""

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
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
    "piers",
    "yard_cover",
    "harbour",
    "rigging",
    "boat_inputs",
    "walkable",
    "gardens",
    "garden_inputs",
    "build",
    "archetypes",
    "occluders",
    "blocks",
    "basic",
    "area",
    "collision",
    "collision_key",
    "banks",
    "quay_lighting",
    "water",
    "water_appearance",
    "water_levels",
    "garden_finishes",
    "hedges",
    "parked",
    "packaging",
    "stages",
    "inputs",
    "input_keys",
    "sources",
    "surfaces",
    "surface_join",
    "surface_library",
    "ground_finishes",
    "street_materials",
    "street_profiles",
    "street_survey",
    "bridges",
    "bridge_access",
    "bridge_details",
    "bridge_materials",
    "bridge_kit",
    "bridge_join",
    "bridge_survey",
    "night_accents",
    "kerbs",
    "stairs",
    "stair_survey",
    "retaining",
    "retaining_survey",
    "attachments",
    "soffits",
    "tunnels",
    "underpasses",
    "openings",
    "portals",
    "road_top",
    "forests",
    "forest_floor",
    "meadows",
    "forest_paths",
    "visibility",
    "canopy_trees",
    "fences",
    "fence_survey",
    "route_surface",
    "furniture",
    "playgrounds",
    "tree_inputs",
    "geometry",
    "extrusion",
    "roofs",
    "register",
    "stored_facades",
    "slope_buildings",
    "funicular",
    "floodlights",
    "footings",
    "metal_roofs",
    "passage_grade",
    "proximity",
    "roof_returns",
    "stair_rails",
    "surface_audit",
    "surface_closure",
    "tunnel_ground",
    "venues",
    "vessels",
    "walkable_audit",
    "wires",
)
# Producers outside coarse/, relative to pipeline/turku3d.
SOURCES = (
    "__init__.py",
    "buildings/inputs.py",
    "buildings/reconstruct.py",
    "cities/__init__.py",
    "ground/curbs.py",
    "ground/finished.py",
    "ground/inputs.py",
    "ground/mesh.py",
    "ground/points.py",
    "integer_rows.py",
    "lidar/__init__.py",
    "lidar/cache.py",
    "lidar/profiles.py",
    "lidar/surface.py",
    "nl/convert.py",
    "nl/lidar.py",
    "package/facades.py",
    "package/manifest.py",
    "props/facing.py",
    "props/racks.py",
    "props/seating.py",
    "props/signposts.py",
    "region/cache.py",
    "source_code.py",
    "tile_layer.py",
    "wire_points.py",
)
# What a build reaches that must not change the producer identity, relative to pipeline/turku3d
# (a trailing "/" covers a package). tests/test_producer_identity.py fails for any module or
# resource the build reaches that is neither a producer nor listed here.
NOT_PRODUCERS = {
    "coarse/audit.py": "verification queued after publication",
    "coarse/material_audit.py": "a manifest report only",
    "coarse/retention.py": "build directory retention",
    "profiling.py": "stage timing",
    "fetch/": "network fetch and the raw registry; raw inputs enter by content through input keys",
    "nl/fetch.py": "network fetch; raw inputs enter by content through input keys",
    "migrations/": "the farm's migration chain applies and records them; a new migration must not rebuild tiles",
    "coarse/span_lamps.py": "reached only through the span-lamps@N migration, whose version keys its output",
    "coarse/span_lamp_seams.py": "shared survey windows of the span-lamps@N migration",
    "coarse/square_lights.py": "reached only through the square-lights@N migration, whose version keys its output",
    "../../textures/library/facade-programs.json": "per building through facade_programs.program_hash",
    "../../textures/library/facade-appearance.json": "per building through facade_programs.program_hash",
}


def package_sources():
    return sorted(p for p in BRIDGE.parent.glob("*.mjs") if not p.name.endswith(".test.mjs"))


def producer_paths():
    from turku3d.facade_programs import source_paths
    from turku3d.landmarks.descriptions import source_paths as landmark_sources

    props = Path(__file__).parents[1] / "props"
    return sorted(
        [
            *source_paths(),
            Path(__file__).parents[1] / "wall_surfaces.py",
            Path(__file__).parents[1] / "wall_assembly.py",
            *[Path(__file__).with_name(name + ".py") for name in PRODUCERS],
            *[Path(__file__).parents[1] / name for name in SOURCES],
            Path(__file__).with_name("vessel_observations.json"),
            Path(__file__).with_name("quay_lighting_observations.json"),
            Path(__file__).parents[1] / "stage_cache.py",
            Path(__file__).parents[1] / "spatial.py",
            Path(__file__).parents[1] / "lidar/decoded.py",
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
            *sorted((Path(__file__).parents[1] / "playgrounds").glob("*.py")),
            props / "lights.py",
            Path(__file__).parents[1] / "night_lighting.py",
            Path(__file__).parents[1] / "night_lighting.json",
            props / "orientation.py",
            Path(C.__file__),
            *landmark_sources(),
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
        facadeKit=ensure_kit(work / "shared"),
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


def prepared_kit(root):
    """The output root's facade kit; with a shared cache, one deterministic render for all roots.

    Each product generation is a new root. Rendering the same kit there again costs about
    30 s CPU, and every worker that starts at once would render its own copy.
    """
    from turku3d.facade_kit import code_hash

    target = root / f"facade-kit-{code_hash()}"
    if (target / "kit.json").exists():
        return ensure_kit(root)
    shared = os.environ.get("TURKU3D_SHARED_CACHE")
    source = _kit_locked(Path(shared) / "facade-kits", ensure_kit) if shared else None

    def materialise(root):
        if source is not None and not (target / "kit.json").exists():
            stage = Path(tempfile.mkdtemp(prefix="facade-kit-", dir=root))
            maps = {}
            for slot, path in source["maps"].items():
                shutil.copyfile(path, stage / Path(path).name)
                maps[slot] = str(target / Path(path).name)
            (stage / "kit.json").write_text(json.dumps(dict(key=source["key"], maps=maps, version=3)))
            os.rename(stage, target)
        return ensure_kit(root)

    return _kit_locked(root, materialise)


def _kit_locked(root, operation):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".facade-kit.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return operation(root)


def shared_package_inputs(library, work):
    """Material library identities and facade kit shared by every package of one build.

    Whole-building models package dozens of times per tile; walking and fingerprinting
    the library and preparing the kit once keeps that cost per tile, not per model.
    """
    from turku3d.tile_layer import identities

    files = {}
    for directory, _, names in os.walk(library, followlinks=True):
        for name in sorted(names):
            path = Path(directory) / name
            files["library/" + str(path.relative_to(library))] = path
    kit = prepared_kit(work / "shared")
    for slot, path in kit["maps"].items():
        files["kit/" + slot] = Path(path)
    kit = {k: v for k, v in kit.items() if k != "maps"}
    return identities(files), kit


def package_visual(tile, layer_files, stage, work, library, *, cache_unit=None, shared=None):
    """Reuse a packed visual when emitted geometry, material files and packaging code match.

    `shared` is `shared_package_inputs(library, work)` computed once by
    the caller; the cache key is identical either way.
    """
    from turku3d.tile_layer import identities

    from .build import _write

    files = {f"input/{i}": p for i, p in enumerate(layer_files)}
    building_keys = set().union(*(glb_buildings(p.read_bytes()) for p in layer_files))
    stored = stored_request(building_keys)
    for identity in stored["facadeBindings"]:
        building, side = identity.rsplit("_", 1)
        directory = Path(stored["facades"]) / building
        sidecar = directory / f"{side}.json"
        files["facades/" + identity] = sidecar
        for name in json.loads(sidecar.read_text())["files"].values():
            files[f"facades/{building}/{name}"] = directory / name
    shared_ids, kit = shared or shared_package_inputs(library, work)
    file_ids = {**identities(files), **shared_ids}
    cache = StageCache(work / ".stages" / (cache_unit or tile.id), retain=True)

    def package():
        visual, stats = _package_visual(
            tile,
            layer_files,
            stage,
            work,
            library,
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
            C.REPO_ROOT / "pipeline/env/package-lock.json",
            _package_visual,
        ),
        [tile.id, dict(sorted(file_ids.items())), kit, parameter_hash(building_keys)],
        package,
    )
    for name, data in assets.items():
        destination = work / "shared" / name
        if not destination.exists():
            _write(destination, data)
    cache.report(stage / "package-times.json")
    return visual, stats
