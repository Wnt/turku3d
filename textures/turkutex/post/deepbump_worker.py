"""Run the unmodified DeepBump CLI with bounded ONNX CPU resources.

The session factory is scoped to this dedicated subprocess. DeepBump owns its image
algorithm; this adapter only supplies SessionOptions unavailable in its CLI.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise SystemExit("Usage: deepbump_worker CLI.py INPUT OUTPUT MODULE [options]")
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    import onnxruntime as ort

    constructor = ort.InferenceSession

    def bounded_session(*args, **kwargs):
        options = kwargs.pop("sess_options", None) or ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        return constructor(*args, sess_options=options, **kwargs)

    ort.InferenceSession = bounded_session
    sys.argv = argv
    sys.path.insert(0, str(Path(argv[0]).resolve().parent))
    runpy.run_path(argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
