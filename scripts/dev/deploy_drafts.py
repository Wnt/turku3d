"""Incremental OCI source deployment with uninterrupted cached serving and idle route-ring warming."""

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


def deploy_multiplayer(destination):
    return remote(
        f"{HOME}/run.sh bash {destination}/tools/remote-drafts/update-multiplayer.sh "
        f"{destination}"
    )


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
        multiplayer = deploy_multiplayer(destination)
        return dict(commit=commit, seconds=round(time.monotonic() - started, 3),
                    reused=True, multiplayer=multiplayer)
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
    # Keep the old producer serving while a standby binds the alternate port. Its
    # workers stay paused until the old process group has stopped writing caches.
    active = remote(f"cat {HOME}/ACTIVE_PORT 2>/dev/null || echo 7765")
    port = 7766 if active == "7765" else 7765
    unit = f"turku-drafts-slot@{port}"
    old_unit = remote(f"cat {HOME}/ACTIVE_UNIT 2>/dev/null || echo turku-drafts")
    remote(
        f"test -f {HOME}/draft-upstream.caddy || "
        f"cp {destination}/tools/remote-drafts/draft-upstream.caddy {HOME}/draft-upstream.caddy; "
        f"{HOME}/tools/bin/caddy validate --config {destination}/tools/remote-drafts/Caddyfile "
        "--adapter caddyfile >/dev/null"
    )
    # Standby queues outdated tiles until activation; unchanged cache stays available.
    remote(
        f"install -m 644 {destination}/tools/remote-drafts/turku-drafts-slot@.service ~/.config/systemd/user/ && "
        f"printf '%s\\n' TURKU3D_DRAFT_HOME={HOME} TURKU3D_DRAFT_PORT={port} "
        f"TURKU3D_DRAFT_STANDBY=1 TURKU3D_DRAFT_SOURCE={destination} > {HOME}/slot-{port}.env && "
        "systemctl --user daemon-reload && "
        f"systemctl --user start {unit}"
    )
    health = f"""import time,json,urllib.request,urllib.error
end=time.monotonic()+5
while time.monotonic()<end:
 try:
  with urllib.request.urlopen('http://127.0.0.1:{port}/coarse/health',timeout=.5) as r:
   if r.status==200:
    print(json.load(r)['instance']); break
 except (OSError,urllib.error.URLError): pass
 time.sleep(.1)
else: raise SystemExit('standby draft producer did not become ready within5s')
"""
    instance = remote("python3 -c " + shlex.quote(health))
    pending = f"{HOME}/.repo-{uuid.uuid4().hex}"
    remote(
        f"if test -d {HOME}/repo && ! test -L {HOME}/repo; then "
        f"mv {HOME}/repo {HOME}/repo-initial-{uuid.uuid4().hex}; fi && "
        f"ln -s {destination} {pending} && mv -Tf {pending} {HOME}/repo && "
        f"echo 'reverse_proxy 127.0.0.1:{port}' > {HOME}/draft-upstream.caddy.next && "
        f"mv {HOME}/draft-upstream.caddy.next {HOME}/draft-upstream.caddy && "
        "systemctl --user kill --kill-whom=main --signal=USR1 turku-drafts-proxy"
    )
    # SIGUSR1 reloads Caddy's original config file without closing its listener.
    # Do not stop the old upstream until the proxy confirms the standby instance.
    switched = f"""import time,json,urllib.request
end=time.monotonic()+5
while time.monotonic()<end:
 try:
  with urllib.request.urlopen('http://127.0.0.1:7764/coarse/health',timeout=.5) as r:
   if r.status==200 and json.load(r).get('instance') == {instance!r}: break
 except OSError: pass
 time.sleep(.1)
else: raise SystemExit('proxy did not switch to standby within5s')
"""
    remote("python3 -c " + shlex.quote(switched))
    remote(f"systemctl --user stop turku-drafts-prebuild {old_unit} && "
           f"systemctl --user disable {old_unit}")
    cache_storage = remote(
        f"{HOME}/run.sh python {destination}/tools/remote-drafts/cache-storage.py "
        f"--work /srv/turku3d/work --destination /home/ubuntu/turku3d-cache"
    )
    restarted = remote(
        f"echo {commit} > {HOME}/SOURCE_COMMIT && "
        f"echo {port} > {HOME}/ACTIVE_PORT && echo {unit} > {HOME}/ACTIVE_UNIT && "
        f"sed -i s/TURKU3D_DRAFT_STANDBY=1/TURKU3D_DRAFT_STANDBY=0/ {HOME}/slot-{port}.env && "
        f"systemctl --user enable {unit} && "
        f"systemctl --user kill --kill-whom=main --signal=USR1 {unit} && "
        f"install -m 644 {destination}/tools/remote-drafts/turku-facade-assets.service "
        f"{destination}/tools/remote-drafts/turku-facade-assets.path "
        "~/.config/systemd/user/ && "
        "systemctl --user daemon-reload && "
        "systemctl --user enable --now turku-facade-assets.path && "
        "systemctl --user start --no-block turku-facade-assets.service && "
        "systemctl --user disable turku-drafts-prebuild && "
        "rm -f ~/.config/systemd/user/turku-drafts-prebuild.service && "
        "systemctl --user daemon-reload && "
        "date -u +%Y-%m-%dT%H:%M:%SZ"
    )
    multiplayer = deploy_multiplayer(destination)
    return dict(
        commit=commit,
        seconds=round(time.monotonic() - started, 3),
        restarted_utc=restarted,
        proxy_restarted=False,
        active_port=port,
        rsync=result.stdout,
        multiplayer=multiplayer,
        cache_storage=cache_storage,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    if args.commit:
        print(json.dumps(deploy(args.repo, args.commit)))
    else:
        parser.error("--commit SHA required")
    return 0
