#!/usr/bin/env python3
"""Per-feature verdict of two world systems judged at the same cameras (w2-compare).

  compare_systems.py --a DIR --b DIR --features features.json [--names proven,W2] [--out DIR] [--close 10]

--a/--b are scene-judge run directories (key.json, scores/judge-*.json) of the same view set, one per system. A view's
overall is the judge's weighted category sum times ten (scene-judge WEIGHTS), averaged over the repeats. Per feature
(the view's `group`): views, both means, margin (b - a), winner, and how far the measures agree:
  views   share of the feature's views whose own winner is the feature's winner;
  repeats share of repeat pairs (a's repeat i against b's repeat i) that pick the same winner for the feature;
  aspects per category, which system has the higher mean (the six letters in identity..defects order).
A feature whose margin is within the judge's spread (the larger of the two repeat ranges) or whose repeats disagree is
a close call. The close-call page (close.html + close.jpg) shows photo | a | b for at most --close views, taken from
the close features first (the view nearest the feature's margin), then the smallest view margins; the photos stay
outside Git. Writes table.json, table.md, close.html and close.jpg into --out.
"""
import argparse
import html
import json
import statistics as st
from pathlib import Path

from PIL import Image, ImageDraw

W = dict(identity=.3, buildings=.2, materials=.15, lighting=.1, street=.15, defects=.1)


def load(run):
    run = Path(run)
    key = {k["id"]: k for k in json.loads((run / "key.json").read_text())["key"]}
    reps = [json.loads(f.read_text()) for f in sorted((run / "scores").glob("judge-*.json"))]
    views = {}
    for r, rep in enumerate(reps):
        for v in rep["views"]:
            k = key[v["id"]]
            e = views.setdefault(k["view"], dict(group=k.get("group"), render=k["render"], photo=(k.get("photo") or {}).get("image"), overall=[], scores=[], issues=[]))
            e["overall"].append(10 * sum(W[c] * v["scores"][c] for c in W))
            e["scores"].append(v["scores"])
            e["issues"] += [dict(i, repeat=r + 1) for i in v.get("issues", [])]
    return views, len(reps), [rep.get("meta", {}) for rep in reps]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--features", required=True)
    ap.add_argument("--names", default="proven,W2")
    ap.add_argument("--out", required=True)
    ap.add_argument("--close", type=int, default=10)
    ap.add_argument("--photos", help="views.jsonl registry for the photo paths (default: the run's key)")
    args = ap.parse_args()
    na, nb = args.names.split(",")
    A, ra, ma = load(args.a)
    B, rb, mb = load(args.b)
    feats = {f["id"].replace("_", "-"): f for f in json.loads(Path(args.features).read_text())["features"]}
    common = [v for v in A if v in B]
    reps = min(ra, rb)
    rows, per_view = {}, []
    for v in common:
        g = A[v]["group"] or feats.get(v, {}).get("feature", "other")
        a, b = st.mean(A[v]["overall"]), st.mean(B[v]["overall"])
        per_view.append(dict(view=v, feature=g, a=round(a, 1), b=round(b, 1), margin=round(b - a, 1),
                             what=feats.get(v, {}).get("what", ""), a_reps=[round(x, 1) for x in A[v]["overall"]], b_reps=[round(x, 1) for x in B[v]["overall"]]))
        rows.setdefault(g, []).append(v)
    table = []
    for g, vs in sorted(rows.items()):
        a = st.mean(st.mean(A[v]["overall"]) for v in vs)
        b = st.mean(st.mean(B[v]["overall"]) for v in vs)
        winner = nb if b > a else na if a > b else "tie"
        own = [nb if st.mean(B[v]["overall"]) > st.mean(A[v]["overall"]) else na if st.mean(A[v]["overall"]) > st.mean(B[v]["overall"]) else "tie" for v in vs]
        per_rep = []
        for r in range(reps):
            ar = st.mean(A[v]["overall"][r] for v in vs)
            br = st.mean(B[v]["overall"][r] for v in vs)
            per_rep.append(dict(a=round(ar, 1), b=round(br, 1)))
        rep_agree = sum((p["b"] > p["a"]) == (b > a) for p in per_rep) / max(1, len(per_rep))
        spread = max([max(p["a"] for p in per_rep) - min(p["a"] for p in per_rep), max(p["b"] for p in per_rep) - min(p["b"] for p in per_rep)]) if reps > 1 else None
        aspects = {}
        for c in W:
            ac = st.mean(st.mean(s[c] for s in A[v]["scores"]) for v in vs)
            bc = st.mean(st.mean(s[c] for s in B[v]["scores"]) for v in vs)
            aspects[c] = dict(a=round(ac, 2), b=round(bc, 2), winner=nb if bc > ac else na if ac > bc else "tie")
        close = abs(b - a) <= (spread or 0) or rep_agree < 1
        table.append(dict(feature=g, views=len(vs), a=round(a, 1), b=round(b, 1), margin=round(b - a, 1), winner=winner,
                          view_agreement=round(own.count(winner) / len(vs), 2), repeat_agreement=round(rep_agree, 2), spread=None if spread is None else round(spread, 1),
                          close=close, aspects=aspects, per_repeat=per_rep, ids=vs))
    alla = st.mean(st.mean(A[v]["overall"]) for v in common)
    allb = st.mean(st.mean(B[v]["overall"]) for v in common)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cost = sum((m.get("costUsd") or 0) for m in ma + mb)
    (out / "table.json").write_text(json.dumps(dict(a=dict(name=na, run=str(Path(args.a).resolve()), repeats=ra), b=dict(name=nb, run=str(Path(args.b).resolve()), repeats=rb),
                                                    all=dict(views=len(common), a=round(alla, 1), b=round(allb, 1), margin=round(allb - alla, 1)),
                                                    features=table, views=per_view, costUsd=round(cost, 2)), indent=1) + "\n")
    letters = {na: na[0].upper(), nb: nb[0].upper(), "tie": "="}
    md = [f"| Feature | Views | {na} | {nb} | Margin ({nb} − {na}) | Winner | Views agree | Repeats agree | Aspects (I B M L S D) |", "|---|---|---|---|---|---|---|---|---|"]
    for t in sorted(table, key=lambda t: t["margin"]):
        md.append(f"| {t['feature']}{' (close)' if t['close'] else ''} | {t['views']} | {t['a']} | {t['b']} | {t['margin']:+} | {t['winner']} | {t['view_agreement']:.0%} | {t['repeat_agreement']:.0%} | "
                  + " ".join(letters[t['aspects'][c]['winner']] for c in W) + " |")
    md.append(f"| **all** | {len(common)} | {alla:.1f} | {allb:.1f} | {allb - alla:+.1f} | {nb if allb > alla else na} | | | |")
    (out / "table.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    # Close calls: the close features' views nearest their feature margin first, then the smallest view margins.
    pv = {p["view"]: p for p in per_view}
    pick = []
    for t in sorted([t for t in table if t["close"]], key=lambda t: abs(t["margin"])):
        best = min(t["ids"], key=lambda v: abs(pv[v]["margin"]))
        pick.append(best)
    for p in sorted(per_view, key=lambda p: abs(p["margin"])):
        if p["view"] not in pick:
            pick.append(p["view"])
    pick = pick[:args.close]
    reg = {}
    if args.photos:
        reg = {json.loads(line)["id"].replace("_", "-"): json.loads(line)["image"] for line in Path(args.photos).read_text().splitlines() if line.strip()}
    w, h = 480, 360
    sheet = Image.new("RGB", (3 * w + 8, len(pick) * (h + 22)), "white")
    d = ImageDraw.Draw(sheet)
    cells = []
    import random
    rng, order = random.Random(20261113), {}
    for i, v in enumerate(pick):
        photo = reg.get(v) or A[v]["photo"]
        flip = rng.random() < 0.5  # X and Y are the two systems in a random order per row; close-key.json maps them
        order[v] = dict(X=nb if flip else na, Y=na if flip else nb)
        imgs = [photo, B[v]["render"], A[v]["render"]] if flip else [photo, A[v]["render"], B[v]["render"]]
        y = i * (h + 22)
        d.text((4, y + 4), f"{i + 1}. {v} ({pv[v]['feature']}): left photo, middle X, right Y", fill="black")
        for c, p in enumerate(imgs):
            if p and Path(p).exists():
                sheet.paste(Image.open(p).convert("RGB").resize((w, h)), (c * (w + 4), y + 20))
        cells.append((i + 1, v, imgs))
    sheet.save(out / "close.jpg", quality=82)
    page = ["<!doctype html><meta charset=utf-8><title>W2 compare close calls</title><style>body{font:14px system-ui;margin:16px}"
            "img{width:32%;margin-right:.5%}h3{margin:18px 0 4px}</style><h1>Close calls: which render is closer to the photo?</h1>"
            "<p>Left: the photo. Middle: X. Right: Y. For each, answer X, Y or same. (Which system is X differs per row; close-key.json holds it.)</p>"]
    for n, v, imgs in cells:
        page.append(f"<h3>{n}. {html.escape(pv[v]['feature'])}: {html.escape(pv[v]['what'])}</h3>" + "".join(f"<img src='file://{html.escape(str(p))}'>" for p in imgs if p))
    (out / "close.html").write_text("\n".join(page) + "\n")
    (out / "close-key.json").write_text(json.dumps([dict(n=n, view=v, **order[v], judge_margin=pv[v]["margin"]) for n, v, _ in cells], indent=1) + "\n")
    print(f"close calls: {pick}\ncost of the judge runs: {cost:.2f} USD")


if __name__ == "__main__":
    main()
