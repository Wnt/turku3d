"""The integrator's Chrome policy: batch plans and the full-suite schedule.

A batch runs the specs spec_selection picks for its class and paths. Every 2 hours with a
landing since the last full run, the code lane runs the full suite on both projects
(`full_suite: true`) from the live commit. Chrome is advisory: failing specs are recorded and
reported to the batch's authors; they never block publication or bisect.
"""

from __future__ import annotations

import json
import os

import landing_class
import spec_selection

FULL_INTERVAL = 2 * 3600


def last_full(rows):
    """The newest full-suite integration row, or None."""
    for row in reversed(rows):
        if row.get("full_suite"):
            return row
    return None


def full_due(rows, landed, now):
    """A full run is due 2 h after the last one when a landing arrived since it."""
    previous = last_full(rows)
    since = previous["time"] if previous else 0.0
    return now - since >= FULL_INTERVAL and any(row.get("time", 0) > since for row in landed)


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


def mark_full_suite(published, advisory):
    """Record in the live release that the full Chrome suite ran on it, with the advisory
    failures of that run."""
    path = record_path(published)
    record = json.loads(path.read_text())
    pending = path.with_suffix(".tmp")
    record = {**record, "full_suite": True, "advisory": advisory}
    pending.write_text(json.dumps(record, indent=2) + "\n")
    os.replace(pending, path)
