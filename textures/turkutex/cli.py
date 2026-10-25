"""turkutex command line: `turkutex <command> ...` (or `python -m turkutex ...`).

  codex run JOBS --out DIR [-j N] [--max-pct 100]   batch runner (see turkutex/codex/runner.py)
  codex usage                                       weekly quota snapshot (free, no model call)
  codex collect THREAD_ID [--copy DIR]              images + prompt provenance of one thread
  facades --near E N --out F.geojson [...]          street facades from kantakartta footprints
  refs FACADES.geojson [--out DIR]                  Street View reference crops + pose JSON
  jobs label|improve FACADES --refs DIR --out J.jsonl  build Codex jobs from reference crops
  post IN.png --out DIR [--tile x|xy] [...]         tileable + flatten + normals/height + ORM + KTX2 + QA
  qa IN.png                                         seam-ratio / low-frequency metrics as JSON
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "codex":
        sub = rest[0] if rest else ""
        if sub == "run":
            from .codex.runner import main as m
            return m(rest[1:])
        if sub == "usage":
            from .codex import usage
            print(json.dumps(usage.snapshot()))
            return 0
        if sub == "collect":
            from .codex import rollout as ro
            tid = rest[1]
            rp = ro.find_rollout(tid)
            r = ro.parse_rollout(rp) if rp else None
            imgs = ro.collect_images(tid, rollout=r)
            if "--copy" in rest:
                d = Path(rest[rest.index("--copy") + 1])
                d.mkdir(parents=True, exist_ok=True)
                for n, im in enumerate(imgs):
                    shutil.copy2(im["path"], d / f"{n:02d}.png")
            print(json.dumps({"thread_id": tid, "rollout": str(rp) if rp else None,
                              "model": r.model if r else None, "tokens": r.tokens if r else None,
                              "used_percent": r.used_percent if r else None,
                              "calls": [c.__dict__ for c in r.calls] if r else [],
                              "images": imgs, "primary": ro.pick_primary(imgs)}, indent=1))
            return 0
        print("usage: turkutex codex run|usage|collect", file=sys.stderr)
        return 2
    if cmd == "facades":
        from .streetview.facades import main as m
        return m(rest)
    if cmd == "refs":
        from .streetview.refs import main as m
        return m(rest)
    if cmd == "jobs":
        from .jobs import main as m
        return m(rest)
    if cmd == "post":
        from .post.pipeline import main as m
        return m(rest)
    if cmd == "qa":
        from .post.qa import main as m
        return m(rest)
    print(f"unknown command {cmd!r}\n{__doc__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
