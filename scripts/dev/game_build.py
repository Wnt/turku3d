"""Reuse an intact local game bundle before acquiring short-check capacity."""

import hashlib
import os
import subprocess
from pathlib import Path

import host_slots
import live_cache
import sandbox as s


def outputs(work, public):
    """Hash every output byte; missing, corrupted and extra files invalidate the stamp."""
    result = {}
    for name in ("game", "check-game") if public else ("game",):
        root = work / "build" / name
        if not (root / "index.html").is_file():
            return None
        result[name] = {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }
    return result


def build(repo, work, env):
    for name in ("game", "check-game"):
        if not (work / "build" / name).resolve().is_relative_to(work.resolve()):
            raise ValueError("game build output must stay inside the sandbox")
    directory = work / "cache/game-build"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = directory / "stamp.json"
    with s.lock(directory / "lock"):
        key = live_cache.game_key(repo, env)
        public = env.get("VITE_PUBLIC_RELEASE") == "1"
        previous = s.read_json(stamp, {})
        actual = outputs(work, public)
        if previous.get("key") == key and actual and actual == previous.get("outputs"):
            print("game-build: cached PASS (input and output hashes match)", flush=True)
            return
        stamp.unlink(missing_ok=True)
        with host_slots.slot("check") as inherited:
            jobs = host_slots.integration_jobs(lane=inherited) if inherited else 1
            subprocess.run(
                ["node", "scripts/build.mjs"],
                cwd=repo / "game",
                env={**env, "TURKU3D_JOBS": str(jobs)},
                check=True,
            )
        actual = outputs(work, public)
        if not actual or live_cache.game_key(repo, env) != key:
            raise ValueError("game build outputs missing or inputs changed during build; retry")
        s.write_json(stamp, {"key": key, "outputs": actual})


if __name__ == "__main__":
    build(s.REPO, Path(os.environ["TURKU3D_WORK"]), dict(os.environ))
