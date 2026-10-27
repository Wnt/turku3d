"""Advisory checks of the live build: what blocks publication, what only gets reported.

User decision (2026-10-27 21:00): a batch whose build stages succeed is published to live and
Pages. Only BUILD_STAGES (and tile-validate's BLOCKING_CHECKS) mean "cannot be built"; every
other live stage, the walk checks and the facade baseline are advisory. Their failures are
`{"stage", "detail", "log", "specs"?, "checks"?}` entries in the build report, the ledger row
and the release record; `warn` sends the new ones to the batch's authors.
"""

from __future__ import annotations

import json

import coordination as coord
import landing
import sandbox as s

# Stages whose failure means the batch cannot be built; the others are advisory.
BUILD_STAGES = ("refresh", "tile-build", "tile-validate", "game-build")
# tile-validate checks that block publication; its other checks (buildings, curbs) are advisory.
BLOCKING_CHECKS = ("format", "seams")


def facade_drop(report):
    """The advisory entry for fewer bound facades than the published release with no reported
    reason, or None."""
    bound, baseline = report["facades_bound"], report["facades_baseline"]
    if bound >= baseline or report["facade_regression_reason"]:
        return None
    return {
        "stage": "facade-baseline",
        "detail": f"facades_bound regressed: {bound} < {baseline}; no reported reason",
        "facades_bound": bound,
        "facades_baseline": baseline,
    }


def tile_checks(path, returncode):
    """The failing advisory checks of a tile-validate run (validator report at path).

    Raises ValueError when the validator itself failed or a blocking check (format, seams)
    failed: then the tiles cannot be shipped."""
    try:
        checks = json.loads(path.read_text())["checks"]
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError(f"no validator report ({exc})") from exc
    failing = sorted(name for name, check in checks.items() if check.get("status") == "fail")
    blocking = [name for name in failing if name in BLOCKING_CHECKS]
    if returncode not in (0, 1) or blocking or (returncode == 1) != bool(failing):
        raise ValueError(
            f"tile-validate failed: {', '.join(blocking) or f'validator exit {returncode}'}"
        )
    return failing


def car_report(path, returncode):
    """Preserve known-red car diagnostics without accepting validator load errors."""
    if returncode not in (0, 1) or not path.is_file():
        raise ValueError(
            f"car report failed ({returncode}); report missing or validator error: {path}"
        )
    data = json.loads(path.read_text())
    cars = data.get("checks", {}).get("cars", {})
    status = cars.get("status")
    if status not in ("pass", "fail") or bool(data.get("pass")) != (status == "pass"):
        raise ValueError(f"car report has no usable car result: {path}")
    if returncode != (0 if status == "pass" else 1):
        raise ValueError(f"car report exit code disagrees with result: {path}")
    per_tile = cars.get("perTile", [])
    if not per_tile:
        raise ValueError(f"car report has no tested tiles: {path}")
    return {
        "status": status,
        "failed_tiles": sum(tile.get("status") == "fail" for tile in per_tile),
        "tested_tiles": len(per_tile),
        "report": "reports/validate-cars.json",
        "known_red_reason": "Published car probes still fail curb response and four wall/building crossings",
    }


def labels(entries):
    """One label per failing spec, check or audit of an advisory list."""
    out = []
    for entry in entries or ():
        parts = entry.get("specs") or entry.get("checks")
        out += [f"{entry['stage']} {part}" for part in parts] if parts else [entry["stage"]]
    return out


def release_advisory(release):
    """The advisory failures recorded in a published release ([] for none or unknown)."""
    try:
        return json.loads((release / "live-build.json").read_text()).get("advisory") or []
    except (OSError, ValueError):
        return []


def recipients(main_repo, start, end):
    """The tasks whose landings are in start..end (the orchestrator when none is known)."""
    names = []
    if start != end:
        for commit in landing.landed_heads(start, end, main_repo):
            task = landing.author(commit)
            if task and task not in names and (s.tasks_root() / task / "meta.json").exists():
                names.append(task)
    return names or ["orchestrator"]


def warn(run, row, known):
    """Send the batch's authors the advisory failures that the replaced release (`known`, its
    advisory list) did not have. Returns the recipients, or [] when nothing new failed: known
    failures stay in the row, the release record and the Pages notes."""
    old = set(labels(known))
    failing = labels(row["advisory"])
    new = [label for label in failing if label not in old]
    if not new:
        return []
    lines = [
        f"  {label}{'' if label in new else ' (already red in the previous release)'}"
        for label in failing
    ]
    logs = sorted({entry["log"] for entry in row["advisory"] if entry.get("log")})
    body = "\n".join(
        [
            f"Integration {run.id} ({row['from'][:12]}..{row['to'][:12]}) published "
            f"{row['to'][:12]} to live and Pages with failing advisory checks:",
            *lines,
            f"Logs: {', '.join(logs) or 'none'}.",
            "Nothing was reverted. If one of these is yours, fix it in a new landing; Jonni "
            "decides the order of fixes from the Pages release notes.",
        ]
    )
    subject = f"integration {run.id} published with failing checks: {', '.join(new[:3])}"
    sent = []
    for name in recipients(run.main_repo, row["from"], row["to"]):
        try:
            coord.send(name, "warning", subject, body)
        except (ValueError, OSError):
            continue
        sent.append(name)
    return sent
