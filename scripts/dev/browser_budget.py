"""Per-task browser budget: browser minutes (Playwright wall seconds x workers) per task.

Every agent Chrome run through `host_slots.py browser` debits the running task's budget in
`~/turku3d/tasks/<task>/browser-budget.json`; once it is spent, further runs are refused.
The default comes from `gate.toml` (`[browser] budget_minutes`); a brief overrides it with a
line `BROWSER_BUDGET_MIN = <minutes>`. Sandboxes without a task (main, the integrator) and
commands inside an integration reservation are not budgeted.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FILE = "browser-budget.json"
_OVERRIDE = re.compile(r"\bBROWSER_BUDGET_MIN\s*=\s*(\d+(?:\.\d+)?)")


def tasks_root():
    return Path(os.environ.get("TURKU3D_TASKS_ROOT", Path.home() / "turku3d/tasks")).expanduser()


def default_minutes(root=REPO):
    config = tomllib.loads((Path(root) / "scripts/dev/gate.toml").read_text())
    return float(config["browser"]["budget_minutes"])


def task_dir(task, tasks=None):
    """The task's directory when the sandbox runs a registered task, else None."""
    if not task or task == "main":
        return None
    path = Path(tasks or tasks_root()) / task
    return path if (path / "meta.json").is_file() else None


def budget_minutes(task, root=REPO, tasks=None):
    """The brief's `BROWSER_BUDGET_MIN = N` line, else gate.toml's default."""
    directory = task_dir(task, tasks)
    meta = json.loads((directory / "meta.json").read_text()) if directory else {}
    brief = Path(meta.get("brief", "")) if meta.get("brief") else None
    if brief and brief.is_file():
        found = _OVERRIDE.search(brief.read_text())
        if found:
            return float(found.group(1))
    return default_minutes(root)


@contextlib.contextmanager
def _locked(directory):
    with (directory / "browser-budget.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read(task, root=REPO, tasks=None):
    """The task's accounting, or None for an unbudgeted sandbox."""
    directory = task_dir(task, tasks)
    if directory is None:
        return None
    path = directory / FILE
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        data = {"task": task, "runs": []}
    budget = budget_minutes(task, root, tasks)
    used = round(sum(run["browser_minutes"] for run in data["runs"]), 3)
    return {
        **data,
        "budget_minutes": budget,
        "used_minutes": used,
        "remaining_minutes": round(budget - used, 3),
    }


def debit(task, command, seconds, workers, root=REPO, tasks=None):
    """Record one browser run; returns the updated accounting (None when unbudgeted)."""
    directory = task_dir(task, tasks)
    if directory is None:
        return None
    with _locked(directory):
        current = read(task, root, tasks)
        entry = {
            "time": time.time(),
            "command": " ".join([Path(command[0]).name, *command[1:]])[:300],
            "specs": [part for part in command if part.endswith(".spec.ts")],
            "projects": [
                command[i + 1] for i, part in enumerate(command[:-1]) if part == "--project"
            ],
            "seconds": round(seconds, 3),
            "workers": workers,
            "browser_minutes": round(seconds * workers / 60, 3),
        }
        data = {"task": task, "runs": [*current["runs"], entry]}
        path = directory / FILE
        pending = path.with_name(f".{FILE}.{os.getpid()}.tmp")
        pending.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(pending, path)
    return read(task, root, tasks)


def summary(account):
    return (
        f"browser budget: {account['used_minutes']:.1f} of {account['budget_minutes']:g} "
        f"browser-minutes used, {max(account['remaining_minutes'], 0):.1f} left"
    )


def refusal(account):
    """The refusal message when the budget is spent, else None."""
    if account is None or account["remaining_minutes"] > 0:
        return None
    return (
        f"browser budget spent ({account['used_minutes']:.1f} of {account['budget_minutes']:g} "
        "browser-minutes): no further Chrome runs for this task. Finish with what you have and "
        "state it in the result and select relevant checks for the bounded agent-owned async run."
    )


def run(task, command, env):
    """Run one admitted browser command and debit its wall seconds x TURKU3D_JOBS workers."""
    started = time.monotonic()
    code = subprocess.call(command, env=env)
    account = debit(task, command, time.monotonic() - started, int(env.get("TURKU3D_JOBS", "1")))
    if account is not None:
        print(summary(account), file=sys.stderr, flush=True)
    return code
