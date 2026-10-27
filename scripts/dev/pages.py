"""Publish a verified live release to GitHub Pages as one orphan commit.

The deploy repository named in `$TURKU3D_DATA/publish/pages.json`
(`{"repo": "owner/name", "branch": "gh-pages"}`) only ever holds the latest release:
`game/` and `tiles/` from the release, the site files in `pages-site/` and `.nojekyll`.
Each publish force-pushes a new parentless commit, so the repository never accumulates
older releases. No source code, pipeline data or street imagery is published.
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

CONFIG = "publish/pages.json"
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
    return {"repo": data["repo"], "branch": data.get("branch", "gh-pages")}


def generated(meta):
    """Generated-style facade maps: the only facade class allowed in a public release."""
    return meta.get("source_mode") == "generate" and bool(meta.get("generated_qa"))


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


def assemble(release, target, repo, branch):
    """Write the deploy tree: release game and tiles plus the static site files."""
    release, target = Path(release), Path(target)
    for name in ("game", "tiles"):
        source = release / name
        if not source.is_dir():
            raise ValueError(f"release has no {name}/: {release}")
        shutil.copytree(source, target / name)
    values = {"{{url}}": site_url(repo), "{{repo}}": repo, "{{branch}}": branch}
    for path in SITE.iterdir():
        text = path.read_text()
        for key, value in values.items():
            text = text.replace(key, value)
        (target / path.name).write_text(text)
    (target / ".nojekyll").write_text("")
    return sum(path.stat().st_size for path in target.rglob("*") if path.is_file())


def remote_url(repo):
    return f"git@github.com:{repo}.git"


def git(*args, cwd, timeout=120):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True, timeout=timeout
    ).stdout.strip()


def push(site, repo, branch, message):
    """Force-push site as a single parentless commit; returns the commit id."""
    git("init", "-q", "-b", branch, cwd=site)
    git("add", "-A", cwd=site)
    git("commit", "-q", "-m", message, cwd=site)
    git(
        "push",
        "-q",
        "--force",
        remote_url(repo),
        f"HEAD:refs/heads/{branch}",
        cwd=site,
        timeout=PUSH_TIMEOUT,
    )
    return git("rev-parse", "HEAD", cwd=site)


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


def publish(published, release=None):
    """Publish release (default: the live release) to the configured Pages repository."""
    published = Path(published)
    target = config(published)
    if target is None:
        raise ValueError(f"{published / CONFIG} does not exist")
    release = Path(release or (published / "live")).resolve()
    record = json.loads((release / "live-build.json").read_text())
    facades = check_facades(release)
    started = time.monotonic()
    date = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    message = f"Release {record['commit'][:12]} {date}"
    with tempfile.TemporaryDirectory(prefix="pages-") as temp:
        site = Path(temp) / "site"
        size = assemble(release, site, target["repo"], target["branch"])
        commit = push(site, target["repo"], target["branch"], message)
    url = enable_pages(target["repo"], target["branch"])
    return {
        "repo": target["repo"],
        "branch": target["branch"],
        "release": record["commit"],
        "commit": commit,
        "facades": facades,
        "bytes": size,
        "seconds": round(time.monotonic() - started, 3),
        "url": url,
    }


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--published", type=Path, default=os.environ.get("TURKU3D_DATA"))
    parser.add_argument("--release", type=Path, help="release dir (default: <published>/live)")
    args = parser.parse_args(argv)
    if not args.published:
        parser.error("--published or TURKU3D_DATA is required")
    result = publish(args.published, args.release)
    print(json.dumps(result))
    print(result["url"])
    return 0
