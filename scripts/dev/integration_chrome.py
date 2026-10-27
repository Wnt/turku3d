"""The integrator's Chrome policy: batch plans, the full-suite schedule and Pages readiness.

A batch runs the specs spec_selection picks for its class and paths. Every 2 hours with a
landing since the last full run, the code lane runs the full suite on both projects
(`full_suite: true`) from the commit of the last green full run, so a red full run bisects
every landing since then. Pages publishes only a release a green full run verified.
"""

from __future__ import annotations

import json
import os

import landing_class
import sandbox as s
import spec_selection

FULL_INTERVAL = 2 * 3600


def last_full(rows, *, green=False):
    """The newest full-suite integration row (green only when asked), or None."""
    for row in reversed(rows):
        if row.get("full_suite") and (not green or row.get("result") == "green"):
            return row
    return None


def full_due(rows, landed, now):
    """A full run is due 2 h after the last one when a landing arrived since it."""
    previous = last_full(rows)
    since = previous["time"] if previous else 0.0
    return now - since >= FULL_INTERVAL and any(row.get("time", 0) > since for row in landed)


def full_base(rows, main_repo, live, end):
    """G of a full run: the last green full run's commit when main contains it, else live."""
    row = last_full(rows, green=True)
    commit = row and row.get("to")
    if not commit:
        return live
    ancestor = s.run("git", "merge-base", "--is-ancestor", commit, end, cwd=main_repo, check=False)
    return commit if ancestor.returncode == 0 else live


def batch_plan(main_repo, start, end, landing, full=False):
    """The batch's plan from its class, its paths and the playable tile list delta; a full
    run's plan is every spec on both projects."""
    if full:
        return spec_selection.full_plan("scheduled full suite")
    return spec_selection.select(
        landing_class.changed_paths(main_repo, start, end),
        landing["class"],
        tiles_changed="playable-tiles" in landing["data_delta"],
        root=main_repo,
    )


def data_plan(published):
    """A data build's plan: @streaming specs only when the playable tile list differs from
    the live release's."""
    delta = landing_class.data_delta(
        landing_class.data_parts(published),
        landing_class.release_parts((published / "live").resolve()),
    )
    return spec_selection.select([], "data", tiles_changed="playable-tiles" in delta)


def record_path(published):
    return (published / "live").resolve() / "live-build.json"


def mark_full_suite(published):
    """Record in the live release that a green full Chrome suite verified it."""
    path = record_path(published)
    record = json.loads(path.read_text())
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps({**record, "full_suite": True}, indent=2) + "\n")
    os.replace(pending, path)


def verified(published):
    """Whether a green full Chrome suite verified the live release (Pages may publish it)."""
    return bool(json.loads(record_path(published).read_text()).get("full_suite"))
