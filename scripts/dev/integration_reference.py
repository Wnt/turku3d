"""Private draft diagnostics. Nothing from this module enters a release row or Pages."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import advisory
import coordination as coord
import draft_reference_score as score
import sandbox as s


def changed(repo, before, after):
    return before != after and bool(
        s.git("diff", "--name-only", before, after, "--", "pipeline/turku3d/coarse/", cwd=repo)
    )


def queue(run, row, work, env):
    """Freeze a batch before the integration checkout moves; run diagnostics independently.

    This is intentionally outside the public advisory collection and its failure handling.
    It does not block publication or get cancelled when another landing arrives.
    """
    directory = work / "reports/draft-reference-advisory" / run.id
    try:
        if not score.private_tool() or not changed(run.main_repo, row["from"], row["to"]):
            return
        directory.mkdir(parents=True, exist_ok=False)
        for side, version in (("before", row["from"]), ("after", row["to"])):
            score.snapshot(version, directory / side, run.main_repo)
        request = {
            "batch": run.id,
            "before": str(directory / "before"),
            "after": str(directory / "after"),
            "recipients": sorted(
                set(["orchestrator", *advisory.recipients(run.main_repo, row["from"], row["to"])])
            ),
        }
        s.write_json(directory / "request.json", request)
        # Own code snapshot keeps this worker alive across the next integration checkout.
        # Both versions predate the hook on its first landing; use this module's stable copy.
        worker = directory / "worker"
        worker.mkdir()
        for name in ("integration_reference.py", "draft_reference_score.py"):
            (worker / name).write_bytes(Path(__file__).with_name(name).read_bytes())
        # The worker imports the current tooling from its frozen after snapshot.
        command = [
            sys.executable,
            str(worker / "integration_reference.py"),
            "--request",
            str(directory / "request.json"),
        ]
        worker_env = {**env, "PYTHONPATH": str(directory / "after/scripts/dev")}
        worker_env.pop("TURKU3D_INTEGRATION_OWNER", None)
        with (directory / "worker.log").open("w") as log:
            subprocess.Popen(
                command,
                cwd=directory / "after",
                env=worker_env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
    except FileExistsError:
        # Integration retries must not enqueue the same batch twice.
        return
    except (OSError, ValueError, subprocess.CalledProcessError):
        # Do not leak an exception, tool output or diagnostics into the public advisory list.
        private_notice(
            ["orchestrator"],
            "Private draft advisory could not start",
            f"Batch {run.id}; inspect local directory {directory}.",
        )


def private_notice(recipients, subject, body):
    for recipient in recipients:
        with contextlib.suppress(OSError, ValueError):
            # post() writes only the addressed inbox. send() also writes console notices.
            coord.post(coord.actor(), recipient, "warning", subject, body)


def enqueue(request, directory, builds, queue_root):
    """The private standing watcher owns score serialization, results and notifications."""
    pending = queue_root / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    ids = []
    for tile in score.TILES:
        identifier = f"draft-{request['batch'].replace('.', '-')}-{tile}"
        job = {
            "version": 1,
            "id": identifier,
            "requester": "ref-score-tool",
            "command": "diff",
            "tile": tile,
            "before": str(directory / "builds/before/coarse"),
            "after": str(directory / "builds/after/coarse"),
            "notify_recipients": request["recipients"],
            "batch": request["batch"],
            "noise": 0.00001,
            "informational_metrics": sorted(score.INFORMATIONAL),
        }
        # A watcher can never observe half a request. Temp files are not *.json.
        temporary = pending / f".{identifier}.tmp"
        temporary.write_text(json.dumps(job) + "\n")
        temporary.replace(pending / f"{identifier}.json")
        ids.append(identifier)
    s.write_json(directory / "queued.json", {"ids": ids, "builds": builds})


def work(request_path):
    request = json.loads(request_path.read_text())
    directory = request_path.parent
    with s.lock(s.root() / ".registry/draft-reference-advisory.lock"):
        try:
            build_dir = directory / "builds"
            build_dir.mkdir()
            builds = score.build_pair(
                request["before"], request["after"], build_dir, repo=Path(request["after"])
            )
            root = Path(
                os.environ.get(
                    "GMAPS_REQUESTS", str(Path.home() / "turku3d/private/gmaps-data/requests")
                )
            )
            enqueue(request, directory, builds, root)
        except Exception:
            private_notice(
                ["orchestrator"],
                "Private draft advisory build failed",
                f"Batch {request['batch']}; inspect local worker.log in {directory}.",
            )
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    work(args.request)
