#!/usr/bin/env python3
"""One pass of the D8 visual QA loop: render every registered view, judge it against its
reference photo with Codex GPT-6-Astra, and write issues.jsonl + report.html.

  qa/.venv/bin/python qa/loop.py --url http://localhost:5173/ [--ids a,b] [--backend webgl]
  qa/.venv/bin/python qa/loop.py --serve stub --no-judge          # plumbing check, no Codex
  qa/.venv/bin/python qa/loop.py --renders DIR                     # judge existing renders

Outputs go to data/qa/runs/<run_id>/ (run_id defaults to a UTC timestamp, plus the git short sha
when available). Codex calls stop as soon as weekly usage reaches --cap (default 100 %).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from judge.judge import QuotaError, judge  # noqa: E402
from qalib import registry  # noqa: E402
from qalib.paths import QA_DIR, REPO, data_dir, rel, resolve  # noqa: E402
from report.report import build_report  # noqa: E402


def default_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
    except OSError:
        sha = ""
    return f"{ts}-{sha}" if sha else ts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--views", default=str(registry.default_registry()))
    ap.add_argument("--ids", help="comma-separated view ids (default all)")
    ap.add_argument("--url", help="game URL, e.g. http://localhost:5173/")
    ap.add_argument("--serve", help="'stub' or a directory to serve statically instead of --url")
    ap.add_argument("--renders", help="use existing renders (<id>.png [+ .id.png/.id.json]) instead of rendering")
    ap.add_argument("--backend", choices=["webgpu", "webgl"])
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--no-id-image", action="store_true", help="do not send the ID pass to the judge")
    ap.add_argument("--cap", type=float, default=None)
    a = ap.parse_args(argv)

    run_id = a.run_id or default_run_id()
    rd = data_dir() / "runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    views = registry.load(a.views)
    if a.ids:
        want = a.ids.split(",")
        views = [v for v in views if v["id"] in want]
    if not views:
        print("no views")
        return 2

    rdir = Path(a.renders).resolve() if a.renders else rd / "renders"
    if not a.renders:
        if not (a.url or a.serve):
            ap.error("need --url, --serve or --renders")
        cmd = ["node", str(QA_DIR / "render" / "render.mjs"), "--views", str(Path(a.views).resolve()), "--out", str(rdir)]
        cmd += ["--url", a.url] if a.url else ["--serve", a.serve]
        if a.ids:
            cmd += ["--ids", a.ids]
        if a.backend:
            cmd += ["--backend", a.backend]
        r = subprocess.run(cmd, cwd=QA_DIR / "render")
        if r.returncode not in (0, 1):
            print("render crashed")
            return 2

    pairs = []
    for v in views:
        png = rdir / f"{v['id']}.png"
        if not png.exists():
            print(f"{v['id']}: no render, skipped")
            continue
        p = {"pair_id": v["id"], "view_id": v["id"], "label": "render", "ref": v["image"], "render": rel(png)}
        if (rdir / f"{v['id']}.id.png").exists() and (rdir / f"{v['id']}.id.json").exists():
            p["id_image"] = rel(rdir / f"{v['id']}.id.png")
            p["id_table"] = rel(rdir / f"{v['id']}.id.json")
        pairs.append(p)
    (rd / "run.json").write_text(json.dumps({"run_id": run_id, "kind": "render", "views": a.views, "pairs": pairs}, indent=1))

    if not a.no_judge:
        (rd / "judge").mkdir(exist_ok=True)
        for p in pairs:
            v = next(x for x in views if x["id"] == p["view_id"])
            ctx = f"reference captured {v.get('capture_date')}, source {v['source']['kind']}, pose confidence {v['pose_confidence']['level']}."
            try:
                j = judge(resolve(p["ref"]), resolve(p["render"]),
                          None if a.no_id_image else (resolve(p["id_image"]) if p.get("id_image") else None),
                          context=ctx, cap=a.cap)
            except QuotaError as e:
                print(f"STOP: {e}")
                break
            (rd / "judge" / f"{p['pair_id']}.json").write_text(json.dumps(j, indent=1, ensure_ascii=False))
            res = j["result"] or {}
            print(f"{p['pair_id']}: score={res.get('similarity')} issues={len(res.get('issues', []))} "
                  f"usage {j['usage_before_pct']}->{j['usage_after_pct']}%")
    print(build_report(rd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
