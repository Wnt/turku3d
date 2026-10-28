"""Explicit Chrome diagnostics and browser load reporting.

Normal integrations run no Chrome. Request `integrator.py run --full` for a full
suite after publication, or run a focused Playwright spec in the worker sandbox.
"""

from __future__ import annotations

import json
import os

import spec_selection


def full_plan():
    """The explicit full run's plan: every spec on both projects."""
    return spec_selection.full_plan("explicit full suite")


def data_plan(published, root):
    """Data publication runs no Chrome; focused streaming checks are explicit."""
    return spec_selection.select([], "data", tiles_changed=False, root=root)


def record_path(published):
    return (published / "live").resolve() / "live-build.json"


def mark_full_suite(published, advisory, *, release=None):
    """Record in the live release that the full Chrome suite ran on it, with the advisory
    failures of that run."""
    path = release / "live-build.json" if release else record_path(published)
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
