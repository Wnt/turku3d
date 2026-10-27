"""Pipeline tests never write into the shared data root.

The test process starts with ``TURKU3D_DATA`` pointing at a directory that does not exist, so a
test that does not set up its own temporary root (``monkeypatch.setenv("TURKU3D_DATA", ...)``)
reads nothing real and children it starts inherit the same absent root. An audit hook refuses any
write, rename, delete or directory change under the real data root (``~/turku3d/data`` and the
``TURKU3D_DATA`` the run was started with) with PermissionError, and the test that tried fails.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

_REAL = {Path.home() / "turku3d" / "data"}
if os.environ.get("TURKU3D_DATA"):
    _REAL.add(Path(os.environ["TURKU3D_DATA"]).expanduser())
REAL_ROOTS = tuple(str(p.resolve()) for p in _REAL)
ABSENT_ROOT = Path(tempfile.mkdtemp(prefix="turku3d-tests-")) / "no-data-root"
os.environ["TURKU3D_DATA"] = str(ABSENT_ROOT)

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_PATH_EVENTS = {
    "os.remove": 1,
    "os.rename": 2,
    "os.replace": 2,
    "os.rmdir": 1,
    "os.mkdir": 1,
    "os.link": 2,
    "os.symlink": 2,
    "os.truncate": 1,
    "os.chmod": 1,
    "os.utime": 1,
    "shutil.rmtree": 1,
    "shutil.copyfile": 2,
    "shutil.move": 2,
}
violations: list[str] = []


def _real(path) -> bool:
    if isinstance(path, int) or path is None:
        return False
    try:
        p = os.path.realpath(os.fsdecode(path))
    except (TypeError, ValueError):
        return False
    return any(p == r or p.startswith(r + os.sep) for r in REAL_ROOTS)


def _audit(event: str, args) -> None:
    if event == "open":
        path, mode, flags = args
        writing = (mode and any(c in mode for c in "wax+")) or (
            mode is None and isinstance(flags, int) and flags & _WRITE_FLAGS
        )
        paths = [path] if writing else []
    elif event in _PATH_EVENTS:
        paths = args[: _PATH_EVENTS[event]]
    else:
        return
    for path in paths:
        if _real(path):
            violations.append(f"{event} {os.fsdecode(path)}")
            raise PermissionError(f"tests must not write into the shared data root: {path}")


sys.addaudithook(_audit)


@pytest.fixture(autouse=True)
def _no_shared_data_writes():
    start = len(violations)
    yield
    if len(violations) > start:
        pytest.fail("wrote into the shared data root: " + "; ".join(violations[start:]))
