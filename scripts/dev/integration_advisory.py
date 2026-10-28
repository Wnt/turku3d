"""Post-publish diagnostics, preempted by newer landings."""

import shutil
import subprocess

import advisory
import integration_chrome as chrome
import integration_reference
import pages
import sandbox as s


def finish(run, row, report, work, release, known, *, environment, registry, record):
    """Finish diagnostics after Pages; a waiting landing preempts owned command groups."""
    import live_build

    repo = s.sandbox(run.name) / "repo"
    env = environment(repo, run.published, run.lane)
    # Data builds can use an older tile builder; compare main with its head at publication,
    # not with the tile builder or the carried game's commit.
    head = s.git("rev-parse", "main", cwd=run.main_repo)

    def cancelled():
        return s.git("rev-parse", "main", cwd=run.main_repo) != head or (
            run.lane == "integration" and head != run.end
        )

    # Private reference jobs have their own local queue and inbox-only reporting.
    integration_reference.queue(run, row, work, env)

    log = registry() / f"integration-{run.id}-advisory.log"
    completed = False
    try:
        if cancelled():
            raise InterruptedError("newer landing waiting; advisory checks cut short")
        report = live_build.post_publish(
            repo,
            work,
            run.published,
            env,
            log,
            report,
            kind=run.kind,
            previous=run.previous,
            chrome=run.chrome,
            cancelled=cancelled,
        )
        row["advisory"] = report["advisory"]
        row["chrome"] = report.get("chrome")
        if run.full:
            chrome.mark_full_suite(run.published, row["advisory"], release=release)
        if run.lane == "integration" and run.full:
            deep_log = registry() / f"integration-{run.id}-deep.log"
            row["deep_gate_seconds"], deep = advisory.deep_gate(repo, env, deep_log, cancelled)
            row["advisory"] += deep
        completed = True
    except InterruptedError as exc:
        row["advisory"] = report["advisory"]
        row["advisory_interrupted"] = str(exc)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        row["advisory"] = report["advisory"]
        row["advisory"].append({"stage": "advisory", "detail": str(exc)[-1000:], "log": str(log)})
    # Write the exact release, never whichever release another lane has just published.
    path = release / "live-build.json"
    fields = {
        key: report[key]
        for key in ("chrome", "browser_load", "car_validation", "facade_ground_audit")
        if key in report
    }
    s.write_json(
        path,
        {
            **s.read_json(path, {}),
            **fields,
            "advisory": row["advisory"],
            "advisory_pending": False,
            "advisory_interrupted": row.get("advisory_interrupted"),
            "full_suite": bool(run.full and report.get("chrome")),
        },
    )
    if car := report.get("car_validation"):
        target = release / car["report"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(work / car["report"], target)
    row["advisory_seconds"] = report.get("advisory_seconds", {})
    row["full_suite"] = bool(run.full and report.get("chrome"))
    row["result"] = "green"
    row["warned"] = advisory.warn(run, row, known)
    # Updating the notes sends no game or tile bytes, and never overwrites a newer deployment.
    if completed and not cancelled() and row.get("pages", {}).get("commit"):
        row["pages_notes"] = pages.update_notes(run.published, release, row["pages"])
    return record(run, row)
