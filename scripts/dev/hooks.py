"""Git hooks check isolated index/commit snapshots, never unrelated working files."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path

from gate_lib import git, paths_from, run_gate


@contextmanager
def snapshot(root, revision, staged=False):
    cache = root / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hook-", dir=cache) as directory:
        target = Path(directory) / "tree"
        git(root, "worktree", "add", "--detach", "--quiet", str(target), revision)
        try:
            if staged:
                changed = paths_from(
                    git(root, "diff", "--cached", "--name-only", "--no-renames", "-z")
                )
                for name in changed:
                    file = target / name
                    file.parent.mkdir(parents=True, exist_ok=True)
                    entry = git(root, "ls-files", "--stage", "-z", "--", name)
                    if file.is_symlink() or file.is_file():
                        file.unlink()
                    if entry:
                        mode = entry.split(b" ", 1)[0]
                        content = git(root, "show", f":{name}")
                        if mode == b"120000":
                            file.symlink_to(content.decode())
                        elif mode == b"160000":
                            raise ValueError(
                                "pre-commit: submodule updates require a separate gate"
                            )
                        else:
                            file.write_bytes(content)
                            file.chmod(0o755 if mode == b"100755" else 0o644)
                        git(target, "add", "--force", "--", name)
            # Dependencies are read-only here. Caches and generated files stay in the snapshot.
            for name in (
                "node_modules",
                "game/node_modules",
                "tools/validator/node_modules",
                "qa/render/node_modules",
                "textures/node/node_modules",
                ".venv",
            ):
                source = root / name
                dest = target / name
                if source.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.symlink_to(source, target_is_directory=True)
            stamp = root / ".cache/env-sync/stamp.json"
            if stamp.exists():
                destination = target / ".cache/env-sync/stamp.json"
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(stamp, destination)
            yield target
        finally:
            git(root, "worktree", "remove", "--force", str(target))


def check_snapshot(target, changed, lint_only=False):
    return subprocess.run(
        [
            sys.executable,
            str(target / "scripts/dev/hooks.py"),
            "_check",
            "lint" if lint_only else "full",
        ],
        cwd=target,
        env={
            **os.environ,
            "TURKU3D_WORK": str(target / ".cache/work"),
            "TURKU3D_BROWSER_DIR": str(target / ".cache/browser"),
        },
        text=True,
        input=json.dumps(changed),
        check=False,
    ).returncode


def main():
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "_check":
            config = tomllib.loads((root / "scripts/dev/gate.toml").read_text())
            return run_gate(root, config, json.load(sys.stdin), lint_only=sys.argv[2] == "lint")
        if sys.argv[1:] == ["pre-commit"]:
            changed = sorted(
                paths_from(git(root, "diff", "--cached", "--name-only", "--no-renames", "-z"))
            )
            if not changed:
                return 0
            with snapshot(root, "HEAD", staged=True) as target:
                return check_snapshot(target, changed, lint_only=True)
        if sys.argv[1:] != ["pre-push"]:
            raise ValueError("usage: hooks.py pre-commit|pre-push")
        result = 0
        for line in sys.stdin:
            _local_ref, local, _remote_ref, remote = line.split()
            if set(local) == {"0"}:  # branch deletion has no new content
                continue
            base = remote if set(remote) != {"0"} else "main"
            if set(remote) != {"0"}:
                # Fail explicitly for an unknown remote object; never fetch during a hook.
                git(root, "cat-file", "-e", remote + "^{commit}")
            ancestor = git(root, "merge-base", base, local).decode().strip()
            changed = sorted(
                paths_from(git(root, "diff", "--name-only", "--no-renames", "-z", ancestor, local))
            )
            with snapshot(root, local) as target:
                result = max(result, check_snapshot(target, changed))
        return result
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"hook failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Git exports GIT_DIR/GIT_WORK_TREE in some hook contexts. Nested worktrees own theirs.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        os.environ.pop(key, None)
    raise SystemExit(main())
