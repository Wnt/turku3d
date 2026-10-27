"""The integrator's Chrome policy: batch plans and the full-suite schedule.

A code batch runs no Chrome (it ships the served tiles with a new game bundle); a data build
runs the @streaming specs when the playable tile list changed. Every 2 hours with a landing
since the last full run, the code lane runs the full suite on both projects
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


def full_plan():
    """The scheduled full run's plan: every spec on both projects."""
    return spec_selection.full_plan("scheduled full suite")


def data_plan(published, root):
    """A data build's plan from the specs in root: @streaming specs only when the playable
    tile list differs from the live release's."""
    delta = landing_class.data_delta(
        landing_class.data_parts(published),
        landing_class.release_parts((published / "live").resolve()),
    )
    return spec_selection.select([], "data", tiles_changed="playable-tiles" in delta, root=root)


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


def browser_load(rows_path, load_before):
    """Summarize host load and per-test frame p95 (tests/e2e/fixtures.ts rows) for one Chrome stage."""
    rows = []
    if rows_path.is_file():
        for line in rows_path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    paces = sorted(row["pace"]["p95FrameMs"] for row in rows if row.get("pace"))
    return {
        "host_load_start": [round(value, 2) for value in load_before],
        "host_load_end": [round(value, 2) for value in os.getloadavg()],
        "frame_p95_ms": (
            {"median": round(paces[len(paces) // 2], 1), "max": round(paces[-1], 1)}
            if paces
            else None
        ),
        "attempts": len(rows),
        "retried": [
            {
                "test": f"[{row['project']}] {row['file']} {row['title']}",
                "status": row["status"],
                "host_load": round(row["hostLoad"], 2),
            }
            for row in rows
            if row.get("retry")
        ],
    }


def load_summary(load):
    frame = load["frame_p95_ms"]
    return (
        f"host load {', '.join(f'{value:.2f}' for value in load['host_load_end'])}"
        f" (start {load['host_load_start'][0]:.2f}); frame p95 "
        + (f"median {frame['median']} ms, max {frame['max']} ms" if frame else "unmeasured")
        + f"; {len(load['retried'])} load retries"
    )
