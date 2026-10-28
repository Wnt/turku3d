"""Incremental OCI generator source deployment; prewarming is a separate asynchronous step."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import tarfile
import time
import uuid
from pathlib import Path

from facade_publish import HOST, SSH, remote

HOME = "/home/ubuntu/turku3d-drafts"


def deploy(repo, commit):
    started = time.monotonic()
    repo = Path(repo)
    commit = subprocess.check_output(
        ["git", "rev-parse", f"{commit}^{{commit}}"], cwd=repo, text=True
    ).strip()
    source = Path(os.environ["TURKU3D_WORK"]) / "build/draft-deploy" / commit
    if not source.exists():
        source.mkdir(parents=True)
        with subprocess.Popen(["git", "archive", commit], cwd=repo, stdout=subprocess.PIPE) as proc:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
                archive.extractall(source, filter="data")
            if proc.wait():
                raise RuntimeError("git archive failed")
    destination = f"{HOME}/sources/{commit}"
    remote(f"mountpoint -q /srv/turku3d && mkdir -p {destination}")
    previous = remote(f"readlink -f {HOME}/repo")
    if previous == destination:
        return dict(commit=commit, seconds=round(time.monotonic() - started, 3), reused=True)
    result = subprocess.run(
        [
            "rsync",
            "-a",
            "--checksum",
            "--stats",
            f"--link-dest={previous}",
            "-e",
            shlex.join(SSH),
            str(source) + "/",
            f"{HOST}:{destination}/",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    # Validate the proxy config before touching the live source pointer.
    remote(
        f"{HOME}/tools/bin/caddy validate --config {destination}/tools/remote-drafts/Caddyfile "
        "--adapter caddyfile >/dev/null"
    )
    pending = f"{HOME}/.repo-{uuid.uuid4().hex}"
    remote(
        f"systemctl --user stop turku-drafts-prebuild turku-drafts && "
        f"if test -d {HOME}/repo && ! test -L {HOME}/repo; then "
        f"mv {HOME}/repo {HOME}/repo-initial-{uuid.uuid4().hex}; fi && "
        f"ln -s {destination} {pending} && mv -Tf {pending} {HOME}/repo && "
        f"echo {commit} > {HOME}/SOURCE_COMMIT && "
        f"install -m 644 {destination}/tools/remote-drafts/turku-facade-assets.service "
        f"{destination}/tools/remote-drafts/turku-facade-assets.path ~/.config/systemd/user/ && "
        "systemctl --user daemon-reload && "
        "systemctl --user enable --now turku-facade-assets.path && "
        "systemctl --user start --no-block turku-facade-assets.service && "
        "systemctl --user start turku-drafts && systemctl --user restart turku-drafts-proxy"
    )
    return dict(commit=commit, seconds=round(time.monotonic() - started, 3), rsync=result.stdout)


def prewarm():
    """Queue the existing nearest-first ring; return without waiting for any tile build."""
    remote("systemctl --user restart --no-block turku-drafts-prebuild")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument(
        "--prewarm", action="store_true", help="start background nearest-first warming only"
    )
    args = parser.parse_args()
    if args.prewarm:
        prewarm()
        print(json.dumps(dict(prewarm="queued")))
    elif args.commit:
        print(json.dumps(deploy(args.repo, args.commit)))
    else:
        parser.error("--commit SHA or --prewarm required")
    return 0
