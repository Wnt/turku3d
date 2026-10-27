"""Browser spec selection from gate.toml's game-e2e map, a per-spec pass cache and the
full-suite schedule.

`select(paths, landing_class)` picks the Playwright spec files a change can affect: a
changed spec selects itself, a changed test helper, sibling data file read by name or
directly imported module selects the specs that import it, a changed Playwright config
selects its whole test directory, and every other trigger path (`triggers`, plus
`tile_triggers` for pipeline code that changes the assembled tiles) selects the specs
matching its `[[stages.related]]` rows (`tags` declared on the specs, or `specs` globs).
A trigger path with no row and no import route selects every spec and says so.

Classes: code runs that selection on webgpu, both projects when a `both_projects` path
changed; data runs only `@streaming` specs, and only when the playable tile list changed;
inert runs nothing. A batch's class is the widest of its landings (`batch_class`).

`run(plan, ...)` runs the uncached (spec, project) pairs and caches each passing pair in
`~/turku3d/wt/.registry/spec-results/` (newest 500), keyed by the game bundle tree, the
tile manifest without its build time, the spec with its test-side imports, the config
with its imports, web-server script and Playwright version, and the project.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import time
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STAGE = "game-e2e"
# Playwright config (in game/) -> its test directory (repo-relative).
CONFIGS = {
    "playwright.config.ts": "game/tests/e2e",
    "playwright.tile.config.ts": "game/tests/e2e-tile",
}
TILE_CONFIG = "playwright.tile.config.ts"
PROJECTS = ("webgpu", "webgl")
CLASSES = ("inert", "data", "code")
KEEP = 500

_TAG = re.compile(r"""['"`]\s*(@[a-z][a-z0-9-]*)(?![\w/-])""")
_IMPORT = re.compile(r"""(?:\bfrom\s+|\bimport\s*\(\s*|\bimport\s+)['"](\.[^'"]+)['"]""")
# A sibling data file read by name, e.g. readFileSync(path.join(dirname, 'viewpoints.json')).
_SIBLING = re.compile(r"""['"]([\w-][\w.-]*\.(?:json|ts|mjs))['"]""")
_SCRIPT = re.compile(r"""(\.\./[\w./-]+\.mjs)""")
_SUFFIXES = ("", ".ts", ".mts", ".mjs", ".js", "/index.ts")


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def gate_config(root=REPO):
    return tomllib.loads((Path(root) / "scripts/dev/gate.toml").read_text())


def e2e_stage(config):
    return next(stage for stage in config["stages"] if stage["name"] == STAGE)


def spec_files(root=REPO):
    """Repo-relative spec files of every config's test directory."""
    root = Path(root)
    return sorted(
        str(path.relative_to(root))
        for directory in CONFIGS.values()
        for path in (root / directory).rglob("*.spec.ts")
    )


def spec_tags(root, spec):
    return set(_TAG.findall((Path(root) / spec).read_text()))


def imports(root, path):
    """Repo-relative files that path imports with relative specifiers."""
    root = Path(root)
    source = root / path
    try:
        text = source.read_text()
    except (OSError, UnicodeDecodeError):
        return set()
    found = {
        str((source.parent / name).relative_to(root))
        for name in _SIBLING.findall(text)
        if (source.parent / name).is_file()
    }
    for specifier in _IMPORT.findall(text):
        base = source.parent / specifier
        for suffix in _SUFFIXES:
            candidate = Path(os.path.normpath(f"{base}{suffix}"))
            if candidate.is_file():
                found.add(str(candidate.relative_to(root)))
                break
    return found


def closure(root, path):
    """path plus its local imports, followed through test helpers and dev tools only.

    Game modules a spec imports directly are included but not followed: the bundle
    carries them to every spec, and the tag map covers that route.
    """
    seen, pending = set(), [path]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        if current == path or matches(current, ("game/tests/*", "tools/dev/*")):
            pending.extend(imports(root, current) - seen)
    return seen


def config_files(root, config):
    """The Playwright config, its local imports and the dev scripts its web server runs."""
    root = Path(root)
    name = f"game/{config}"
    files = closure(root, name)
    for script in _SCRIPT.findall((root / name).read_text()):
        candidate = Path(os.path.normpath(root / "game" / script))
        if candidate.is_file():
            files |= closure(root, str(candidate.relative_to(root)))
    return files


def batch_class(classes):
    """The widest class of a batch's landings: code > data > inert; none known is code."""
    known = [value for value in classes if value in CLASSES]
    if not known or len(known) != len(classes):
        return "code"
    return max(known, key=CLASSES.index)


def full_plan(reason):
    """Every spec of the run's config on both projects."""
    return {
        "specs": "all",
        "projects": list(PROJECTS),
        "reason": reason,
        "full_suite": True,
    }


def select(changed_paths, landing_class, *, tiles_changed=False, root=REPO, config=None):
    """{"specs", "projects", "reason"} for a change of landing_class (code, data or inert).

    Data landings run no browser spec except @streaming when the playable tile list
    changed; inert landings run none. Both projects run only when a path listed in the
    stage's `both_projects` changed.
    """
    root = Path(root)
    config = config or gate_config(root)
    stage = e2e_stage(config)
    prose = config["gate"].get("prose", ["*.md"])
    paths = sorted({p for p in changed_paths if not matches(p, prose)})
    specs = spec_files(root)
    tags = {spec: spec_tags(root, spec) for spec in specs}
    chosen, wanted, reasons, direct = set(), set(), [], []
    if landing_class == "code":
        importers = {spec: closure(root, spec) for spec in specs}
        for path in paths:
            if not matches(path, [*stage["triggers"], *stage.get("tile_triggers", ())]):
                continue
            if path.startswith("game/tests/") and not (root / path).is_file():
                # A deleted spec or helper: its importers changed in the same diff.
                continue
            hit = {spec for spec in specs if path in importers[spec]}
            rows = [row for row in stage.get("related", []) if matches(path, row["paths"])]
            for row in rows:
                wanted.update(row.get("tags", ()))
                hit |= {spec for spec in specs if matches(spec, row.get("specs", ()))}
            directory = CONFIGS.get(path.removeprefix("game/"))
            if directory:
                hit |= {spec for spec in specs if spec.startswith(directory + "/")}
            if not hit and not rows:
                # A trigger without a map row: every spec, until gate.toml maps the path.
                hit = set(specs)
                reasons.append(f"{path}: unmapped trigger, all specs")
            elif hit:
                direct.append(f"{path} ({len(hit)})")
            chosen |= hit
    elif landing_class not in ("data", "inert"):
        raise ValueError(f"unknown landing class: {landing_class}")
    if tiles_changed and landing_class != "inert":
        wanted.add("@streaming")
        reasons.append("playable tile list changed")
    chosen |= {spec for spec in specs if tags[spec] & wanted}
    both = landing_class == "code" and any(
        matches(p, stage.get("both_projects", ())) for p in paths
    )
    if direct:
        reasons.insert(0, "paths " + ", ".join(direct[:5]) + (" ..." if len(direct) > 5 else ""))
    if wanted:
        reasons.insert(0, "tags " + " ".join(sorted(wanted)))
    if not chosen:
        reasons.append(f"{landing_class} landing: no browser spec matches")
    return {
        "specs": sorted(chosen),
        "projects": list(PROJECTS) if both else ["webgpu"],
        "reason": "; ".join(reasons),
        "full_suite": False,
    }


# Per-spec pass cache -------------------------------------------------------------------


def cache_dir():
    root = Path(os.environ.get("TURKU3D_WT_ROOT", Path.home() / "turku3d/wt")).expanduser()
    return root / ".registry/spec-results"


def _sha(chunks):
    h = hashlib.sha256()
    for chunk in chunks:
        h.update(chunk if isinstance(chunk, bytes) else chunk.encode())
        h.update(b"\0")
    return h.hexdigest()


def tree_hash(directory):
    directory = Path(directory)
    files = sorted(p for p in directory.rglob("*") if p.is_file()) if directory.is_dir() else []
    return _sha(part for p in files for part in (str(p.relative_to(directory)), p.read_bytes()))


def manifest_hash(manifest):
    """Tile manifest content without its build timestamp; tile bytes are in its sha256s."""
    data = json.loads(Path(manifest).read_text())
    data.pop("built", None)
    return _sha([json.dumps(data, sort_keys=True)])


def files_hash(root, files):
    root = Path(root)
    return _sha(part for name in sorted(files) for part in (name, (root / name).read_bytes()))


def playwright_version(root):
    package = Path(root) / "game/node_modules/@playwright/test/package.json"
    return json.loads(package.read_text())["version"] if package.is_file() else "missing"


def spec_key(*, bundle, tiles, spec, config, project):
    return _sha(["spec-result/v1", bundle, tiles, spec, config, project])


def hit(directory, key):
    return (directory / f"{key}.json").is_file()


def store(directory, key, row):
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / f".{key}.{os.getpid()}.tmp"
    temp.write_text(json.dumps(row, sort_keys=True))
    os.replace(temp, directory / f"{key}.json")
    prune(directory)


def prune(directory, keep=KEEP):
    """Keep the newest `keep` results by modification time."""
    entries = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in entries[keep:]:
        stale.unlink(missing_ok=True)


def outcomes(report, root):
    """{(repo-relative spec, project): passed} from a Playwright JSON report."""
    results = {}
    base = Path(report["config"]["rootDir"])

    def walk(suite):
        for spec in suite.get("specs", ()):
            name = str((base / spec["file"]).resolve().relative_to(Path(root).resolve()))
            for test in spec["tests"]:
                pair = (name, test["projectName"])
                ok = test["status"] in ("expected", "flaky", "skipped")
                results[pair] = results.get(pair, True) and ok
        for child in suite.get("suites", ()):
            walk(child)

    for suite in report.get("suites", ()):
        walk(suite)
    return results


def run(
    plan,
    *,
    root,
    env,
    bundle_dir,
    tiles_manifest,
    runner,
    config=TILE_CONFIG,
    log=None,
    cache=None,
):
    """Run the plan's uncached specs on its projects; cache passing (spec, project) pairs
    in `cache` (default cache_dir()).

    runner(command, cwd, env) returns an object with returncode and stdout. Returns
    (returncode, output, stats) with selected, run, cached and skipped `project:spec`
    lists (skipped = specs of this config the plan did not select) and wall seconds.
    """
    root = Path(root)
    cache = Path(cache) if cache else cache_dir()
    started = time.monotonic()
    directory = CONFIGS[config]
    available = [spec for spec in spec_files(root) if spec.startswith(directory + "/")]
    specs = available if plan["specs"] == "all" else [s for s in plan["specs"] if s in available]
    pairs = [(spec, project) for project in plan["projects"] for spec in specs]
    bundle = tree_hash(bundle_dir)
    tiles = manifest_hash(tiles_manifest) if Path(tiles_manifest).is_file() else "missing"
    settings = _sha([files_hash(root, config_files(root, config)), playwright_version(root)])
    keys = {
        (spec, project): spec_key(
            bundle=bundle,
            tiles=tiles,
            spec=files_hash(root, closure(root, spec)),
            config=settings,
            project=project,
        )
        for spec, project in pairs
    }
    cached = [pair for pair in pairs if hit(cache, keys[pair])]
    pending = [pair for pair in pairs if pair not in cached]
    groups = {}
    for project in plan["projects"]:
        names = tuple(spec for spec, p in pending if p == project)
        if names:
            groups.setdefault(names, []).append(project)
    code, output, ran = 0, [], []
    report_path = Path(env.get("TURKU3D_WORK", root)) / "reports/spec-results.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    for names, projects in groups.items():
        command = [
            str(root / "game/node_modules/.bin/playwright"),
            "test",
            "-c",
            config,
            "--reporter=list,json",
            *(os.path.relpath(root / name, root / "game") for name in names),
            *(arg for project in projects for arg in ("--project", project)),
        ]
        report_path.unlink(missing_ok=True)
        output.append("$ " + " ".join(command))
        if log:
            log.write(output[-1] + "\n")
            log.flush()
        proc = runner(
            command, cwd=root / "game", env={**env, "PLAYWRIGHT_JSON_OUTPUT_FILE": str(report_path)}
        )
        output.append(proc.stdout)
        code = code or proc.returncode
        try:
            results = outcomes(json.loads(report_path.read_text()), root)
        except (OSError, ValueError, KeyError):
            results = {}
        for pair in ((name, project) for project in projects for name in names):
            ran.append(pair)
            if results.get(pair):
                store(cache, keys[pair], {"spec": pair[0], "project": pair[1], "time": time.time()})
    label = [f"{project}:{spec}" for spec, project in pairs]
    stats = {
        "full_suite": bool(plan.get("full_suite")),
        "reason": plan["reason"],
        "selected": label,
        "run": [f"{project}:{spec}" for spec, project in ran],
        "cached": [f"{project}:{spec}" for spec, project in cached],
        "skipped": [
            f"{project}:{spec}"
            for project in PROJECTS
            for spec in available
            if (spec, project) not in pairs
        ],
        "seconds": round(time.monotonic() - started, 3),
    }
    return code, "\n".join(output), stats
