"""Immutable precheck receipts and release snapshots consumed by the merge queue."""

import hashlib
import json

import live_build
import sandbox as s


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def artifact_hash(release):
    """Hash bytes and paths, including the release metadata, before publication."""
    result = hashlib.sha256()
    for path in sorted(release.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"verified release contains a symlink: {path}")
        if not path.is_file():
            continue
        result.update(str(path.relative_to(release)).encode() + b"\0")
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                result.update(chunk)
    return result.hexdigest()


def create(repo, published, work, gate, build, base="main"):
    head = s.git("rev-parse", "HEAD", cwd=repo)
    tree = s.git("rev-parse", "HEAD^{tree}", cwd=repo)
    main = s.git("rev-parse", base, cwd=repo)
    checks = {
        "gate": {
            row["name"]: row.get("input_hash")
            or digest({"tree": tree, "main": main, "check": row["name"], "status": row["status"]})
            for row in gate["stages"]
            if row["status"] == "PASS"
        },
        "live": build["input_hashes"],
    }
    release_id = digest({"tree": tree, "checks": checks})
    release = live_build.prepare(work, published, head, build, release_id)
    return {
        "version": 1,
        "head": head,
        "tree": tree,
        "main": main,
        "checks": checks,
        "release_id": release_id,
        "artifact_hash": artifact_hash(release),
    }


def validate(record, repo, published):
    if record.get("version") != 1:
        raise ValueError("precheck receipt missing or obsolete")
    if record["head"] != s.git("rev-parse", "HEAD", cwd=repo):
        raise ValueError("precheck head differs from worker branch")
    if record["tree"] != s.git("rev-parse", "HEAD^{tree}", cwd=repo):
        raise ValueError("precheck tree differs from worker branch")
    if record["main"] != s.git("rev-parse", "main", cwd=repo):
        raise ValueError("main moved since precheck")
    if record["release_id"] != digest({"tree": record["tree"], "checks": record["checks"]}):
        raise ValueError("precheck input hash record changed")
    release = published / "live-releases" / record["release_id"]
    if not release.is_dir() or artifact_hash(release) != record["artifact_hash"]:
        raise ValueError("prechecked release is missing or changed")
    if json.loads((release / "live-build.json").read_text())["commit"] != record["head"]:
        raise ValueError("prechecked release names another commit")
    return release
