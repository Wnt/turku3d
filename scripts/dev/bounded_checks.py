"""Agent-selected commands with one hard wall deadline and owned process groups."""

import concurrent.futures
import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path


def run(command, *, cwd, env, log, deadline):
    started = time.monotonic()
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    with Path(log).open("w") as output:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            code = 124
        finally:
            # Kill the complete group, including children left by an exited shell.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    return {
        "command": command,
        "exit": code,
        "seconds": round(time.monotonic() - started, 3),
        "log": str(log),
    }


def commands(selected, *, cwd, env, directory, seconds):
    """Parallel selections share a single budget, including process startup."""
    started = time.monotonic()
    deadline = started + seconds
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(selected))) as pool:
        futures = [
            pool.submit(
                run,
                ["/bin/sh", "-c", command],
                cwd=cwd,
                env=env,
                log=Path(directory) / f"{index}.log",
                deadline=deadline,
            )
            for index, command in enumerate(selected)
        ]
        results = [future.result() for future in futures]
    return {
        "ok": all(r["exit"] == 0 for r in results),
        "seconds": round(time.monotonic() - started, 3),
        "checks": results,
    }
