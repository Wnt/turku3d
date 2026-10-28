"""Merge an agent branch, deploy immediately, smoke, then start owned async checks.

The historical module/ledger names remain readable by already-running task processes.
There is no batch integrator, deployment lock, queue, bisection or rollback path.
"""

import contextlib
import json
import os
import subprocess
import time

import coordination as coord
import delivery
import sandbox as s

PUBLIC_REF = "refs/public/main"
PUSH_SECONDS = 60


class Conflict(ValueError):
    """Rebase requires the agent to resolve its own conflicts."""


def ledger_path():
    return s.root() / ".registry/landed.jsonl"


def done(text):
    """Whether a run result has exactly one `## Result:` line, and it is DONE."""
    return [line for line in text.splitlines() if line.startswith("## Result:")] == [
        "## Result: DONE"
    ]


def result_done(path):
    """Whether the task directory's recorded result (last.md) is DONE."""
    result = path / "last.md"
    return result.exists() and done(result.read_text())


def ledger_rows():
    """The landed.jsonl rows (landings and integrator reverts); torn lines are skipped."""
    rows = []
    with contextlib.suppress(FileNotFoundError):
        for line in ledger_path().read_text().splitlines():
            with contextlib.suppress(ValueError):
                rows.append(json.loads(line))
    return rows


def behind(name):
    """Landings on main that the task's checkout has not rebased onto."""
    missing = coord.not_rebased(name)
    return sum(row.get("commit") in missing for row in ledger_rows()) if missing else 0


def completed_at(meta):
    return meta.get("finished") if meta.get("state") == "done" else meta.get("finish_requested")


def member(name):
    """A finished task's (path, meta, result text): DONE result recorded."""
    from task_runner import task

    path = task(name)
    meta = s.read_json(path / "meta.json")
    own_turn = (
        meta.get("state") in ("starting", "running")
        and os.environ.get("TURKU3D_SANDBOX") == name
        and meta.get("finish_requested")
    )
    if not own_turn and (meta.get("state") != "done" or meta.get("exit_code") != 0):
        raise ValueError(f"{name}: task must have completed successfully before landing")
    if not result_done(path):
        raise ValueError(f"{name}: result must contain an exact ## Result: DONE heading")
    return path, meta, (path / "last.md").read_text()


def checkout(name):
    """The task's sandbox checkout: on its branch, clean, with a gate."""
    repo = s.sandbox(name) / "repo"
    if s.git("branch", "--show-current", cwd=repo) != f"wt/{name}":
        raise ValueError(f"{name}: sandbox is not on its expected branch")
    if s.git("status", "--porcelain", cwd=repo):
        raise ValueError(f"{name}: sandbox is dirty")
    if not (repo / "scripts/dev/gate").is_file():
        raise ValueError(f"{name}: missing scripts/dev/gate; main unchanged")
    return repo


def main_worktree(repo):
    from task_runner import main_checkout

    main = main_checkout(repo)
    if main and s.git("status", "--porcelain", cwd=main):
        raise ValueError("main worktree is dirty")
    return main


def rebase(repo):
    """Rebase onto main; on conflict abort and return the conflicting paths."""
    if s.run(
        "git", "merge-base", "--is-ancestor", "main", "HEAD", cwd=repo, check=False
    ).returncode:
        result = s.run("git", "rebase", "main", cwd=repo, check=False)
        if result.returncode:
            files = s.git("diff", "--name-only", "--diff-filter=U", cwd=repo, check=False)
            s.git("rebase", "--abort", cwd=repo, check=False)
            return files.splitlines() or [result.stderr.strip()[:240] or "rebase failed"]
    return []


def advance(repo, main, before, head):
    if main:
        if s.git("status", "--porcelain", cwd=main):
            raise ValueError("main became dirty during gate; main unchanged")
        s.git("merge", "--ff-only", head, cwd=main)
    else:
        s.git("update-ref", "refs/heads/main", head, before, cwd=repo)


def push_public(repo):
    """Publish main's tree to `origin` as the next commit of the public line; best effort.

    The public repository starts from one squashed import because older local history holds
    street imagery, so local `main` itself is never pushed. Each merge adds one commit with
    main's tree on top of PUBLIC_REF and pushes that line to origin's `main`. Returns None
    without an origin, else {"commit", "seconds"} or {"error", "seconds"}.
    """
    if not s.git("remote", "get-url", "origin", cwd=repo, check=False):
        return None
    started = time.monotonic()
    try:
        parent = s.git("rev-parse", "--verify", "-q", PUBLIC_REF, cwd=repo, check=False)
        if not parent:
            raise ValueError(f"{PUBLIC_REF} is missing; create the public import first")
        tree = s.git("rev-parse", "main^{tree}", cwd=repo)
        commit = parent
        if s.git("rev-parse", parent + "^{tree}", cwd=repo) != tree:
            subject = s.git("log", "-1", "--format=%s", "main", cwd=repo)
            source = s.git("rev-parse", "main", cwd=repo)
            message = f"{subject}\n\nMerged into local main {source}."
            commit = s.git("commit-tree", tree, "-p", parent, "-m", message, cwd=repo)
            s.git("update-ref", PUBLIC_REF, commit, parent, cwd=repo)
        subprocess.run(
            ["git", "push", "-q", "origin", f"{PUBLIC_REF}:refs/heads/main"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=PUSH_SECONDS,
        )
        return {"commit": commit, "seconds": round(time.monotonic() - started, 3)}
    except subprocess.CalledProcessError as exc:
        error = (exc.stderr or exc.stdout or str(exc)).strip()[-300:]
    except (subprocess.TimeoutExpired, ValueError) as exc:
        error = str(exc)[-300:]
    return {"error": error, "seconds": round(time.monotonic() - started, 3)}


def record_merge(name, path, meta, before, head, tests, paths, started, subject):
    row = {
        "name": name,
        "before": before,
        "commit": head,
        "source": head,
        "time": time.time(),
        "merge_wall_seconds": round(time.monotonic() - started, 3),
        "test_seconds": tests["seconds"],
        "tests": tests["checks"],
        "done_time": completed_at(meta),
        "class": "code",
        "paths": paths,
        "changed_paths": len(paths),
        "tiles": [],
        "data_delta": [],
    }
    ledger_path().parent.mkdir(parents=True, exist_ok=True)
    with ledger_path().open("a") as output:
        output.write(json.dumps(row) + "\n")
    current = s.read_json(path / "meta.json", {})
    s.write_json(path / "meta.json", {**current, "landed": row, "merged": row})
    coord.broadcast_landed(
        name, row, subject, 0, "Merged into main; direct Pages deployment follows."
    )
    print(f"{name}: merged {head[:12]} into main; tests {tests['seconds']}s", flush=True)
    return row


def land(name, keep_sandbox=False, note=None):
    """Compatibility entrypoint for running runners; finish uses the same direct flow."""
    import post_deploy
    from task_runner import cleanup_landed

    started = time.monotonic()
    path, meta, _text = member(name)
    repo = checkout(name)
    previous = meta.get("merged") or meta.get("landed") or {}
    head = s.git("rev-parse", "HEAD", cwd=repo)
    already = (
        previous.get("commit") == head
        and s.run(
            "git", "merge-base", "--is-ancestor", head, "main", cwd=repo, check=False
        ).returncode
        == 0
    )
    if (
        already
        and s.read_json(path / "deployment.json", {}).get("commit") == head
        and s.read_json(path / "deployment.json", {}).get("result") == "deployed"
    ):
        if not keep_sandbox:
            cleanup_landed(name)
        return previous
    selected = delivery.plan(path, repo)
    if not already:
        post_deploy.validate_fix(selected.get("fix_for"))
    if selected.get("heads_up"):
        coord.send("all", "interface", f"{name}: shared change", selected["heads_up"], refs=(head,))
    if not already:
        before = s.git("rev-parse", "main", cwd=repo)
        if conflicts := rebase(repo):
            raise Conflict(
                f"rebase onto main conflicts in {', '.join(conflicts)}; resolve and finish again"
            )
        tests = delivery.check(repo, selected, path)
        if not tests["ok"]:
            raise ValueError(
                f"affected tests failed or exceeded 30s; main unchanged; {path / 'quick'}"
            )
        if s.git("rev-parse", "main", cwd=repo) != before:
            raise ValueError("main moved during selected tests; rebase and finish again")
        if s.git("status", "--porcelain", cwd=repo):
            raise ValueError("tests changed tracked or untracked files; commit or remove them")
        head = s.git("rev-parse", "HEAD", cwd=repo)
        paths = s.git("diff", "--name-only", before, head, cwd=repo).splitlines()
        advance(repo, main_worktree(repo), before, head)
        subject = s.git("log", "-1", "--format=%s", head, cwd=repo)
        previous = record_merge(name, path, meta, before, head, tests, paths, started, subject)
        if selected.get("fix_for"):
            post_deploy.append(
                {
                    "job": name + ":" + head,
                    "name": name,
                    "commit": head,
                    "state": "fix-merged",
                    "fix_for": selected["fix_for"],
                    "time": time.time(),
                }
            )
    selected["changed_paths"] = previous.get("paths", [])
    deployed = delivery.after_merge(name, repo, head, selected, path)
    # Source publication is independent of Pages and never delays its package/upload.
    pushed = push_public(repo)
    if pushed:
        print(f"source publication: {json.dumps(pushed)}", flush=True)
    if deployed["result"] != "deployed":
        raise ValueError(f"main remains merged; deploy failed: {deployed.get('error')}")
    if keep_sandbox and meta.get("worker") == "external":
        current = s.read_json(path / "meta.json", {})
        s.write_json(path / "meta.json", {**current, "state": "external"})
    if not keep_sandbox:
        cleanup_landed(name)
    return {**previous, "deployment": deployed}
