"""Tasks worked outside Codex: `codex-task adopt` registers them, `finish` hands them to the queue.

Their meta.json carries `worker: external`. The merge queue never resumes them through Codex;
it marks them `returned` and posts a request to the task inbox instead (see merge_queue.py).
"""

from __future__ import annotations

import time
from pathlib import Path

import sandbox as s


def adopt(name, brief):
    """Register an existing sandbox as a task worked by a non-Codex worker."""
    s.name_ok(name)
    home = s.sandbox(name)
    if not (home / "sandbox.env").exists():
        raise ValueError(f"no sandbox: {name}; create it with wt new first")
    path = s.tasks_root() / name
    if (path / "meta.json").exists():
        raise ValueError(f"task exists: {name}")
    brief_path = Path(brief).resolve()
    if not brief_path.is_file():
        raise ValueError(f"no brief: {brief_path}")
    path.mkdir(parents=True, exist_ok=True)
    now = time.time()
    meta = {
        "name": name,
        "repo": str(home / "repo"),
        "brief": str(brief_path),
        "created": now,
        "started": now,
        "state": "external",
        "worker": "external",
    }
    s.write_json(path / "meta.json", meta)
    print(f"{name}: adopted; record the result with codex-task finish")


def finish(name, result_path):
    """Record a result produced by a non-Codex worker so the merge queue can land the task.

    A new `finished` time also re-enqueues a task the queue returned to its worker.
    """
    from task_runner import running, task

    path = task(name)
    text = Path(result_path).read_text()
    if [line for line in text.splitlines() if line.startswith("## Result:")] != ["## Result: DONE"]:
        raise ValueError("result must contain an exact ## Result: DONE heading")
    with s.lock(path / "lock"):
        meta = s.read_json(path / "meta.json")
        if running(meta):
            raise ValueError("task is still running; stop it first")
        (path / "last.md").write_text(text)
        meta.update(state="done", exit_code=0, finished=time.time(), worker="external")
        meta["last_commit"] = s.git("log", "-1", "--format=%h %s", cwd=meta["repo"])
        s.write_json(path / "meta.json", meta)
    print(f"{name}: DONE result recorded; the merge queue lands it after precheck")
