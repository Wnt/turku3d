"""The lander: rebase, fast gate and fast-forward merge of a finished task branch onto main.

`codex-task finish` (external workers), the Codex runner at turn end and `codex-task land`
(the orchestrator) all run land(), serialised with `wt/.registry/land.lock`. After the merge it
appends the ledger row, publishes the public line, sends a `landed` message to every other
active task, starts the integrator when none runs and removes the sandbox. No host slot, no
live build and no receipt: integrator.py builds and verifies the live release asynchronously.
"""

import contextlib
import json
import os
import subprocess
import time

import browser_budget
import coordination as coord
import landing_class
import sandbox as s

BUDGET_SECONDS = 180
FACADE_MARKER = "Facade regression reason:"
PUBLIC_REF = "refs/public/main"
PUSH_SECONDS = 60


class Conflict(ValueError):
    """The rebase onto main conflicts; the rebase is aborted and nothing else changed."""


def ledger_path():
    return s.root() / ".registry/landed.jsonl"


def classes(repo, ranges):
    """Landing class fields for each (base, head) range of one landing, in order.

    The data delta compares the data root with the last ledger row that recorded its
    parts (else with the live release), so a data-root change is attributed to the first
    landing after it.
    """
    published = landing_class.published_root()
    current = landing_class.data_parts(published)
    baseline = None
    if ledger_path().is_file():
        for line in reversed(ledger_path().read_text().splitlines()):
            with contextlib.suppress(ValueError):
                if baseline := json.loads(line).get("data_parts"):
                    break
    if baseline is None:
        baseline = landing_class.release_parts((published / "live").resolve())
    delta = landing_class.data_delta(current, baseline)
    fields = []
    for base, head in ranges:
        fields.append(
            {**landing_class.classify(repo, base, head, delta, published), "data_parts": current}
        )
        delta = []
    return fields


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


def landed_heads(start, end, repo):
    """Bisection points in start..end: landed heads from the ledger, always ending at end."""
    commits = s.git(
        "rev-list", "--reverse", "--first-parent", f"{start}..{end}", cwd=repo
    ).splitlines()
    heads = {row.get("commit") for row in ledger_rows()}
    points = [commit for commit in commits if commit in heads]
    return points if points[-1:] == [end] else [*points, end]


def facade_reason_in(start, end, repo):
    """The reported facade regression reason of any landing in start..end, or None."""
    commits = set(s.git("rev-list", f"{start}..{end}", cwd=repo).splitlines())
    return next(
        (
            row["facade_regression_reason"]
            for row in ledger_rows()
            if row.get("commit") in commits and row.get("facade_regression_reason")
        ),
        None,
    )


def author(commit):
    """The task whose landing produced commit."""
    return next(
        (row.get("name") for row in reversed(ledger_rows()) if row.get("commit") == commit), None
    )


def member(name):
    """A finished task's (path, meta, result text): DONE result recorded."""
    from task_runner import task

    path = task(name)
    meta = s.read_json(path / "meta.json")
    if meta.get("state") != "done" or meta.get("exit_code") != 0:
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
    s.reap_browsers(name)
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


def push_public(repo):
    """Publish main's tree to `origin` as the next commit of the public line; best effort.

    The public repository starts from one squashed import because older local history holds
    street imagery, so local `main` itself is never pushed. Each landing adds one commit with
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
            message = f"{subject}\n\nLanded as local main {source}."
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


def record(name, path, text, before, commit, started, gate, landing, push, lock_wait):
    """Append the ledger row and store it in meta; `first_finished` is never overwritten.

    `landing` holds the class fields (classes()). The best-effort push to origin is
    reported in `push` and excluded from the merge time, like the wait for land.lock
    (`lock_wait_seconds`).
    """
    seconds, stages = gate
    elapsed = round(time.monotonic() - started - (push or {}).get("seconds", 0), 3)
    with s.lock(path / "lock"):
        meta = s.read_json(path / "meta.json")
        row = {
            "name": name,
            "before": before,
            "commit": commit,
            "source": commit,
            "time": time.time(),
            "merge_wall_seconds": elapsed,
            "merge_slow": elapsed > BUDGET_SECONDS,
            "lock_wait_seconds": lock_wait,
            "fast_gate_seconds": seconds,
            "fast_gate_stages": stages,
            "first_done_time": meta.get("first_finished", meta.get("finished")),
            "done_time": meta.get("finished"),
            "facade_regression_reason": facade_reason(text),
            # The task's agent Chrome use (browser_budget.py); None for an unbudgeted task.
            "browser_minutes": (browser_budget.read(name) or {}).get("used_minutes"),
            **landing,
        }
        if push is not None:
            row["push"] = push
        meta["landed"] = row
        s.write_json(path / "meta.json", meta)
    with ledger_path().open("a") as ledger:
        ledger.write(json.dumps(row) + "\n")
    return row


def land(name, keep_sandbox=False, note=None):
    """Land one finished task on main in at most BUDGET_SECONDS; return its ledger row.

    A rebase conflict or a red fast gate raises with main and the sandbox unchanged; the
    worker fixes it on its branch and finishes again. Open requests in the task inbox are
    reported, not held. `keep_sandbox` keeps the sandbox for a task that continues in a next
    slice (an external worker becomes active again).
    """
    from task_runner import cleanup_landed, task

    waited = time.monotonic()
    with s.lock(s.root() / ".registry/land.lock"), s.lock(task(name) / "lock"):
        started = time.monotonic()
        deadline = started + BUDGET_SECONDS
        path, meta, text = member(name)
        landed = meta.get("landed")
        if landed and landed.get("done_time") == meta.get("finished"):
            print(f"{name}: already landed {landed['commit'][:12]}; finish again to land more")
            return landed
        repo = checkout(name)
        main = main_worktree(repo)
        for _attempt in range(3):
            before = s.git("rev-parse", "main", cwd=repo)
            if conflicts := rebase(repo):
                raise Conflict(
                    f"{name}: rebase onto main conflicts in {', '.join(conflicts)}; main and the "
                    "sandbox are unchanged. Run git rebase main in the sandbox, resolve, rerun "
                    "scripts/dev/gate --fast --base main, then finish again."
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
        [landing] = classes(repo, [(before, head)])
        over_budget([path], started, BUDGET_SECONDS, "merge")
        advance(repo, main, before, head)
        pushed = push_public(repo)
        lock_wait = round(started - waited, 3)
        row = record(
            name, path, text, before, head, started, (seconds, stages), landing, pushed, lock_wait
        )
        if keep_sandbox and meta.get("worker") == "external":
            with s.lock(path / "lock"):
                current = s.read_json(path / "meta.json")
                current["state"] = "external"
                s.write_json(path / "meta.json", current)
        subject = s.git("log", "-1", "--format=%s", head, cwd=repo)
    print(
        f"{name}: landed {head[:12]} as {row['class']} in {row['merge_wall_seconds']}s "
        f"(fast gate {seconds}s, waited {lock_wait}s for land.lock): {subject}"
    )
    report(name, row, subject, (landed or {}).get("time", 0), note)
    if keep_sandbox:
        print(f"{name}: sandbox kept for the next slice")
    else:
        cleanup_landed(name)
    return row


def report(name, row, subject, since, note):
    """After the merge: open requests, the broadcast and the integrator start; best effort."""
    try:
        for message in coord.open_requests(name):
            print(
                f"{name}: open {message['kind']} {message['id']} from {message['from']}: "
                f"{message['subject']} (not holding; you decide)"
            )
        recipients = coord.broadcast_landed(name, row, subject, since, note)
        print(f"{name}: landed message sent to {', '.join(recipients) or 'no active task'}")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"{name}: landed, broadcast failed: {error}")
    try:
        import integrator

        lanes = integrator.ensure(landing_class.published_root())
        print(
            f"{name}: integrator "
            + (
                f"started ({', '.join(lanes)})"
                if lanes
                else "not started (running, settled or no live release)"
            )
        )
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"{name}: landed, integrator start failed: {error}")
