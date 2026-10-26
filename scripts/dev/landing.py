"""Batch integration with one gate and failing-prefix bisection; main stays atomic."""

import contextlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import coordination as coord
import live_build
import sandbox as s


def fast(name):
    """Publish an unchanged-main, fully prechecked branch without running a build."""
    import verification
    from task_runner import cleanup_landed, main_checkout, running, task

    started = time.monotonic()
    path = task(name)
    repo = s.sandbox(name) / "repo"
    with s.lock(s.root() / ".registry/land.lock"), s.lock(path / "lock"):
        meta = s.read_json(path / "meta.json")
        if running(meta) or meta.get("state") != "done" or meta.get("exit_code") != 0:
            raise ValueError(f"{name}: task is not DONE")
        if not (path / "last.md").is_file() or [
            line
            for line in (path / "last.md").read_text().splitlines()
            if line.startswith("## Result:")
        ] != ["## Result: DONE"]:
            raise ValueError(f"{name}: DONE result is missing")
        if (
            s.git("status", "--porcelain", cwd=repo)
            or s.git("branch", "--show-current", cwd=repo) != f"wt/{name}"
        ):
            raise ValueError(f"{name}: branch is dirty or unexpected")
        before = s.git("rev-parse", "main", cwd=repo)
        receipt = s.read_json(path / "precheck.json", {})
        published = Path(os.environ["TURKU3D_DATA"])
        verification.validate(receipt, repo, published)
        if receipt["main"] != before or s.git("merge-base", "HEAD", "main", cwd=repo) != before:
            raise ValueError(f"{name}: precheck is stale; worker must recheck")
        main = main_checkout(repo)
        if main and s.git("status", "--porcelain", cwd=main):
            raise ValueError("main worktree is dirty")
        if time.monotonic() - started > 110:
            raise ValueError("merge verification is slow; 2-minute budget would be exceeded")
        with coord.landing_guard(name, force=False):
            if main:
                s.git("merge", "--ff-only", receipt["head"], cwd=main)
            else:
                s.git("update-ref", "refs/heads/main", receipt["head"], before, cwd=repo)
            live_build.publish(published, receipt["head"], receipt["release_id"])
        elapsed = round(time.monotonic() - started, 3)
        queued = s.read_json(s.root() / ".registry/merge-queue.json", {"tasks": {}})["tasks"].get(
            name, {}
        )
        record = {
            "name": name,
            "before": before,
            "commit": receipt["head"],
            "source": receipt["head"],
            "time": time.time(),
            "merge_wall_seconds": elapsed,
            "merge_slow": elapsed > 120,
            "verification": receipt["release_id"],
            "live_build": receipt["live_build"],
            "done_time": queued.get("done", meta.get("finished")),
            "enqueue_time": queued.get("enqueue"),
            "start_time": queued.get("started"),
        }
        meta["landed"] = record
        s.write_json(path / "meta.json", meta)
        with (s.root() / ".registry/landed.jsonl").open("a") as ledger:
            ledger.write(json.dumps(record) + "\n")
        cleanup_landed(name, receipt["head"])
        return record


def batch(names, force=False):
    from task_runner import cleanup_landed, main_checkout, running, task

    if len(set(names)) != len(names):
        raise ValueError("batch members must be unique")
    with s.lock(s.root() / ".registry/land.lock"), contextlib.ExitStack() as locks:
        members = []
        for name in names:
            path = task(name)
            locks.enter_context(s.lock(path / "lock"))
            meta = s.read_json(path / "meta.json")
            if running(meta) or meta.get("state") != "done" or meta.get("exit_code") != 0:
                raise ValueError(f"{name}: task must have completed successfully")
            result = path / "last.md"
            if not result.exists() or [
                line for line in result.read_text().splitlines() if line.startswith("## Result:")
            ] != ["## Result: DONE"]:
                raise ValueError(f"{name}: result must contain an exact ## Result: DONE heading")
            repo = s.sandbox(name) / "repo"
            if s.git("branch", "--show-current", cwd=repo) != f"wt/{name}" or s.git(
                "status", "--porcelain", cwd=repo
            ):
                raise ValueError(f"{name}: sandbox is dirty or on an unexpected branch")
            if not (repo / "scripts/dev/gate").is_file():
                raise ValueError(f"{name}: missing scripts/dev/gate")
            with coord.landing_guard(name, force):
                pass
            s.reap_browsers(name)
            members.append((name, path, meta, s.git("rev-parse", "HEAD", cwd=repo)))
        main = main_checkout(repo)
        if main and s.git("status", "--porcelain", cwd=main):
            raise ValueError("main worktree is dirty")
        before = s.git("rev-parse", "main", cwd=repo)
        gate_base = before
        integration = s.new("land-batch-" + uuid.uuid4().hex[:10], before)
        env = {**os.environ, **s.read_env(integration.parent / "sandbox.env")}
        env["TURKU3D_VERIFY_BASE"] = before
        log_path = s.tasks_root() / ("batch-" + integration.parent.name + ".log")
        started = time.monotonic()
        commits = []
        build_report = None
        gate_failure = ""
        gate_cache_hits = []
        gate_stages = {}

        def assemble(count):
            s.git("reset", "--hard", before, cwd=integration)
            commits.clear()
            for name, _, _, head in members[:count]:
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
                        s.git("cherry-pick", "--abort", cwd=integration, check=False)
                        raise ValueError(f"{name}: integration conflict; main unchanged") from None
                commits.append(s.git("rev-parse", "HEAD", cwd=integration))

        def gate(log):
            nonlocal gate_failure
            head = s.git("rev-parse", "HEAD", cwd=integration)
            result = s.run_group(
                [str(integration / "scripts/dev/gate"), "--base", gate_base],
                cwd=integration,
                env=env,
            )
            log.write(result.stdout)
            log.flush()
            gate_cache_hits[:] = [
                line.split()[2]
                for line in result.stdout.splitlines()
                if line.startswith("cached PASS") and len(line.split()) > 2
            ]
            gate_stages.clear()
            for line in result.stdout.splitlines():
                fields = line.split()
                if fields and fields[0] == "cached":
                    fields = fields[1:]
                if len(fields) >= 3 and fields[0] in ("PASS", "FAIL") and fields[2].endswith("s"):
                    try:
                        gate_stages[fields[1]] = float(fields[2][:-1])
                    except ValueError:
                        pass
            code = result.returncode
            if (
                s.git("status", "--porcelain", cwd=integration)
                or s.git("rev-parse", "HEAD", cwd=integration) != head
            ):
                raise ValueError("gate changed integration files or HEAD; main unchanged")
            if code:
                gate_failure = (
                    f"failing test: {s.failure_detail(result.stdout)}; host load {s.host_load()}"
                )
                log.write(gate_failure + "\n")
            return code == 0

        try:
            for _attempt in range(3):
                before = s.git("rev-parse", "main", cwd=integration)
                assemble(len(members))
                with log_path.open("w") as log:
                    if not gate(log):
                        low, high = 0, len(members)
                        # Locate the bisected failing prefix, retaining interactions with predecessors.
                        assemble(0)
                        if not gate(log):
                            raise ValueError(
                                f"base gate fails; {gate_failure}; main unchanged; {log_path}"
                            )
                        while high - low > 1:
                            middle = (low + high) // 2
                            assemble(middle)
                            if gate(log):
                                low = middle
                            else:
                                high = middle
                        raise ValueError(
                            f"batch bisected failing prefix ends at {names[high - 1]}; {gate_failure}; main unchanged; {log_path}"
                        )
                build_report = live_build.build(
                    integration,
                    Path(env["TURKU3D_WORK"]),
                    Path(env["TURKU3D_DATA"]),
                    env,
                    log_path.with_suffix(".live.log"),
                    next(
                        (
                            line.partition(":")[2].strip()
                            for _name, member_path, _meta, _head in members
                            for line in (member_path / "last.md").read_text().splitlines()
                            if line.startswith("Facade regression reason:")
                        ),
                        None,
                    ),
                )
                if s.git("status", "--porcelain", cwd=integration):
                    raise ValueError("live build changed integration files; main unchanged")
                if s.git("rev-parse", "main", cwd=integration) == before:
                    break
            else:
                raise ValueError("main changed during batch gate three times; retry batch")
            live_build.prepare(
                Path(env["TURKU3D_WORK"]), Path(env["TURKU3D_DATA"]), commits[-1], build_report
            )
            with contextlib.ExitStack() as guards:
                for name, _path, _, head in members:
                    guards.enter_context(coord.landing_guard(name, force))
                    member_repo = s.sandbox(name) / "repo"
                    if (
                        s.git("status", "--porcelain", cwd=member_repo)
                        or s.git("rev-parse", "HEAD", cwd=member_repo) != head
                    ):
                        raise ValueError(f"{name}: member changed during gate")
                if s.git("rev-parse", "main", cwd=integration) != before:
                    raise ValueError("main changed during batch gate; retry batch")
                if main:
                    if s.git("status", "--porcelain", cwd=main):
                        raise ValueError("main became dirty during gate")
                    s.git("merge", "--ff-only", commits[-1], cwd=main)
                else:
                    s.git("update-ref", "refs/heads/main", commits[-1], before, cwd=integration)
                live_build.publish(Path(env["TURKU3D_DATA"]), commits[-1])
                with (s.root() / ".registry/landed.jsonl").open("a") as ledger:
                    queue_rows = s.read_json(
                        s.root() / ".registry/merge-queue.json", {"tasks": {}}
                    ).get("tasks", {})
                    for (name, path, meta, source), commit in zip(members, commits, strict=True):
                        queued = queue_rows.get(name, {})
                        record = {
                            "name": name,
                            "before": before,
                            "commit": commit,
                            "source": source,
                            "batch": names,
                            "time": time.time(),
                            "gate_seconds": round(time.monotonic() - started, 3),
                            "gate_cache_hits": gate_cache_hits,
                            "gate_stages": gate_stages,
                            "live_build": build_report,
                            "done_time": queued.get("done", meta.get("finished")),
                            "enqueue_time": queued.get("enqueue"),
                            "start_time": queued.get("started"),
                        }
                        meta["landed"] = record
                        s.write_json(path / "meta.json", meta)
                        ledger.write(json.dumps(record) + "\n")
                print(
                    f"batch landed: {', '.join(names)}; live stages {build_report['seconds']}; {log_path}"
                )
                for (name, _, _, _), commit in zip(members, commits, strict=True):
                    cleanup_landed(name, commit)
        finally:
            # This process created the integration checkout; member worktrees are untouched.
            s.git("worktree", "remove", "--force", str(integration), check=False)
            s.git("branch", "-D", f"wt/{integration.parent.name}", cwd=s.REPO, check=False)
            with s.lock(s.root() / ".registry/lock"):
                records = s.registry()
                records.pop(integration.parent.name, None)
                s.save_registry(records)
            shutil.rmtree(integration.parent, ignore_errors=True)
