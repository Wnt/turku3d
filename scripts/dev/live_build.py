"""Build, check and publish the real office distribution during landing."""

import filecmp
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import live_cache
import sandbox as s

TILES = ("t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1")


def affected_tiles(previous, work):
    """Map changed layer meshes to visual tiles; ambiguous changes rebuild all tiles."""
    old = previous / "layers"
    new = work / "build/layers"
    if not old.is_dir() or not new.is_dir():
        return TILES
    if any(path.is_symlink() for root in (old, new) for path in root.rglob("*")):
        return TILES
    changed = set()
    names = {p.relative_to(old) for p in old.rglob("*") if p.is_file()}
    names |= {p.relative_to(new) for p in new.rglob("*") if p.is_file()}
    for name in names:
        before, after = old / name, new / name
        if before.is_file() and after.is_file() and filecmp.cmp(before, after, shallow=False):
            continue
        stem = name.name
        if stem.endswith(".vis.glb") and stem.split(".vis.glb")[0] in TILES:
            changed.add(stem.split(".vis.glb")[0])
        elif stem.endswith(".col.npz") and stem.startswith("c_"):
            _, i, j = stem.split(".")[0].split("_")
            changed.add(f"t0_{int(i) // 4}_{int(j) // 4}")
        elif stem == "MANIFEST.json":
            continue
        else:
            return TILES
    return tuple(tile for tile in TILES if tile in changed)


def same_tree(old, new):
    if not old.is_dir() or not new.is_dir():
        return False
    if any(path.is_symlink() for root in (old, new) for path in root.rglob("*")):
        return False
    before = {p.relative_to(old) for p in old.rglob("*") if p.is_file()}
    after = {p.relative_to(new) for p in new.rglob("*") if p.is_file()}
    return before == after and all(
        filecmp.cmp(old / name, new / name, shallow=False) for name in before
    )


def _clone(source, target):
    if target.exists():
        return
    if not source.exists():
        return
    source = source.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # APFS clones keep a private, writable snapshot without copying asset bytes.
    if subprocess.call(
        ["cp", "-cRL", str(source), str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ):
        shutil.copytree(source, target)


def seed(work, published):
    for relative in ("build/layers", "textures", "dist/tiles"):
        _clone(published / relative, work / relative)
    approvals = published / "facade-approvals/assembly"
    if approvals.is_dir():
        with s.lock(approvals / ".export.lock"):
            shutil.copytree(approvals, work / "textures/assembly", dirs_exist_ok=True)


def baseline(published):
    record = published / "live/live-build.json"
    if record.exists():
        return int(json.loads(record.read_text())["facades_bound"])
    # The legacy deploy recorded this count only in its assembler log.
    legacy_log = published.parent / "logs/deploy-once.log"
    if legacy_log.exists():
        for line in reversed(legacy_log.read_text().splitlines()):
            try:
                result = json.loads(line)
                if (
                    Path(result.get("manifest", "")).resolve()
                    == (published / "dist/tiles/manifest.json").resolve()
                ):
                    return int(result["facades_bound"])
            except (ValueError, KeyError, TypeError):
                continue
    export = published / "textures/assembly/MANIFEST.json"
    if export.exists():
        return len(json.loads(export.read_text())["bound"])
    raise ValueError("published facade baseline is unavailable")


def inspect(manifest_path, facade_count, minimum, reason=None):
    manifest = json.loads(manifest_path.read_text())
    for tile in TILES:
        entry = manifest["visual"][tile]
        if entry.get("tris", 0) < 1000:
            raise ValueError(f"{tile}: office geometry is missing; Chrome tests would skip")
        if entry.get("ground_texture_bytes", 0) <= 0:
            raise ValueError(f"{tile}: ground textures are unbound")
        if entry.get("wire_bytes", 8_000_001) >= 8_000_000:
            raise ValueError(f"{tile}: L0 wire size exceeds 8 MB")
    if facade_count < minimum and not reason:
        raise ValueError(f"facades_bound regressed: {facade_count} < {minimum}; no reported reason")
    if not manifest.get("spawn"):
        raise ValueError("live build has no spawn")
    return {
        "facades_bound": facade_count,
        "facades_baseline": minimum,
        "facade_regression_reason": reason if facade_count < minimum else None,
    }


def warm_checks(repo, work, env):
    """Use the queue-owned Chrome for affected tile specs when available."""
    service = env.get("TURKU3D_WARM_BROWSER_STATE")
    script = repo / "scripts/dev/warm_browser.mjs"
    selector = repo / "scripts/dev/affected_browser.py"
    if not service or not Path(service).is_file() or not script.is_file() or not selector.is_file():
        return [], {}
    selected = subprocess.run(
        [
            env.get("PIPELINE_PY", "python3"),
            str(selector),
            "--base",
            env.get("TURKU3D_VERIFY_BASE", "main"),
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    specs = json.loads(selected.stdout)["tile"]
    if not specs:
        return [], {}
    commands = []
    scopes = {}
    for backend in ("webgpu", "webgl"):
        stage = "chrome-" + backend
        command = [
            "node",
            str(script),
            "run",
            "--repo",
            str(repo),
            "--work",
            str(work),
            "--profile",
            env["TURKU3D_BROWSER_DIR"],
            "--port",
            env["TURKU3D_PORT_BASE"],
            "--service-state",
            service,
            "--backend",
            backend,
        ]
        for spec in specs:
            command.extend(("--spec", spec))
        commands.append((stage, command, repo))
        scopes[stage] = specs
    return commands, scopes


def build(repo, work, published, env, log_path, reason=None):
    seed(work, published)
    minimum = baseline(published)
    durations = {}
    cache_hits = []
    input_hashes = {}
    report = None
    previous = ""
    cache_enabled = (repo / ".git").exists()
    commands = [
        ("refresh", ["make", "refresh"], repo),
        ("tile-build", ["make", "tile-build", "T=" + " ".join(TILES)], repo),
        ("game-build", ["make", "game-build"], repo),
        ("collision-sweep", ["make", "collision-sweep"], repo),
        ("collision-audit", ["make", "collision-audit"], repo),
        (
            "chrome",
            [
                str(repo / "game/node_modules/.bin/playwright"),
                "test",
                "-c",
                "playwright.tile.config.ts",
            ],
            repo / "game",
        ),
    ]
    warm, scopes = warm_checks(repo, work, env)
    if warm:
        commands[-1:] = warm
    with log_path.open("w") as log:
        for stage, command, cwd in commands:
            if stage == "tile-build":
                previous_id = env.get("TURKU3D_PREVIOUS_RELEASE_ID")
                if previous_id:
                    prior = published / "live-releases" / previous_id
                    selected = affected_tiles(prior, work)
                    # Facade and library texture changes affect tile packing globally.
                    if selected and same_tree(prior / "textures", work / "textures"):
                        command = ["make", "tile-build", "T=" + " ".join(selected)]
                        scopes[stage] = selected
            start = time.monotonic()
            log.write(f"\n== {stage}: {' '.join(command)} ==\n")
            log.flush()
            key = (
                live_cache.digest(repo, work, published, stage, previous, scopes.get(stage))
                if cache_enabled
                else None
            )
            if key:
                input_hashes[stage] = key
            cached = live_cache.restore(published, work, stage, key) if key else None
            if cached is not None:
                log.write(f"cache hit {key}\n")
                durations[stage] = 0.0
                cache_hits.append(stage)
                if stage == "tile-build":
                    report = inspect(
                        work / "dist/tiles/manifest.json", cached["facades_bound"], minimum, reason
                    )
                previous = key
                continue
            run_env = {**env, "TILE_DIST": str(work / "dist/tiles")}
            proc = s.run_group(command, cwd=cwd, env=run_env)
            log.write(proc.stdout)
            log.flush()
            durations[stage] = round(time.monotonic() - start, 3)
            if proc.returncode:
                raise ValueError(
                    f"{stage} failed ({proc.returncode}); {s.failure_detail(proc.stdout)}; host load {s.host_load()}: {log_path}; main unchanged"
                )
            if stage == "tile-build":
                facade_count = None
                for line in reversed(proc.stdout.splitlines()):
                    try:
                        facade_count = int(json.loads(line)["facades_bound"])
                        break
                    except (ValueError, KeyError, TypeError):
                        continue
                if facade_count is None:
                    raise ValueError(f"tile-build did not report facades_bound: {log_path}")
                report = inspect(work / "dist/tiles/manifest.json", facade_count, minimum, reason)
            if key:
                live_cache.store(
                    published,
                    work,
                    stage,
                    key,
                    {"facades_bound": report["facades_bound"]} if stage == "tile-build" else {},
                )
                previous = key
    report["seconds"] = durations
    report["cache_hits"] = cache_hits
    report["input_hashes"] = input_hashes
    return report


def prepare(work, published, commit, report, release_id=None):
    """Copy the verified release before advancing main."""
    releases = published / "live-releases"
    releases.mkdir(parents=True, exist_ok=True)
    release = releases / (release_id or commit)
    if release.exists():
        if (published / "live").is_symlink() and (published / "live").resolve() == release:
            raise ValueError(f"release is already live: {release}")
        shutil.rmtree(release)
    release.mkdir()
    artifacts = (
        (work / "build/game", "game"),
        (work / "dist/tiles", "tiles"),
        (work / "build/layers", "layers"),
        (work / "textures", "textures"),
    )
    for source, name in artifacts:
        if not source.is_dir():
            raise ValueError(f"verified {name} is missing: {source}")
        _clone(source, release / name)
        if name in ("game", "tiles"):
            # The existing server caches Brotli by path and mtime. Give each
            # release fresh mtimes so the same stable URL cannot reuse old bytes.
            for asset in (release / name).rglob("*"):
                if asset.is_symlink():
                    raise ValueError(f"verified distribution contains a symlink: {asset}")
                if asset.is_file():
                    os.utime(asset, None)
    (release / "live-build.json").write_text(
        json.dumps({"commit": commit, **report}, indent=2) + "\n"
    )
    return release


def publish(published, commit, release_id=None):
    """Swap one release link; stable game/tile paths resolve through that link."""
    release = published / "live-releases" / (release_id or commit)
    if not (release / "live-build.json").exists():
        raise ValueError(f"prepared release is missing: {release}")
    if json.loads((release / "live-build.json").read_text())["commit"] != commit:
        raise ValueError("prepared release commit does not match landing")
    # Migrate the old service's paths once. Its existing server resolves these paths
    # for each request, so it serves the new release without killing an owned process.
    live = published / "live"
    if not live.is_symlink():
        legacy = published / "live-releases/legacy"
        legacy.mkdir(exist_ok=True)
        for target, name in (
            (published / "build/game", "game"),
            (published / "dist/tiles", "tiles"),
            (published / "build/layers", "layers"),
            (published / "textures", "textures"),
        ):
            if not target.is_symlink():
                if target.exists():
                    target.rename(legacy / name)
                target.symlink_to(os.path.relpath(live / name, target.parent))
        temporary = published / ".live-next"
        temporary.symlink_to(legacy, target_is_directory=True)
        os.replace(temporary, live)
    temporary = published / ".live-next"
    temporary.symlink_to(release, target_is_directory=True)
    os.replace(temporary, live)
    version = published.parent / "logs/live-version.txt"
    version.parent.mkdir(parents=True, exist_ok=True)
    pending = version.with_suffix(".tmp")
    pending.write_text(commit + "\n")
    os.replace(pending, version)
