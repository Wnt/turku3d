"""Publish main's game with the tiles the localhost game serves to GitHub Pages with serialized, ordered publication.

`publish-pages` (`--from-served`, the default) ships main's production bundle with the served
tile set (served.release: the live release when it already is exactly that); `--release DIR`
ships a specific release dir. The integrator publishes each live release it builds.

The deploy repository named in `$TURKU3D_DATA/publish/pages.json`
(`{"repo": "owner/name", "branch": "gh-pages", "region": {...}}`) only ever holds the latest
release: `game/` from the release, `tiles/` filtered to the configured region (pages_region.py),
the site files in `pages-site/` and `.nojekyll`.
Each publish fetches the remote head under a shared local lock and appends a commit.
Older code or tile inputs cannot replace a newer release. No source code, pipeline data or
street imagery is published: every bound facade must be generated-style. The commit message is the release notes: the commits since
the previous Pages release, the advisory checks the release failed (known-broken) and the
region: the shipped tiles and each excluded tile with the criterion it failed.

    scripts/dev/publish-pages --plan     # print the region selection of the live release; publishes nothing
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pages_region
import sandbox
import tile_store

CONFIG = "publish/pages.json"
# The last release this data root pushed to Pages; the next notes list commits since it.
STATE = "publish/pages-last.json"
REPO = Path(__file__).resolve().parents[2]
NOTE_COMMITS = 40
SITE = Path(__file__).resolve().parent / "pages-site"
PUSH_TIMEOUT = 900


class PhotoFacades(ValueError):
    """The release binds facade maps that are not generated-style."""


def config(published):
    """The deploy target, or None when this data root does not publish to Pages."""
    path = Path(published) / CONFIG
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    if not isinstance(data.get("repo"), str) or "/" not in data["repo"]:
        raise ValueError(f"{path}: repo must be 'owner/name'")
    return {
        "repo": data["repo"],
        "branch": data.get("branch", "gh-pages"),
        "region": pages_region.region(data),
    }


def generated(meta):
    """Facade maps allowed in a public release: generated-style maps, maps derived from them and
    fast-pass maps.

    A derived map (`derived`, `derived-neighbour`) is cut from a generated map's style record and
    carries no street imagery. A fast map (`turkutex.facade_fast`) is drawn procedurally from a
    recorded description of a few parameters and has no reference pixels. A photo-derived map has
    a `method` and no generated provenance.
    """
    if meta.get("method"):
        return False
    if meta.get("source_mode") == "generate":
        return bool(meta.get("generated_qa"))
    if meta.get("source_mode") == "fast":
        return bool(meta.get("fast_description")) and not meta.get("references")
    return meta.get("source_mode") in ("derived", "derived-neighbour") and bool(
        meta.get("derived_from")
    )


def check_facades(release):
    """Every bound facade of the release's texture assembly must be generated-style."""
    release = Path(release)
    record = json.loads((release / "live-build.json").read_text())
    assembly = release / "textures/assembly"
    manifest = assembly / "MANIFEST.json"
    if not manifest.is_file():
        if record.get("facades_bound"):
            raise PhotoFacades(f"{manifest} is missing but the release binds facades")
        return 0
    bound = json.loads(manifest.read_text())["bound"]
    if record.get("facades_bound", 0) > len(bound):
        raise PhotoFacades(
            f"live-build.json binds {record['facades_bound']} facades, the manifest lists {len(bound)}"
        )
    offending = []
    for facade_id in bound:
        building, index = facade_id.rsplit("_", 1)
        sidecar = assembly / "facades" / building / f"{index}.json"
        if not sidecar.is_file() or not generated(json.loads(sidecar.read_text())):
            offending.append(facade_id)
    if offending:
        raise PhotoFacades(
            f"{len(offending)} bound facades are not generated-style "
            f"(e.g. {', '.join(offending[:5])}); refusing to publish {release}"
        )
    return len(bound)


def site_url(repo):
    owner, name = repo.split("/")
    return f"https://{owner.lower()}.github.io/{name}/"


def assemble(release, target, repo, branch, selection):
    """Write the deploy tree: the release game, its tiles filtered to the selection and the site files."""
    release, target = Path(release), Path(target)
    for name in ("game", "tiles"):
        if not (release / name).is_dir():
            raise ValueError(f"release has no {name}/: {release}")
    shutil.copytree(release / "game", target / "game")
    pages_region.write_tiles(release / "tiles", target / "tiles", selection)
    values = {"{{url}}": site_url(repo), "{{repo}}": repo, "{{branch}}": branch}
    for path in SITE.iterdir():
        text = path.read_text()
        for key, value in values.items():
            text = text.replace(key, value)
        (target / path.name).write_text(text)
    (target / ".nojekyll").write_text("")
    return sum(path.stat().st_size for path in target.rglob("*") if path.is_file())


def commit_lines(since, commit, repo=REPO):
    """`<sha12> <subject>` per first-parent commit in since..commit (at most NOTE_COMMITS)."""
    try:
        log = git("log", "--first-parent", "--format=%H %s", f"{since}..{commit}", cwd=repo)
    except (subprocess.SubprocessError, OSError):
        return [f"  (commit log unavailable for {since[:12]}..{commit[:12]})"]
    lines = [f"  {line[:12]} {line[41:]}" for line in log.splitlines()]
    if len(lines) > NOTE_COMMITS:
        lines = [*lines[:NOTE_COMMITS], f"  ... and {len(lines) - NOTE_COMMITS} older commits"]
    return lines


def notes(record, since, date, selection, repo=REPO):
    """The gh-pages commit message: release, commit range, advisory failures and the region."""
    commit = record["commit"]
    lines = [f"Release {commit[:12]} {date}", ""]
    if since and since != commit:
        lines += [f"Commits {since[:12]}..{commit[:12]}:", *commit_lines(since, commit, repo)]
    else:
        lines.append(f"Commits: no new commits since the previous Pages release ({commit[:12]})")
    advisory = record.get("advisory")
    lines.append("")
    if advisory is None:
        lines.append("Advisory checks: not recorded for this release")
    elif record.get("advisory_pending") and not advisory:
        lines.append("Advisory checks: pending (post-publish)")
    elif not advisory:
        lines.append("Advisory checks: all green")
    else:
        lines.append("Known broken (advisory checks that failed; published anyway):")
        for entry in advisory:
            parts = entry.get("specs") or entry.get("checks") or ()
            first = entry.get("detail", "").splitlines()[0][:200] if entry.get("detail") else ""
            lines.append(f"  {entry['stage']}: {first}")
            lines += [f"    {part}" for part in parts]
    cars = record.get("car_validation")
    if cars and cars.get("status") == "fail":
        lines.append(
            f"Car report (accepted known red): {cars['failed_tiles']}/{cars['tested_tiles']} "
            "tiles fail"
        )
    chrome = record.get("chrome") or {}
    if record.get("full_suite"):
        lines.append("Chrome: scheduled full suite")
    elif chrome:
        lines.append(f"Chrome: {len(chrome.get('selected', ()))} selected spec runs")
    return "\n".join(lines) + "\n\n" + pages_region.notes(selection)


def remote_url(repo):
    return f"git@github.com:{repo}.git"


def git(*args, cwd, timeout=120):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True, timeout=timeout
    ).stdout.strip()


def superseded(record, current, repo=REPO):
    """Reject older/divergent code and, for compatible code, older tile inputs."""
    if record["commit"] != current["commit"]:
        try:
            git("merge-base", "--is-ancestor", current["commit"], record["commit"], cwd=repo)
        except subprocess.CalledProcessError:
            return "older or unrelated source commit"
    old_time, new_time = current.get("data_time"), record.get("data_time")
    if old_time is not None and (new_time is None or new_time < old_time):
        return "older or unknown tile inputs"
    return None


def release_message(message, record):
    return (
        message
        + "\nPages-Release: "
        + json.dumps({key: record.get(key) for key in ("commit", "data_time")})
    )


def remote_release(site):
    message = git("log", "-1", "--format=%B", "FETCH_HEAD", cwd=site)
    for line in message.splitlines():
        if line.startswith("Pages-Release: "):
            return json.loads(line.removeprefix("Pages-Release: "))
    # Publications before the ordering metadata used the source SHA in the subject.
    first = message.splitlines()[0].split()
    if len(first) >= 2 and first[0] == "Release":
        return {"commit": first[1]}
    raise ValueError("remote Pages head has no release identity")


def fetch_head(site, remote, branch):
    try:
        git("fetch", "-q", "--depth=1", remote, branch, cwd=site, timeout=PUSH_TIMEOUT)
    except subprocess.CalledProcessError as exc:
        if "couldn't find remote ref" not in (exc.stderr or ""):
            raise
        return None
    return git("rev-parse", "FETCH_HEAD", cwd=site)


def retryable(exc):
    return any(
        text in (exc.stderr or "").lower()
        for text in ("cannot lock ref", "non-fast-forward", "fetch first", "failed to update ref")
    )


def push(site, target, record, date, selection, source):
    """Fetch/check/commit/push under the caller's lock; retry a remote race once."""
    git("init", "-q", cwd=site)
    git("add", "-A", cwd=site)
    tree = git("write-tree", cwd=site)
    remote, branch = remote_url(target["repo"]), target["branch"]
    for attempt in range(2):
        parent = fetch_head(site, remote, branch)
        current = remote_release(site) if parent else None
        if current and (reason := superseded(record, current, source)):
            return {
                "status": "skipped",
                "skipped": reason,
                "remote_commit": parent,
                "remote_release": current["commit"],
                "retries": attempt,
            }
        since = current["commit"] if current else record.get("from")
        message = release_message(notes(record, since, date, selection, source), record)
        parents = ("-p", parent) if parent else ()
        commit = git("commit-tree", tree, *parents, "-m", message, cwd=site)
        try:
            git(
                "push",
                "-q",
                remote,
                f"{commit}:refs/heads/{branch}",
                cwd=site,
                timeout=PUSH_TIMEOUT,
            )
        except subprocess.CalledProcessError as exc:
            if attempt or not retryable(exc):
                raise
            continue
        return {"status": "published", "commit": commit, "retries": attempt}


def gh(*args):
    return subprocess.run(["gh", *args], text=True, capture_output=True, timeout=60)


def enable_pages(repo, branch):
    """Serve branch at / once; returns the site URL."""
    current = gh("api", f"repos/{repo}/pages")
    if current.returncode:
        created = gh(
            "api",
            "-X",
            "POST",
            f"repos/{repo}/pages",
            "-f",
            f"source[branch]={branch}",
            "-f",
            "source[path]=/",
        )
        # A first push to gh-pages can enable Pages on its own between the two calls (409).
        if created.returncode and "409" not in created.stderr:
            raise ValueError(f"enabling Pages failed: {created.stderr.strip()[-300:]}")
        current = created if not created.returncode else gh("api", f"repos/{repo}/pages")
        if current.returncode:
            raise ValueError(f"reading Pages failed: {current.stderr.strip()[-300:]}")
    return json.loads(current.stdout)["html_url"]


def publish(published, release=None, repo=REPO):
    """Publish release (default: the live release) to the configured Pages repository.

    Advisory failures are listed in the release notes. Generated-facade validation and
    the remote release ordering check protect publication."""
    with sandbox.lock(Path(published) / "publish/pages.lock"):
        return _publish(published, release, repo)


def _publish(published, release, repo):
    published = Path(published)
    target = config(published)
    if target is None:
        raise ValueError(f"{published / CONFIG} does not exist")
    release = Path(release or (published / "live")).resolve()
    record = json.loads((release / "live-build.json").read_text())
    record["data_time"] = tile_store.base_time(release / "tiles")
    facades = check_facades(release)
    selection = pages_region.plan(target, release)
    started = time.monotonic()
    date = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    state_path = published / STATE
    with tempfile.TemporaryDirectory(prefix="pages-") as temp:
        site = Path(temp) / "site"
        size = assemble(release, site, target["repo"], target["branch"], selection)
        outcome = push(site, target, record, date, selection, repo)
    if outcome["status"] == "skipped":
        return {**outcome, "release": record["commit"], "url": site_url(target["repo"])}
    commit = outcome["commit"]
    url = enable_pages(target["repo"], target["branch"])
    pending = state_path.with_suffix(".tmp")
    pending.write_text(json.dumps({"release": record["commit"], "commit": commit}) + "\n")
    os.replace(pending, state_path)
    return {
        **outcome,
        "repo": target["repo"],
        "branch": target["branch"],
        "release": record["commit"],
        "commit": commit,
        "facades": facades,
        "tiles": selection["selected"],
        "excluded": selection["excluded"],
        "bytes": size,
        "seconds": round(time.monotonic() - started, 3),
        "url": url,
    }


def update_notes(published, release, deployment):
    """Append notes only while this deployment is still the remote head."""
    with sandbox.lock(Path(published) / "publish/pages.lock"):
        return _update_notes(published, release, deployment)


def _update_notes(published, release, deployment):
    try:
        record = json.loads((release / "live-build.json").read_text())
        if deployment.get("release") != record["commit"]:
            return {"skipped": "different Pages release"}
        target = config(published)
        selection = pages_region.plan(target, release)
        date = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
        record["data_time"] = tile_store.base_time(release / "tiles")
        message = release_message(notes(record, record.get("from"), date, selection), record)
        with tempfile.TemporaryDirectory(prefix="pages-notes-") as temp:
            site = Path(temp)
            git("init", "-q", cwd=site)
            remote, branch = remote_url(target["repo"]), target["branch"]
            git("fetch", "-q", "--depth=1", remote, branch, cwd=site, timeout=30)
            if git("rev-parse", "FETCH_HEAD", cwd=site) != deployment["commit"]:
                return {"skipped": "newer Pages deployment"}
            tree = git("rev-parse", "FETCH_HEAD^{tree}", cwd=site)
            commit = git("commit-tree", tree, "-p", "FETCH_HEAD", "-m", message, cwd=site)
            git(
                "push",
                "-q",
                remote,
                f"{commit}:refs/heads/{branch}",
                cwd=site,
                timeout=30,
            )
        state = Path(published) / STATE
        if json.loads(state.read_text()).get("commit") == deployment["commit"]:
            state.write_text(json.dumps({"release": record["commit"], "commit": commit}) + "\n")
        return {"commit": commit}
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return {"error": str(exc)[-500:]}


def deploy(published):
    """The integrator's Pages step after every publication: publish the live release when
    publish/pages.json exists, else None. A failure is returned as {"error"} for the
    integration row; the local publication stands."""
    try:
        if config(published) is None:
            return None
        return publish(published)
    except subprocess.CalledProcessError as exc:
        return {"error": f"{' '.join(map(str, exc.cmd[:3]))}: {(exc.stderr or '').strip()[-500:]}"}
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return {"error": str(exc)[-500:]}


def main(argv=None):
    import argparse

    import served

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--published", type=Path, default=os.environ.get("TURKU3D_DATA"))
    parser.add_argument("--main-repo", type=Path, default=REPO)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--from-served",
        action="store_true",
        help="main's production bundle with the served tiles (the default)",
    )
    source.add_argument("--release", type=Path, help="publish this release dir instead")
    parser.add_argument(
        "--plan",
        action="store_true",
        help="print the region selection of --release (default: live); publish nothing",
    )
    args = parser.parse_args(argv)
    if not args.published:
        parser.error("--published or TURKU3D_DATA is required")
    if args.plan:
        release = Path(args.release or (args.published / "live")).resolve()
        print(pages_region.notes(pages_region.plan(config(args.published) or {}, release)), end="")
        return 0
    started = time.monotonic()
    release = args.release or served.release(args.published.resolve(), args.main_repo)
    result = publish(args.published, release)
    result["total_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(result))
    print(result["url"])
    return 0
