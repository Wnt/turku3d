#!/usr/bin/env python3
"""Build issues.jsonl and report.html for one QA run directory, and update the cross-run history.

Run directory layout (data/qa/runs/<run_id>/):
  run.json        {"run_id", "kind", "pairs": [{"pair_id", "view_id", "label", "ref", "render",
                    "id_image"?, "id_table"?, "planted"?}]}
  judge/<pair_id>.json   judge.py output
  eval.json       optional (self-test): planted recall
Outputs:
  issues.jsonl    one ticket per issue (with back-mapped game objects when an ID pass exists)
  report.html     side-by-side images with issue boxes, scores, trend across runs
  ../../issues.jsonl, ../../history.jsonl  (data/qa/) cumulative, de-duplicated by run id

  qa/.venv/bin/python qa/report/report.py data/qa/runs/<run_id>
"""

from __future__ import annotations

import fcntl
import html
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qalib.paths import data_dir, resolve  # noqa: E402

from report.backmap import annotate  # noqa: E402

CAT_COLOURS = {
    "geometry": "#e5484d",
    "texture": "#f5a524",
    "material": "#a855f7",
    "lighting": "#eab308",
    "missing_object": "#06b6d4",
    "scale": "#3b82f6",
    "vegetation": "#22c55e",
    "other": "#94a3b8",
}


def _rewrite_jsonl(path: Path, rows: list[dict], drop_run: str):
    # Separate runs/calibration may finish together; keep the read/merge/replace atomic.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        keep = (
            [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            if path.exists()
            else []
        )
        merged = [row for row in keep if row.get("run_id") != drop_run] + rows
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
            tmp.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged))
        os.replace(tmp.name, path)
    return merged


def collect(rd: Path):
    run = json.loads((rd / "run.json").read_text())
    pairs, tickets = [], []
    for p in run["pairs"]:
        f = rd / "judge" / f"{p['pair_id']}.json"
        j = json.loads(f.read_text()) if f.exists() else None
        res = (j or {}).get("result") or None
        if res:
            idimg = resolve(p["id_image"]) if p.get("id_image") else None
            idtab = resolve(p["id_table"]) if p.get("id_table") else None
            annotate(res["issues"], idimg, idtab)
            for k, it in enumerate(res["issues"]):
                tickets.append(
                    {
                        "run_id": run["run_id"],
                        "pair_id": p["pair_id"],
                        "view_id": p.get("view_id"),
                        "n": k,
                        **it,
                        "status": "open",
                        "ref": p["ref"],
                        "render": p["render"],
                        "thread_id": ((j.get("attempts") or [{}])[-1]).get("thread_id"),
                    }
                )
        pairs.append({**p, "judge": j, "result": res})
    return run, pairs, tickets


def history_row(run, pairs):
    scores = {p["pair_id"]: p["result"]["similarity"] for p in pairs if p["result"]}
    by_label = {}
    for p in pairs:
        if p["result"]:
            by_label.setdefault(p.get("label") or "render", []).append(p["result"]["similarity"])
    return {
        "run_id": run["run_id"],
        "kind": run.get("kind", "render"),
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": run.get("commit"),
        "dirty": run.get("dirty"),
        "complete": run.get("complete", False),
        "comparison_key": run.get("comparison_key"),
        "manifest_sha256": run.get("manifest_sha256"),
        "scores": scores,
        "mean_by_label": {k: round(sum(v) / len(v), 2) for k, v in by_label.items()},
        "n_issues": sum(len(p["result"]["issues"]) for p in pairs if p["result"]),
    }


def trend_svg(history: list[dict], kind: str, comparison_key=None) -> str:
    rows = [
        h
        for h in history
        if h.get("kind") == kind
        and (comparison_key is None or h.get("comparison_key") == comparison_key)
    ]
    if not rows:
        return ""
    labels = sorted({k for h in rows for k in h["mean_by_label"]})
    W, H, L, B = 640, 200, 36, 28
    n = len(rows)
    xs = [L + (W - L - 12) * (i / max(1, n - 1)) for i in range(n)] if n > 1 else [L + (W - L) / 2]
    y = lambda s: 8 + (H - B - 8) * (1 - s / 10)  # noqa: E731
    palette = ["#3b82f6", "#f5a524", "#e5484d", "#22c55e", "#a855f7"]
    parts = [
        f'<svg viewBox="0 0 {W} {H}" class="trend" role="img" aria-label="mean similarity per run">'
    ]
    for s in (0, 5, 10):
        parts.append(
            f'<line x1="{L}" x2="{W - 8}" y1="{y(s):.1f}" y2="{y(s):.1f}" class="grid"/>'
            f'<text x="{L - 6}" y="{y(s) + 4:.1f}" text-anchor="end">{s}</text>'
        )
    for li, lab in enumerate(labels):
        pts = [
            (xs[i], y(h["mean_by_label"][lab]))
            for i, h in enumerate(rows)
            if lab in h["mean_by_label"]
        ]
        c = palette[li % len(palette)]
        if len(pts) > 1:
            parts.append(
                f'<polyline fill="none" stroke="{c}" stroke-width="2" points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}"/>'
            )
        for (a, b), row in zip(pts, [h for h in rows if lab in h["mean_by_label"]], strict=True):
            title = html.escape(
                f"{row['run_id']} ({str(row.get('commit', ''))[:8]}): {row['mean_by_label'][lab]}"
            )
            parts.append(
                f'<circle cx="{a:.1f}" cy="{b:.1f}" r="4" fill="{c}"><title>{title}</title></circle>'
            )
        parts.append(
            f'<text x="{L + 8 + li * 120}" y="{H - 6}" style="fill:{c}">&#9679; {html.escape(lab)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _img_rel(p: str, rd: Path) -> str:
    return html.escape(os.path.relpath(resolve(p), rd))


def render_html(rd: Path, run, pairs, history, ev=None) -> str:
    evrows = {r["pair_id"]: r for r in (ev or {}).get("rows", [])}
    cards = []
    for p in pairs:
        res, j = p["result"], p["judge"] or {}
        boxes = []
        if res:
            for k, it in enumerate(res["issues"], 1):
                x0, y0, x1, y1 = it["bbox"]
                c = CAT_COLOURS.get(it["category"], "#94a3b8")
                boxes.append(
                    f'<div class="box" style="left:{x0 * 100:.2f}%;top:{y0 * 100:.2f}%;width:{(x1 - x0) * 100:.2f}%;'
                    f'height:{(y1 - y0) * 100:.2f}%;border-color:{c}"><span style="background:{c}">{k}</span></div>'
                )
        for pl in p.get("planted") or []:
            if pl["bbox"] != [0, 0, 1, 1]:
                x0, y0, x1, y1 = pl["bbox"]
                boxes.append(
                    f'<div class="box planted" style="left:{x0 * 100:.2f}%;top:{y0 * 100:.2f}%;'
                    f'width:{(x1 - x0) * 100:.2f}%;height:{(y1 - y0) * 100:.2f}%"></div>'
                )
        score = f"{res['similarity']:g}" if res else "n/a"
        same = res["same_place"] if res else ("judge failed" if j else "not judged")
        rows = ""
        if res:
            for k, it in enumerate(res["issues"], 1):
                objs = ", ".join(
                    f"{o.get('kind')}:{o.get('id', o['oid'])} "
                    + " ".join(
                        f"{key}={o[key]}"
                        for key in ("building", "tile", "facade", "material")
                        if o.get(key) is not None
                    )
                    + f" ({o['frac']:.0%})"
                    for o in it.get("objects", [])
                )
                if not objs:
                    objs = it.get("mapping_status", "no visible asset")
                rows += (
                    f'<tr><td>{k}</td><td><span class="sev s{it["severity"]}">{it["severity"]}</span></td>'
                    f'<td><span class="cat" style="--c:{CAT_COLOURS.get(it["category"], "#94a3b8")}">{it["category"]}</span></td>'
                    f"<td>{html.escape(it['what'])}</td><td>{html.escape(it['fix_hint'])}</td><td>{html.escape(objs)}</td></tr>"
                )
        evr = evrows.get(p["pair_id"])
        evtxt = ""
        if evr and evr["planted_total"]:
            evtxt = f'<p class="eval">planted differences caught: <b>{evr["planted_caught"]}/{evr["planted_total"]}</b>'
            if evr["missed"]:
                evtxt += " &middot; missed: " + html.escape("; ".join(evr["missed"]))
            evtxt += "</p>"
        ign = ", ".join(html.escape(x) for x in (res or {}).get("ignored_differences", []))
        att = (j.get("attempts") or [{}])[-1]
        cards.append(f'''
<section class="card" id="{html.escape(p["pair_id"])}">
  <header><h2>{html.escape(p["pair_id"])}</h2>
    <div class="score"><span class="big">{score}</span><span class="muted">/10 &middot; same place: {html.escape(str(same))}</span></div></header>
  <div class="pair">
    <figure><img loading="lazy" src="{_img_rel(p["ref"], rd)}" alt="reference"><figcaption>reference</figcaption></figure>
    <figure><div class="wrap"><img loading="lazy" src="{_img_rel(p["render"], rd)}" alt="render">{"".join(boxes)}</div>
      <figcaption>render{" (dashed = planted)" if p.get("planted") else ""}</figcaption></figure>
  </div>
  <p>{html.escape((res or {}).get("summary", ""))}</p>{evtxt}
  {"<table><thead><tr><th>#</th><th>sev</th><th>category</th><th>what</th><th>fix hint</th><th>objects</th></tr></thead><tbody>" + rows + "</tbody></table>" if rows else ""}
  <p class="muted small">ignored: {ign or "-"} &middot; thread {html.escape(str(att.get("thread_id")))} &middot; {j.get("secs", "?")} s &middot; usage {j.get("usage_before_pct")}% &rarr; {j.get("usage_after_pct")}%</p>
</section>''')
    summary_rows = "".join(
        f'<tr><td><a href="#{html.escape(p["pair_id"])}">{html.escape(p["pair_id"])}</a></td><td>{html.escape(str(p.get("label") or ""))}</td>'
        f"<td>{p['result']['similarity'] if p['result'] else 'n/a'}</td><td>{p['result']['same_place'] if p['result'] else ''}</td>"
        f"<td>{len(p['result']['issues']) if p['result'] else ''}</td></tr>"
        for p in pairs
    )
    evblock = ""
    if ev:
        rc = ev.get("planted_recall")
        evblock = (
            f"<p>Self-test: planted recall <b>{rc[0]}/{rc[1]}</b>, strict score separation "
            f"identical &gt; degraded &gt; other: <b>{ev.get('strict_separation')}</b></p>"
        )
    calibration_path = rd / "calibration.json"
    if calibration_path.exists():
        calibration = json.loads(calibration_path.read_text())
        evblock += (
            f"<p>Judge consistency (n=3): scores {html.escape(str(calibration['scores']))}; "
            f"sample variance {calibration['sample_variance']:.4f}; "
            f"standard deviation {calibration['sample_stddev']:.4f}. "
            "Three repeats on one pair do not establish whole-range reliability.</p>"
        )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>QA Run Report</title>
<style>
:root{{--bg:#f7f7f8;--fg:#18181b;--muted:#6b7280;--card:#fff;--line:#e4e4e7}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#111113;--fg:#ececef;--muted:#9ca3af;--card:#1b1b1f;--line:#2e2e33}}}}
:root[data-theme="dark"]{{--bg:#111113;--fg:#ececef;--muted:#9ca3af;--card:#1b1b1f;--line:#2e2e33}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px}}
main{{max-width:1400px;margin:0 auto}} h1{{font-size:22px}} h2{{font-size:16px;margin:0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:16px 0}}
.card header{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}}
.big{{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}} .muted{{color:var(--muted)}} .small{{font-size:12px}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:10px}} @media (max-width:700px){{.pair{{grid-template-columns:1fr}}}}
figure{{margin:0}} img{{width:100%;display:block;border-radius:6px}} figcaption{{color:var(--muted);font-size:12px}}
.wrap{{position:relative}} .box{{position:absolute;border:3px solid;border-radius:3px;box-sizing:border-box}}
.box span{{position:absolute;left:-3px;top:-3px;color:#fff;font-weight:700;font-size:12px;padding:0 5px;border-radius:3px}}
.box.planted{{border:2px dashed #fff;outline:1px dashed #000}}
table{{border-collapse:collapse;width:100%;margin-top:8px}} td,th{{border-top:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}}
.sev{{display:inline-block;min-width:18px;text-align:center;border-radius:4px;color:#fff;background:#6b7280}}
.s4,.s5{{background:#e5484d}} .s3{{background:#f5a524}} .cat{{border-left:4px solid var(--c);padding-left:6px}}
.trend{{width:100%;max-width:640px;height:auto}} .trend text{{fill:var(--muted);font-size:11px}} .trend .grid{{stroke:var(--line)}}
.eval{{background:color-mix(in srgb,var(--fg) 6%,transparent);padding:6px 8px;border-radius:6px}}
</style></head><body><main>
<h1>QA run {html.escape(run["run_id"])}</h1>
<p class="muted">{html.escape(run.get("kind", "render"))} &middot; {len(pairs)} pairs &middot; generated {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
<p>Commit: {html.escape(str(run.get("commit", "unknown")))}; dirty: {run.get("dirty", "unknown")}; complete: {run.get("complete", False)}; package: {html.escape(str(run.get("manifest_sha256", "unknown")))}</p>
<pre>{html.escape(chr(10).join(run.get("errors", [])))}</pre>
{evblock}
<h2>Mean similarity per run ({html.escape(run.get("kind", "render"))})</h2>{trend_svg(history, run.get("kind", "render"), run.get("comparison_key"))}
<table><thead><tr><th>pair</th><th>label</th><th>score</th><th>same place</th><th>issues</th></tr></thead><tbody>{summary_rows}</tbody></table>
{"".join(cards)}
</main></body></html>"""


def build_report(rd: str | Path) -> Path:
    rd = Path(rd).resolve()
    run, pairs, tickets = collect(rd)
    (rd / "issues.jsonl").write_text(
        "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tickets)
    )
    _rewrite_jsonl(data_dir() / "issues.jsonl", tickets, run["run_id"])
    history = _rewrite_jsonl(data_dir() / "history.jsonl", [history_row(run, pairs)], run["run_id"])
    ev = json.loads((rd / "eval.json").read_text()) if (rd / "eval.json").exists() else None
    out = rd / "report.html"
    out.write_text(render_html(rd, run, pairs, history, ev))
    return out


if __name__ == "__main__":
    print(build_report(sys.argv[1]))
