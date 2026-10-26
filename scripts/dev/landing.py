"""The lander: rebase, fast gate and fast-forward merge of finished task branches onto main.

No host slot, no live build and no receipt. The integrator (integrator.py) builds and
verifies the live release asynchronously from the ledger rows written here.
"""

import contextlib
import json
import os
import shutil
import subprocess
import time
import uuid

import coordination as coord
import sandbox as s

BUDGET_SECONDS = 180
FACADE_MARKER = "Facade regression reason:"


class Returned(ValueError):
    """The branch went back to its worker with an inbox request; main is unchanged."""


def ledger_path():
    return s.root() / ".registry/landed.jsonl"


def facade_reason(text):
    """The result's `Facade regression reason:` text, which admits a lower facade count."""
    return next(
        (
            line[len(FACADE_MARKER) :].strip()
            for line in text.splitlines()
            if line.startswith(FACADE_MARKER)
        ),
        None,
    )


def member(name, force):
    """Validate a finished task: DONE result, clean expected branch, no open requests."""
    from task_runner import running, task

    path = task(name)
    meta = s.read_json(path / "meta.json")
    if running(meta) or meta.get("state") != "done" or meta.get("exit_code") != 0:
        raise ValueError(f"{name}: task must have completed successfully before landing")
    with coord.landing_guard(name, force):
        pass
    result = path / "last.md"
    text = result.read_text() if result.exists() else ""
    if [line for line in text.splitlines() if line.startswith("## Result:")] != ["## Result: DONE"]:
        raise ValueError(f"{name}: result must contain an exact ## Result: DONE heading")
    repo = s.sandbox(name) / "repo"
    if s.git("branch", "--show-current", cwd=repo) != f"wt/{name}":
        raise ValueError(f"{name}: sandbox is not on its expected branch")
    if s.git("status", "--porcelain", cwd=repo):
        raise ValueError(f"{name}: sandbox is dirty")
    if not (repo / "scripts/dev/gate").is_file():
        raise ValueError(f"{name}: missing scripts/dev/gate; main unchanged")
    s.reap_browsers(name)
    return path, meta, repo, text


def main_worktree(repo):
    from task_runner import main_checkout

    main = main_checkout(repo)
    if main and s.git("status", "--porcelain", cwd=main):
        raise ValueError("main worktree is dirty")
    return main


def give_back(name, path, reason, detail):
    """Return the task to its worker: inbox request, meta `returned`, main untouched."""
    coord.send(
        name,
        "request",
        f"lander returned {name}",
        f"The lander cannot land this branch: {reason}\n{detail}\n"
        "Rebase onto current main, resolve it, commit, run scripts/dev/gate --fast --base main, "
        "ack this message, then run codex-task finish again.",
    )
    with s.lock(path / "lock"):
        meta = s.read_json(path / "meta.json")
        meta.update(state="returned", returned_reason=reason, returned_time=time.time())
        s.write_json(path / "meta.json", meta)
    return Returned(f"{name}: returned to worker: {reason}; main unchanged")


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


def stage_times(output):
    """Per-stage seconds from the gate's text report, including cached stages."""
    stages = {}
    for line in output.splitlines():
        fields = line.split()
        if fields and fields[0] == "cached":
            fields = fields[1:]
        if len(fields) >= 3 and fields[0] in ("PASS", "FAIL") and fields[2].endswith("s"):
            with contextlib.suppress(ValueError):
                stages[fields[1]] = float(fields[2][:-1])
    return stages


def fast_gate(repo, base, log_path, deadline):
    """Run `gate --fast --base <base>` in the checkout; return (ok, seconds, stages, detail)."""
    env = {**os.environ, **s.read_env(repo.parent / "sandbox.env")}
    head = s.git("rev-parse", "HEAD", cwd=repo)
    start = time.monotonic()
    result = s.run_group(
        [str(repo / "scripts/dev/gate"), "--fast", "--base", base],
        cwd=repo,
        env=env,
        log=log_path,
        timeout=max(1.0, deadline - start),
    )
    seconds = round(time.monotonic() - start, 3)
    if s.git("status", "--porcelain", cwd=repo) or s.git("rev-parse", "HEAD", cwd=repo) != head:
        raise ValueError("gate changed checkout files or HEAD; main unchanged")
    detail = ""
    if result.returncode:
        detail = f"{s.failure_detail(result.stdout)}; host load {s.host_load()}: {log_path}"
    return result.returncode == 0, seconds, stage_times(result.stdout), detail


def over_budget(paths, started, budget, where):
    """Record `merge_slow` in each task's meta and raise once the landing exceeds its budget."""
    elapsed = round(time.monotonic() - started, 3)
    if elapsed <= budget:
        return
    for path in paths:
        with s.lock(path / "lock"):
            meta = s.read_json(path / "meta.json")
            meta["merge_slow"] = {"seconds": elapsed, "where": where, "time": time.time()}
            s.write_json(path / "meta.json", meta)
    raise ValueError(
        f"merge_slow: landing took {elapsed}s ({where}), over the {budget}s budget; main unchanged"
    )


def advance(repo, main, before, head):
    if main:
        if s.git("status", "--porcelain", cwd=main):
            raise ValueError("main became dirty during gate; main unchanged")
        s.git("merge", "--ff-only", head, cwd=main)
    else:
        s.git("update-ref", "refs/heads/main", head, before, cwd=repo)


def record(name, path, text, before, commit, source, started, gate, batch=None):
    """Append the ledger row and store it in meta; `first_finished` is never overwritten."""
    seconds, stages = gate
    elapsed = round(time.monotonic() - started, 3)
    with s.lock(path / "lock"):
        meta = s.read_json(path / "meta.json")
        row = {
            "name": name,
            "before": before,
            "commit": commit,
            "source": source,
            "time": time.time(),
            "merge_wall_seconds": elapsed,
            "merge_slow": elapsed > BUDGET_SECONDS,
            "fast_gate_seconds": seconds,
            "fast_gate_stages": stages,
            "first_done_time": meta.get("first_finished", meta.get("finished")),
            "done_time": meta.get("finished"),
            "facade_regression_reason": facade_reason(text),
        }
        if batch:
            row["batch"] = batch
        meta["landed"] = row
        s.write_json(path / "meta.json", meta)
    with ledger_path().open("a") as ledger:
        ledger.write(json.dumps(row) + "\n")
    return row


def land(name, force=False):
    """Land one finished task on main in at most BUDGET_SECONDS; return its ledger row."""
    from task_runner import cleanup_landed, task

    started = time.monotonic()
    deadline = started + BUDGET_SECONDS
    with s.lock(s.root() / ".registry/land.lock"), s.lock(task(name) / "lock"):
        path, _meta, repo, text = member(name, force)
        main = main_worktree(repo)
        for _attempt in range(3):
            before = s.git("rev-parse", "main", cwd=repo)
            if conflicts := rebase(repo):
                raise give_back(
                    name,
                    path,
                    "rebase conflict with main",
                    "Conflicting files: " + ", ".join(conflicts),
                )
            head = s.git("rev-parse", "HEAD", cwd=repo)
            ok, seconds, stages, detail = fast_gate(repo, before, path / "gate.log", deadline)
            over_budget([path], started, BUDGET_SECONDS, "fast gate")
            if not ok:
                raise ValueError(f"{name}: fast gate failed: {detail}; main unchanged")
            if s.git("rev-parse", "main", cwd=repo) == before:
                break
        else:
            raise ValueError("main changed during gate three times; retry landing")
        with coord.landing_guard(name, force):
            over_budget([path], started, BUDGET_SECONDS, "merge")
            advance(repo, main, before, head)
        row = record(name, path, text, before, head, head, started, (seconds, stages))
        print(f"{name}: landed {head[:12]} in {row['merge_wall_seconds']}s (fast gate {seconds}s)")
        cleanup_landed(name)
        return row


def batch(names, force=False):
    """Land several tasks in order; a red fast gate bisects and lands the green prefix.

    Each member keeps the three-minute budget, so the batch budget scales with its size.
    """
    from task_runner import cleanup_landed, task

    if len(set(names)) != len(names):
        raise ValueError("batch members must be unique")
    started = time.monotonic()
    budget = BUDGET_SECONDS * len(names)
    deadline = started + budget
    with s.lock(s.root() / ".registry/land.lock"), contextlib.ExitStack() as locks:
        members = []
        for name in names:
            locks.enter_context(s.lock(task(name) / "lock"))
            path, _meta, repo, text = member(name, force)
            members.append((name, path, text, s.git("rev-parse", "HEAD", cwd=repo)))
        main = main_worktree(repo)
        before = s.git("rev-parse", "main", cwd=repo)
        integration = s.new("land-batch-" + uuid.uuid4().hex[:10], before)
        log_path = s.tasks_root() / ("batch-" + integration.parent.name + ".log")
        commits = []
        gates = {}

        def assemble(count):
            s.git("reset", "--hard", before, cwd=integration)
            commits.clear()
            for name, path, _text, head in members[:count]:
                revisions = s.git(
                    "rev-list",
                    "--reverse",
                    "--cherry-pick",
                    "--right-only",
                    "--no-merges",
                    f"HEAD...{head}",
                    cwd=integration,
                ).splitlines()
                if revisions:
                    try:
                        s.git("cherry-pick", *revisions, cwd=integration)
                    except subprocess.CalledProcessError:
                        files = s.git(
                            "diff", "--name-only", "--diff-filter=U", cwd=integration, check=False
                        )
                        s.git("cherry-pick", "--abort", cwd=integration, check=False)
                        raise give_back(
                            name,
                            path,
                            "batch integration conflict",
                            "Conflicting files: " + ", ".join(files.splitlines()),
                        ) from None
                commits.append(s.git("rev-parse", "HEAD", cwd=integration))

        def green(count):
            assemble(count)
            ok, seconds, stages, detail = fast_gate(integration, before, log_path, deadline)
            over_budget([m[1] for m in members], started, budget, f"batch gate of {count}")
            gates[count] = (seconds, stages, detail)
            return ok

        try:
            landed = len(members)
            if not green(landed):
                if not green(0):
                    raise ValueError(f"base gate fails; {gates[0][2]}; main unchanged")
                low, high = 0, len(members)
                while high - low > 1:
                    middle = (low + high) // 2
                    low, high = (middle, high) if green(middle) else (low, middle)
                landed = low
                if landed:
                    assemble(landed)
            culprit = None if landed == len(members) else members[landed]
            rows = []
            if landed:
                with contextlib.ExitStack() as guards:
                    for name, _path, _text, head in members[:landed]:
                        guards.enter_context(coord.landing_guard(name, force))
                        repo = s.sandbox(name) / "repo"
                        if (
                            s.git("status", "--porcelain", cwd=repo)
                            or s.git("rev-parse", "HEAD", cwd=repo) != head
                        ):
                            raise ValueError(f"{name}: member changed during gate")
                    if s.git("rev-parse", "main", cwd=integration) != before:
                        raise ValueError("main changed during batch gate; retry batch")
                    advance(integration, main, before, commits[-1])
                gate = gates[landed][:2]
                for (name, path, text, source), commit in zip(
                    members[:landed], commits, strict=True
                ):
                    rows.append(
                        record(name, path, text, before, commit, source, started, gate, names)
                    )
                print(f"batch landed: {', '.join(names[:landed])}; {log_path}")
                for row in rows:
                    cleanup_landed(row["name"], row["commit"])
            if culprit:
                detail = gates[landed + 1][2]
                raise give_back(
                    culprit[0],
                    culprit[1],
                    "fast gate red in batch",
                    f"Bisected failing prefix ends at {culprit[0]}: {detail}. "
                    f"Landed before it: {', '.join(names[:landed]) or 'none'}; "
                    f"not attempted after it: {', '.join(names[landed + 1 :]) or 'none'}.",
                )
            return rows
        finally:
            # This process created the integration checkout; member worktrees are untouched.
            s.git("worktree", "remove", "--force", str(integration), check=False)
            s.git("branch", "-D", f"wt/{integration.parent.name}", cwd=s.REPO, check=False)
            with s.lock(s.root() / ".registry/lock"):
                records = s.registry()
                records.pop(integration.parent.name, None)
                s.save_registry(records)
            shutil.rmtree(integration.parent, ignore_errors=True)
