"""The single active-agent register: task, actual branch, state and checkout."""

import concurrent.futures
from pathlib import Path

import sandbox as s
import task_view


def refresh():
    processes = task_view.process_snapshot()
    active = [
        (path, meta)
        for path, meta in task_view.task_rows(s.tasks_root())
        if (meta.get("worker") == "external" and meta.get("state") == "external")
        or (meta.get("state") in task_view.ACTIVE and task_view.is_live(meta, processes))
    ]

    def row(item):
        path, meta = item
        repo = meta.get("repo")
        if not repo or not Path(repo).is_dir():
            return None
        branch = s.git("branch", "--show-current", cwd=repo, check=False) if repo else ""
        return {"name": path.name, "branch": branch, "state": meta["state"], "repo": repo}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(active))) as pool:
        rows = [row for row in pool.map(row, active) if row is not None]
    s.write_json(s.root() / ".registry/agents.json", rows)
    return rows
