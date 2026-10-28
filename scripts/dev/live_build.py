"""Build, check and publish the real office distribution for the integrator.

The code lane builds main's game bundle and ships the tile set the localhost game serves
(served.py): no refresh, tile-build or validators. The data lane rebuilds the tiles a changed
data root affects with main's tile code and carries the live game
bundle. Only the build stages decide whether a release is published: a batch whose layers,
tiles, tile format and seams or game bundle cannot be built is red. Every other stage (car
report, facade ground audit, tile-validate, facade baseline and Chrome) is advisory:
its failure is recorded in report["advisory"] and the release is published anyway.
"""

import contextlib
import filecmp
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import advisory as advisory_check
import advisory_chrome
import live_cache
import live_retention
import sandbox as s
import served
import spec_selection
import tile_store
from host_slots import integration_jobs, phase
from integration_chrome import browser_load, load_summary
from landing_class import C, data_parts
from live_layers import affected_collision_files, affected_tiles
from turku3d.region import quality


def tiles(published):
    """The playable L0 tiles the live build ships: the data root's playable-tiles.json."""
    return tuple(C.playable_tile_ids(published))


# Stages per build kind (integrator.py); `inert` builds nothing and re-points live.
STAGES = {
    # The code lane, for every batch that is not inert: main's game bundle (voice clips and the
    # address pool are part of it) with the served tile set.
    "code": ("game-build",),
    # Full runs build the same bundle; Chrome is selected only after publication.
    "full": ("game-build", "chrome"),
    # The data lane: the tiles a changed data root affects, with main's tile code; the
    # live game bundle is carried. Chrome runs what spec_selection picks: nothing unless the
    # playable tile list changed.
    "data": (
        "refresh",
        "tile-build",
        "tile-validate",
        "collision-checks",
        "car-report",
        "facade-ground-audit",
        "chrome",
    ),
    "inert": (),
}


def stages_for(kind, checks=True, *, post_publish=False):
    """The live stages a build of this kind runs: the one seam for stage selection.

    By default only build stages run. post_publish selects diagnostics instead;
    checks=False excludes diagnostics for base checks and bisection builds."""
    return tuple(
        stage
        for stage in STAGES[kind]
        if (stage not in advisory_check.BUILD_STAGES) == post_publish
        and (checks or stage in advisory_check.BUILD_STAGES)
    )


def parked_changed(previous, work):
    """Whether the parked-car layer differs from the previous release's."""
    return not previous or not same_tree(
        previous / "layers/traffic_static", work / "build/layers/traffic_static"
    )


def carry_car_report(previous, work):
    """The previous release's car result, copied for a data build with unchanged parked cars."""
    record = json.loads((previous / "live-build.json").read_text())
    if "car_validation" not in record:
        return None
    target = work / "reports/validate-cars.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(previous / record["car_validation"]["report"], target)
    return {
        **record["car_validation"],
        "report": "reports/validate-cars.json",
        "carried_from": record.get("verified_commit", record["commit"]),
    }


class StageFailed(ValueError):
    """A build stage failed; `stage` names it. The batch cannot be published."""

    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def approval_manifests(published):
    """SHA-256 of the approval root's MANIFEST.json and of the published release's copy.

    seed() replaces the release assembly with the approvals, so the two differ exactly
    when the approvals changed on disk after the published release was built.
    """

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    return {
        "published": sha(published / "live/textures/assembly/MANIFEST.json"),
        "current": sha(published / "facade-approvals/assembly/MANIFEST.json"),
    }


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
    if target.exists() or target.is_symlink():
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
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)


def _replace(source, target):
    """Make target a private clone of source (kept as it is when source is missing)."""
    if not source.exists():
        return
    if target.is_symlink():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)
    _clone(source, target)


def seed(work, published):
    """Seed a reused build sandbox from the live release: layers and textures only when
    missing (refresh updates them in place), the tiles and the game bundle every time (a
    build starts from the live tiles and ships the live game unless it builds its own), and
    the facade approvals."""
    for relative in ("build/layers", "textures"):
        _clone(published / relative, work / relative)
    for relative in ("dist/tiles", "build/game"):
        _replace(published / relative, work / relative)
    # The data root's tile list is authoritative; a sandbox list would shadow it.
    (work / C.PLAYABLE_TILES_FILE).unlink(missing_ok=True)
    approve(work, published)


def approve(work, published):
    """Replace the work's facade assembly with the approval root's."""
    approvals = published / "facade-approvals/assembly"
    if approvals.is_dir():
        # The approvals are authoritative: a retired facade must leave the release too,
        # so the assembly is replaced rather than merged over the published copy.
        target = work / "textures/assembly"
        _detach(target, work)
        if target.exists():
            shutil.rmtree(target)
        with s.lock(approvals / ".export.lock"):
            shutil.copytree(approvals, target)


def _detach(path, work):
    """Replace linked components below work with private clones before writing through them."""
    current = work
    for part in path.relative_to(work).parts:
        current = current / part
        if not current.is_symlink():
            continue
        private = current.with_name(current.name + ".seed-local")
        if private.exists() or private.is_symlink():
            shutil.rmtree(private)
        _clone(current, private)
        current.unlink()
        private.rename(current)


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


def inspect(manifest_path, facade_count, minimum, listed, reason=None):
    """Per listed tile: geometry present, ground textures bound and the 8 MB wire cap."""
    manifest = json.loads(manifest_path.read_text())
    for tile in listed:
        entry = manifest["visual"].get(tile)
        if entry is None:
            raise ValueError(f"{tile}: listed in playable-tiles.json but not assembled")
        if entry.get("tris", 0) < 1000:
            raise ValueError(f"{tile}: office geometry is missing; Chrome tests would skip")
        if entry.get("ground_texture_bytes", 0) <= 0:
            raise ValueError(f"{tile}: ground textures are unbound")
        if entry.get("wire_bytes", 8_000_001) >= 8_000_000:
            raise ValueError(f"{tile}: L0 wire size exceeds 8 MB")
    if not manifest.get("spawn"):
        raise ValueError("live build has no spawn")
    return {
        "tiles": list(listed),
        "facades_bound": facade_count,
        "facades_baseline": minimum,
        "facade_regression_reason": reason if facade_count < minimum else None,
    }


def store_tiles(repo, work, published, built, facades_bound, seeded):
    """Offer the unverified tile build (seeded: its seed's {"time", "parts"}) to the localhost game (tile_store)."""
    commit = s.git("rev-parse", "HEAD", cwd=repo)
    tile_store.put(
        published,
        work / "dist/tiles",
        tile_store.key(repo, commit, published),
        commit,
        "integrator",
        built,
        {"facades_bound": facades_bound},
        seeded=seeded,
    )


def build(
    repo,
    work,
    published,
    env,
    log_path,
    reason=None,
    previous_release=None,
    kind="code",
    join=None,
    joined=(),
    chrome=None,
    timings=None,
    checks=True,
    release_report=None,
    cancelled=None,
):
    """Run the kind's live stages on repo; the caller holds the integration host reservation.

    Build stages run before publication; release_report selects only post-publish stages
    using the existing artifacts. cancelled preempts owned command groups for a newer landing.
    A build stage that fails raises StageFailed; an advisory stage that fails is appended to
    report["advisory"] ({"stage", "detail", "log"}, plus "specs" for Chrome and "checks" for
    tile-validate) and the build goes on. checks=False runs the build stages only.

    Code/full builds ship served tiles; data builds seed the previous release and rebuild
    affected tiles. Shipped/rebuilt provenance goes into report["shipped"]/report["tiles_built"].
    Post-publish car reports carry the previous result when parked cars are unchanged.

    join is offered only while the build is free to restart: once, before the first stage
    that is not a cache hit (refresh included), so a stage that runs for real is never
    discarded and the build is frozen from then on. It is called with the stages this build
    restored from the cache (what a restart repeats). When it moved repo to a newer main it
    returns {"commit", "kind", "reason"}; the build then starts over on that commit,
    appends to the same log and may join again; the caller bounds joins and restarts. The
    update's "chrome" replaces the Chrome plan.

    timings, when given, accumulates every stage's seconds across restarts and failures (the
    batch's cost); report["seconds"] holds only this build's.

    chrome is the spec_selection plan for the Chrome stage (None runs the full suite);
    passing specs are cached per spec and project, and report["chrome"] records what ran.
    """
    stages = stages_for(kind, checks, post_publish=release_report is not None)
    chrome = chrome or spec_selection.full_plan("no selection given")
    minimum = baseline(published)
    advisory = list(release_report.get("advisory", [])) if release_report else []
    report = release_report
    seeded = None
    if release_report is not None:
        listed = tuple(report["tiles"])
        data_time = report["data_time"]
    elif "tile-build" in stages:
        seeded = {"time": time.time(), "parts": data_parts(published)}
        seed(work, published)
        listed = tiles(published)
        data_time = seeded["time"]
    else:
        shipped = served.ship(repo, work, published)
        data_time = tile_store.base_time(shipped["dir"])
        # The release carries the served tiles; tiles listed since are the data lane's.
        listed = tuple(shipped["tile_commits"])
        report = inspect(
            work / "dist/tiles/manifest.json", shipped["facades_bound"], minimum, listed, reason
        )
        if drop := advisory_check.facade_drop(report):
            advisory.append(drop)
        report["shipped"] = {
            name: value for name, value in shipped.items() if name not in ("dir", "facades_bound")
        }
    if release_report is not None:
        report["advisory"] = advisory
    durations = {}
    timings = {} if timings is None else timings
    cache_hits = []
    input_hashes = {}
    browser_loads = {}
    chrome_stats = None
    previous = ""
    cache_enabled = (repo / ".git").exists()
    commands = [
        ("refresh", ["make", "refresh"], repo),
        (
            "tile-build",
            ["make", "tile-build", "AREA=playable-tiles", "T=" + " ".join(listed)],
            repo,
        ),
        (
            "tile-validate",
            [
                "make",
                "tile-validate",
                "T=" + " ".join(listed),
                "TILE_WALKERS=128",
                "VALIDATOR_REQUIRE=format,seams",
                "VALIDATOR_ARGS=--checks format,seams",
            ],
            repo,
        ),
        (
            "collision-checks",
            [
                "make",
                "tile-validate",
                "T=" + " ".join(listed),
                "TILE_WALKERS=128",
                "VALIDATOR_REQUIRE=buildings,curbs",
                "VALIDATOR_ARGS=--checks buildings,curbs --walk-seconds 2",
            ],
            repo,
        ),
        (
            "car-report",
            ["node", "src/cli.ts", "--checks", "cars", "--require", "cars", "--workers", "4"],
            repo / "tools/validator",
        ),
        (
            "facade-ground-audit",
            ["bash", "pipeline/env/run.sh", "python", "-m", "turku3d.package.facade_ground_audit"],
            repo,
        ),
        ("game-build", ["make", "game-build"], repo),
        # The command line is logged by spec_selection.run once the selection is known.
        ("chrome", ["playwright", "test", "-c", spec_selection.TILE_CONFIG], repo / "game"),
    ]
    scopes = {}
    with log_path.open("a" if joined else "w") as log:
        for stage, command, cwd in commands:
            if stage not in stages:
                continue
            if cancelled and cancelled():
                raise InterruptedError("newer landing waiting; advisory checks cut short")
            if (
                stage == "car-report"
                and kind == "data"
                and not parked_changed(previous_release, work)
            ):
                if carried := carry_car_report(previous_release, work):
                    report["car_validation"] = carried
                log.write(f"\n== {stage}: parked cars unchanged; result carried forward ==\n")
                continue
            if stage == "tile-build":
                prior = previous_release
                if prior:
                    selected = affected_tiles(prior, work, listed)
                    # Facade and library texture changes affect tile packing globally.
                    if selected and same_tree(prior / "textures", work / "textures"):
                        command = [*command[:3], "T=" + " ".join(selected)]
                        scopes[stage] = selected
            if stage in ("tile-validate", "collision-checks", "car-report"):
                selected = scopes.get("tile-build") or listed
                files = affected_collision_files(previous_release, work, selected)
                if stage in ("tile-validate", "collision-checks"):
                    command[2] = "T=" + " ".join(selected)
                    if files:
                        command.append("VALIDATOR_INPUTS=" + " ".join(map(str, files)))
                else:
                    command.extend(("--out", str(work / "reports/validate-cars.json")))
                    layers = work / "build/layers"
                    if layers.is_dir():
                        command.extend(("--layers", str(layers)))
                    command.extend(map(str, files))
                scopes[stage] = tuple(str(path.relative_to(work)) for path in files)
            start = time.monotonic()
            log.write(f"\n== {stage}: {' '.join(command)} ==\n")
            log.flush()
            # Chrome results are cached per spec by spec_selection, not per stage.
            key = (
                live_cache.digest(repo, work, published, stage, previous, scopes.get(stage))
                if cache_enabled and stage not in ("chrome", "collision-checks")
                else None
            )
            if key:
                input_hashes[stage] = key
            cached = (
                live_cache.restore(published, work, stage, key)
                if key and stage != "car-report"
                else None
            )
            if cached is not None:
                log.write(f"cache hit {key}\n")
                durations[stage] = 0.0
                timings.setdefault(stage, 0.0)
                cache_hits.append(stage)
                if stage == "tile-build":
                    report = inspect(
                        work / "dist/tiles/manifest.json",
                        cached["facades_bound"],
                        minimum,
                        listed,
                        reason,
                    )
                    if drop := advisory_check.facade_drop(report):
                        advisory.append(drop)
                    store_tiles(
                        repo,
                        work,
                        published,
                        scopes.get(stage) or listed,
                        cached["facades_bound"],
                        seeded,
                    )
                if stage == "facade-ground-audit":
                    report["facade_ground_audit"] = cached["facade_ground_audit"]
                previous = key
                continue
            if join:
                callback, join = join, None
                if update := callback(tuple(cache_hits)):
                    log.write(f"\n== join: rebuilding {update['commit']} as {update['kind']} ==\n")
                    log.flush()
                    return build(
                        repo,
                        work,
                        published,
                        env,
                        log_path,
                        update["reason"],
                        previous_release,
                        update["kind"],
                        callback,
                        (*joined, update["commit"]),
                        update.get("chrome", chrome),
                        timings,
                        checks,
                    )
            run_env = {**env, "TILE_DIST": str(work / "dist/tiles")}
            run_env["VITE_PUBLIC_RELEASE"] = "1" if stage == "game-build" else ""
            load_rows = work / "reports/browser-load" / f"{stage}.jsonl"
            load_before = os.getloadavg()
            if stage in ("tile-validate", "collision-checks"):
                (work / "reports/validate-aoi.json").unlink(missing_ok=True)
            if stage.startswith("chrome"):
                run_env["TURKU3D_JOBS"] = str(integration_jobs("browser"))
                load_rows.parent.mkdir(parents=True, exist_ok=True)
                load_rows.unlink(missing_ok=True)
                run_env["TURKU3D_BROWSER_LOAD_LOG"] = str(load_rows)
            # Agents' Chrome suites queue while this phase is marked (host_slots.admissible).
            with phase(stage) if stage.startswith("chrome") else contextlib.nullcontext():
                if stage == "chrome":
                    code, output, chrome_stats = spec_selection.run(
                        chrome,
                        root=repo,
                        env=run_env,
                        bundle_dir=work / "build/game",
                        tiles_manifest=work / "dist/tiles/manifest.json",
                        runner=lambda command, cwd, env: advisory_chrome.run(
                            command, cwd=cwd, env=env, cancelled=cancelled
                        ),
                        log=log,
                    )
                    proc = subprocess.CompletedProcess(command, code, output)
                else:
                    proc = s.run_group(command, cwd=cwd, env=run_env, cancelled=cancelled)
            log.write(proc.stdout)
            durations[stage] = round(time.monotonic() - start, 3)
            timings[stage] = round(timings.get(stage, 0.0) + durations[stage], 3)
            load = None
            if stage.startswith("chrome"):
                load = browser_load(load_rows, load_before)
                browser_loads[stage] = load
                chrome_stats["host_load"] = {
                    "start": load["host_load_start"],
                    "end": load["host_load_end"],
                }
                log.write(f"{stage} load: {json.dumps(load)}\n")
                log.write(f"{stage} selection: {json.dumps(chrome_stats)}\n")
            log.flush()
            conditions = load_summary(load) if load else f"host load {s.host_load()}"
            detail = f"{s.failure_detail(proc.stdout)}; {conditions}: {log_path}"
            failure, extra = None, {}
            if stage == "car-report":
                if report is None:
                    raise ValueError("tile-build report is unavailable before car validation")
                try:
                    report["car_validation"] = advisory_check.car_report(
                        work / "reports/validate-cars.json", proc.returncode
                    )
                except ValueError as exc:
                    failure = f"{exc}; {detail}"
            elif stage in ("tile-validate", "collision-checks"):
                path = work / "reports/validate-aoi.json"
                try:
                    failing = advisory_check.tile_checks(path, proc.returncode)
                except ValueError as exc:
                    if stage == "tile-validate":
                        raise StageFailed(stage, f"{exc}; {detail}") from exc
                    failing = []
                    failure = f"{exc}; {detail}"
                if failing:
                    failure = f"tile-validate checks failed: {', '.join(failing)}; {detail}"
                    extra = {"checks": failing}
            elif proc.returncode:
                failure = f"{stage} failed ({proc.returncode}); {detail}"
                if stage in advisory_check.BUILD_STAGES:
                    raise StageFailed(stage, failure)
                if stage == "chrome":
                    extra = {"specs": chrome_stats["failed"]}
            if failure:
                advisory.append(
                    {"stage": stage, "detail": failure[-1000:], "log": str(log_path), **extra}
                )
                log.write(f"advisory: {stage} failed; the release is published anyway\n")
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
                report = inspect(
                    work / "dist/tiles/manifest.json", facade_count, minimum, listed, reason
                )
                if drop := advisory_check.facade_drop(report):
                    advisory.append(drop)
                if cache_enabled:
                    store_tiles(
                        repo, work, published, scopes.get(stage) or listed, facade_count, seeded
                    )
            if stage == "facade-ground-audit" and not failure:
                report["facade_ground_audit"] = json.loads(proc.stdout.splitlines()[-1])
            # A failed advisory stage is not cached: the next build with its inputs reruns it.
            if key and stage != "car-report" and not failure:
                live_cache.store(
                    published,
                    work,
                    stage,
                    key,
                    {"facades_bound": report["facades_bound"]}
                    if stage == "tile-build"
                    else {"facade_ground_audit": report["facade_ground_audit"]}
                    if stage == "facade-ground-audit"
                    else {},
                )
                previous = key
            elif key:
                previous = key
    if "tile-build" in stages:
        report["tiles_built"] = list(scopes.get("tile-build") or listed)
    report["data_time"] = data_time  # when the shipped tiles' build read the data root
    if release_report is None:
        report["seconds"] = durations
        report["cache_hits"] = cache_hits
        report["input_hashes"] = input_hashes
    else:
        report["advisory_seconds"] = durations
    report["browser_load"] = browser_loads
    report["chrome"] = chrome_stats
    report["advisory"] = advisory
    return report


def post_publish(repo, work, published, env, log, report, *, kind, previous, chrome, cancelled):
    """Run diagnostics on the already published build, without reseeding its artifacts."""
    return build(
        repo,
        work,
        published,
        env,
        log,
        previous_release=previous,
        kind=kind,
        chrome=chrome,
        release_report=report,
        cancelled=cancelled,
    )


def prepare(work, published, commit, report, release_id=None):
    """Copy the built release into live-releases; publish() then moves the live link."""
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
                if asset.is_file():
                    os.utime(asset, None)
    if "car_validation" in report:
        car_report = work / report["car_validation"]["report"]
        if not car_report.is_file():
            raise ValueError(f"verified car report is missing: {car_report}")
        (release / "reports").mkdir()
        shutil.copy2(car_report, release / "reports/validate-cars.json")
    (release / "live-build.json").write_text(
        json.dumps({"commit": commit, **report}, indent=2) + "\n"
    )
    return release


def repoint(published, start, end):
    """Publish end with the content of start's live release; an inert batch ships nothing new.

    The release directory is an APFS clone of the live one whose record names end, keeps
    `verified_commit` (the commit whose build verified the content) and `repointed_from`.
    """
    previous = (published / "live").resolve()
    record = json.loads((previous / "live-build.json").read_text())
    if record["commit"] != start:
        raise ValueError(
            f"live release is {record['commit'][:12]}, not the batch base {start[:12]}"
        )
    release = published / "live-releases" / end
    if release.exists():
        shutil.rmtree(release)
    _clone(previous, release)
    record.update(
        commit=end, verified_commit=record.get("verified_commit", start), repointed_from=start
    )
    (release / "live-build.json").write_text(json.dumps(record, indent=2) + "\n")
    publish(published, end)
    return release


def prepared(published, commit, release_id=None):
    """Return a complete, link-free prepared release for commit, or raise."""
    release = published / "live-releases" / (release_id or commit)
    if not (release / "live-build.json").exists():
        raise ValueError(f"prepared release is missing: {release}")
    record = json.loads((release / "live-build.json").read_text())
    if record["commit"] != commit:
        raise ValueError("prepared release commit does not match landing")
    if "car_validation" in record and not (release / record["car_validation"]["report"]).is_file():
        raise ValueError("prepared release car report is missing")
    for name in ("game", "tiles", "layers", "textures"):
        directory = release / name
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"prepared release directory is missing or linked: {directory}")
        if any(asset.is_symlink() for asset in directory.rglob("*")):
            raise ValueError(f"prepared release contains a symlink: {directory}")
    return release


def publication_preflight(published, commit, release_id=None):
    """Check release and stable path layout before advancing main."""
    release = prepared(published, commit, release_id)
    live = published / "live"
    if live.exists() and not live.is_symlink():
        raise ValueError(f"live path is not a symlink: {live}")
    if live.is_symlink() and not live.resolve().is_dir():
        raise ValueError(f"live link is invalid: {live}")
    for target, name in (
        (published / "build/game", "game"),
        (published / "dist/tiles", "tiles"),
        (published / "build/layers", "layers"),
        (published / "textures", "textures"),
    ):
        if target.is_symlink():
            expected = os.path.abspath(live / name)
            actual = os.path.abspath(target.parent / os.readlink(target))
            if actual != expected:
                raise ValueError(f"stable path does not point through live: {target}")
        elif live.is_symlink():
            raise ValueError(f"stable path is not a link: {target}")
        elif target.exists() and not target.is_dir():
            raise ValueError(f"legacy stable path is not a directory: {target}")
        if (
            not live.is_symlink()
            and target.exists()
            and (published / "live-releases/legacy" / name).exists()
        ):
            raise ValueError(f"legacy release target already exists: {target}")
    return release


def publish(published, commit, release_id=None):
    """Swap one release link; stable game/tile paths resolve through that link."""
    release = publication_preflight(published, commit, release_id)
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
    previous = live.readlink()
    temporary = published / ".live-next"
    temporary.symlink_to(release, target_is_directory=True)
    os.replace(temporary, live)
    try:
        for target, name in (
            (published / "build/game", "game"),
            (published / "dist/tiles", "tiles"),
            (published / "build/layers", "layers"),
            (published / "textures", "textures"),
        ):
            if not target.is_dir() or target.resolve() != (release / name).resolve():
                raise ValueError(f"stable path does not resolve into release: {target}")
        version = published.parent / "logs/live-version.txt"
        version.parent.mkdir(parents=True, exist_ok=True)
        pending = version.with_suffix(".tmp")
        pending.write_text(commit + "\n")
        os.replace(pending, version)
    except Exception:
        temporary.symlink_to(previous, target_is_directory=True)
        os.replace(temporary, live)
        raise
    if (release / "tiles" / quality.DIST_LEDGER).is_file():
        # The data root's tile-quality.json: every tile this verified release ships, history kept.
        quality.publish(published, release, commit)
    live_retention.after_publish(published, release, previous)
