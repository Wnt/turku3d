#!/usr/bin/env python3
"""Judge self-test with planted differences (the game scene does not exist yet).

For each office reference view R (qa/views/views.jsonl, tag 'office') three "renders" are judged:
  identical  R itself                               -> expect same_place yes, score ~10, no issues
  degraded   R blurred + facade recoloured + a building masked grey  -> planted issues must be found
  other      a Street View crop of a different street (Eerikinkatu)  -> expect same_place no, score low

  prepare  build the degraded images and run.json (no Codex)
  judge    call Codex for every pair that has no result yet (quota-guarded)
  eval     score planted-issue recall and score separation, write eval.json and the HTML report

  qa/.venv/bin/python qa/selftest/planted.py prepare
  qa/.venv/bin/python qa/selftest/planted.py judge
  qa/.venv/bin/python qa/selftest/planted.py eval
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from qalib import degrade, registry  # noqa: E402
from qalib.paths import data_dir, rel, resolve  # noqa: E402

# Planted regions per view, normalised [x0, y0, x1, y1], chosen by eye on the 1280x960 crops.
PLANS = {
    "office_front": {
        "recolour": {"bbox": [0.0, 0.2, 0.5, 0.75], "label": "left half of the wooden facade"},
        "grey": {"bbox": [0.62, 0.08, 1.0, 0.72], "label": "right part of the facade and dormer"},
        "other": "other_eerikinkatu_y300",
    },
    "office_oblique_sw": {
        "recolour": {"bbox": [0.42, 0.27, 0.74, 0.86], "label": "grey-blue wooden building facade"},
        "grey": {"bbox": [0.0, 0.0, 0.18, 0.86], "label": "stone corner building on the left"},
        "other": "other_eerikinkatu_y60",
    },
    "office_oblique_ne": {
        "recolour": {"bbox": [0.85, 0.15, 1.0, 0.84], "label": "red wooden building on the right"},
        "grey": {"bbox": [0.5, 0.3, 0.84, 0.72], "label": "yellow wooden building row in the middle"},
        "other": "other_eerikinkatu_y150",
    },
}
OTHER_REGISTRY = Path(__file__).resolve().parent / "other_views.jsonl"  # committed; images: sv_views.py refetch


def run_dir(name: str) -> Path:
    return data_dir() / "runs" / name


def cmd_prepare(a):
    rd = run_dir(a.run)
    if (rd / "run.json").exists() and not a.force:
        print(f"{rd / 'run.json'} exists (use --force to rebuild the degraded images)")
        return 0
    (rd / "renders").mkdir(parents=True, exist_ok=True)
    others = {v["id"]: v for v in registry.load(OTHER_REGISTRY)}
    pairs = []
    for vid, plan in PLANS.items():
        v = registry.get(vid)
        ref = resolve(v["image"])
        img = Image.open(ref).convert("RGB")
        deg, planted = degrade.chain(img, [
            (degrade.recolour, {"bbox": plan["recolour"]["bbox"], "label": plan["recolour"]["label"]}),
            (degrade.grey_mask, {"bbox": plan["grey"]["bbox"], "label": plan["grey"]["label"]}),
            (degrade.blur, {"radius": 4.0}),
        ])
        dpath = rd / "renders" / f"{vid}__degraded.png"
        deg.save(dpath)
        o = others[plan["other"]]
        pairs += [
            {"pair_id": f"{vid}__identical", "view_id": vid, "label": "identical", "ref": v["image"], "render": v["image"], "planted": []},
            {"pair_id": f"{vid}__degraded", "view_id": vid, "label": "degraded", "ref": v["image"], "render": rel(dpath), "planted": planted},
            {"pair_id": f"{vid}__other", "view_id": vid, "label": "other_street", "ref": v["image"], "render": o["image"],
             "planted": [{"what": f"different place: {o['id']}", "bbox": [0, 0, 1, 1], "category": "geometry"}]},
        ]
    run = {"run_id": a.run, "kind": "selftest-planted", "pairs": pairs}
    (rd / "run.json").write_text(json.dumps(run, indent=1))
    print(f"prepared {len(pairs)} pairs in {rd}")


def cmd_judge(a):
    from judge.judge import QuotaError, judge
    rd = run_dir(a.run)
    run = json.loads((rd / "run.json").read_text())
    (rd / "judge").mkdir(exist_ok=True)
    for p in run["pairs"]:
        out = rd / "judge" / f"{p['pair_id']}.json"
        if out.exists() and json.loads(out.read_text()).get("ok") and not a.force:
            continue
        if a.only and a.only not in p["pair_id"]:
            continue
        try:
            r = judge(resolve(p["ref"]), resolve(p["render"]), cap=a.cap)
        except QuotaError as e:
            print(f"STOP: {e}")
            return 3
        out.write_text(json.dumps(r, indent=1, ensure_ascii=False))
        res = r["result"] or {}
        print(f"{p['pair_id']:32s} ok={r['ok']} score={res.get('similarity')} same={res.get('same_place')} "
              f"issues={len(res.get('issues', []))} secs={r['secs']} usage {r['usage_before_pct']}->{r['usage_after_pct']}")
    return 0


def _area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


def match_planted(planted, issues, min_cover=0.3):
    """A local planted difference is caught if some local issue box (< 80 % of the frame) covers
    >= min_cover of it or vice versa (whole-image planted items: caught by any issue mentioning blur/detail/sharp/soft/low-res)."""
    hits = []
    for pl in planted:
        ok = None
        for i, it in enumerate(issues):
            if pl["bbox"] == [0, 0, 1, 1]:
                txt = (it["what"] + " " + it["fix_hint"]).lower()
                if any(k in txt for k in ("blur", "soft", "sharp", "low-res", "low res", "resolution", "detail", "smear")):
                    ok = i
                    break
            elif _area(it["bbox"]) < 0.8 and (degrade.overlap_frac(it["bbox"], pl["bbox"]) >= min_cover or degrade.overlap_frac(pl["bbox"], it["bbox"]) >= min_cover):
                ok = i
                break
        hits.append({"planted": pl, "caught_by": ok})
    return hits


def cmd_eval(a):
    from report.report import build_report
    rd = run_dir(a.run)
    run = json.loads((rd / "run.json").read_text())
    rows, by_label = [], {}
    for p in run["pairs"]:
        f = rd / "judge" / f"{p['pair_id']}.json"
        if not f.exists():
            continue
        r = json.loads(f.read_text())
        res = r.get("result") or {}
        hits = match_planted(p["planted"], res.get("issues", [])) if p["label"] == "degraded" else []
        row = {"pair_id": p["pair_id"], "label": p["label"], "similarity": res.get("similarity"),
               "same_place": res.get("same_place"), "n_issues": len(res.get("issues", [])),
               "planted_caught": sum(h["caught_by"] is not None for h in hits), "planted_total": len(hits),
               "missed": [h["planted"]["what"] for h in hits if h["caught_by"] is None]}
        rows.append(row)
        by_label.setdefault(p["label"], []).append(res.get("similarity"))
    summ = {k: {"scores": v, "min": min(v), "max": max(v)} for k, v in by_label.items() if all(x is not None for x in v)}
    sep = None
    if {"identical", "degraded", "other_street"} <= summ.keys():
        sep = summ["identical"]["min"] > summ["degraded"]["max"] and summ["degraded"]["min"] > summ["other_street"]["max"]
    deg = [r for r in rows if r["label"] == "degraded"]
    ev = {"rows": rows, "scores": summ, "strict_separation": sep,
          "planted_recall": (sum(r["planted_caught"] for r in deg), sum(r["planted_total"] for r in deg))}
    (rd / "eval.json").write_text(json.dumps(ev, indent=1))
    html = build_report(rd)
    print(json.dumps(ev, indent=1))
    print(f"report: {html}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["prepare", "judge", "eval"])
    ap.add_argument("--run", default="selftest-planted")
    ap.add_argument("--cap", type=float, default=None)
    ap.add_argument("--only", help="substring of pair ids to judge")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    return {"prepare": cmd_prepare, "judge": cmd_judge, "eval": cmd_eval}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
