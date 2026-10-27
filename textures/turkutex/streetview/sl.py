"""Import streetlevel 0.12.13 without its unusable optional native deps.

streetlevel imports pyexiv2 (needs Homebrew inih) and pyfrpc (Mapy.cz; fails to build on macOS 26)
at module import time, but we never write EXIF or use Mapy.cz. We install streetlevel with --no-deps
(see textures/README.md) and stub pyexiv2 here before importing.
"""

from __future__ import annotations

import sys
import types


def _stub_pyexiv2():
    try:
        import pyexiv2  # noqa: F401

        return
    except Exception:  # noqa: BLE001 - ImportError or OSError from a broken dylib
        pass
    m = types.ModuleType("pyexiv2")
    m.ImageData = object
    m.Image = object
    sys.modules["pyexiv2"] = m


_stub_pyexiv2()

from streetlevel import lookaround, streetview  # noqa: E402

__all__ = ["streetview", "lookaround"]
