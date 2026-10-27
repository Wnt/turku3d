"""The commit and inbox messages the integrator writes: revert commits, the culprit's request
and the data-red warning."""

from __future__ import annotations

import coordination as coord
import sandbox as s


def revert_message(run, task, failure, commits):
    """Commit message of an integrator revert: integration row id, stage and exact commits."""
    lines = [
        f"Revert landing {task or 'unknown'} ({commits[-1][:12]}): integration {run.id} "
        f"red at {failure['stage']}",
        "",
        f"Integration: {run.id}",
        f"Failing stage: {failure['stage']}",
        f"Log: {failure['log']}",
        "Reverted commits:",
    ]
    for commit in commits:
        subject = s.git("log", "-1", "--format=%s", commit, cwd=run.main_repo)
        lines.append(f"  {commit} {subject}")
    return "\n".join([*lines, "", f"Detail: {failure['detail'][-300:]}"]) + "\n"


def notify(run, task, culprit, failure, reverted):
    detail = (
        f"reverted on main as {reverted['commit'][:12]}"
        if "commit" in reverted
        else f"NOT reverted ({reverted['error']})"
    )
    body = (
        f"Integration {run.id} is red at your landing {culprit[:12]}: {failure['stage']} "
        f"failed. Log: {failure['log']}. Detail: {failure['detail'][-400:]}\n"
        f"The change is {detail}; live stays on the last published release. Rebase onto "
        "main, fix it, and run codex-task finish again."
    )
    recipient = task if task and (s.tasks_root() / task / "meta.json").exists() else "orchestrator"
    try:
        coord.send(recipient, "request", f"integration red at {culprit[:12]}", body)
    except (ValueError, OSError):
        coord.send("orchestrator", "request", f"integration red at {culprit[:12]}", body)
    return recipient


def warn_data_red(run, row):
    """Tell everyone the data root, not a commit, turned the batch red; returns the subject."""
    failure = row["failure"]
    cause = (
        "The facade approval root's MANIFEST.json differs from the published release's."
        if row["data_cause"] == "approval-root-changed"
        else "The batch base itself is red against the current data root."
    )
    body = (
        f"Integration {run.id} ({row['from'][:12]}..{row['to'][:12]}) is data-red: the base "
        f"{row['from'][:12]} builds red at {failure['stage']}. {cause} No commit was "
        f"reverted; live stays on {row['from'][:12]}. The run is retried when main moves or "
        f"the data root changes. Log: {failure['log']}. Detail: {failure['detail'][-400:]}"
    )
    subject = f"integration {run.id} data-red at {failure['stage']}"
    try:
        coord.send("all", "warning", subject, body)
    except (ValueError, OSError):
        coord.send("orchestrator", "warning", subject, body)
    return subject
