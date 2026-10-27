"""SigLIP zero-shot material and building-grouped ridge probe experiment."""

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
from turkutex.facade_batch import write_json
from turkutex.facade_fast_render import MATERIALS
from turkutex.facade_models import MaterialModel
from turkutex.facade_models_benchmark import read_crop, samples

root = Path.home() / "turku3d/research/facade-cv"
output = Path(os.environ["TURKU3D_WORK"]) / "reports/models"
output.mkdir(parents=True, exist_ok=True)
m = MaterialModel()
rows = []
emb = []
for fid, record, _wall, crop in samples(root):
    if not crop:
        continue
    start = time.monotonic()
    pred, f, s = m.predict(read_crop(crop))
    seconds = time.monotonic() - start
    rows.append(
        dict(
            facade_id=fid,
            astra=record["description"],
            material=pred,
            scores=s.tolist(),
            seconds=seconds,
        )
    )
    emb.append(f)
fixture = Path.home() / "turku3d/wt/textures-fast-facade-pass/work/textures/fast-measure"
reviewed = []
for wall in json.loads((fixture / "plan.json").read_text())["walls"]:
    fid = wall["facade_id"]
    start = time.monotonic()
    p, f, s = m.predict(read_crop(fixture / "inputs" / f"{fid}.jpg"))
    reviewed.append(
        dict(
            facade_id=fid,
            material=p,
            scores=s.tolist(),
            seconds=time.monotonic() - start,
            features=f.tolist(),
        )
    )
x = np.array(emb)
np.savez(output / "siglip-embeddings.npz", features=x, ids=np.array([r["facade_id"] for r in rows]))
excluded = {r["facade_id"].rsplit("_", 1)[0] for r in reviewed}


def split(fid):
    b = fid.rsplit("_", 1)[0]
    if b in excluded:
        return "reviewer"
    bucket = int(hashlib.sha256(b.encode()).hexdigest()[:8], 16) % 10
    return "test" if bucket < 2 else "validation" if bucket < 4 else "train"


for r in rows:
    r["split"] = split(r["facade_id"])
visible = np.array([r["astra"]["wall_visible"] for r in rows])
train = np.array([r["split"] == "train" for r in rows]) & visible
val = np.array([r["split"] == "validation" for r in rows]) & visible
test = np.array([r["split"] == "test" for r in rows]) & visible
y = np.eye(7)[[MATERIALS.index(r["astra"]["material"]) for r in rows]]
# Dual ridge with intercept, no test-dependent hyperparameter selection.
mean = x[train].mean(0)
xx = x - mean
ym = y[train].mean(0)
best = None
for alpha in [0.01, 0.1, 1, 10, 100]:
    w = xx[train].T @ np.linalg.solve(
        xx[train] @ xx[train].T + alpha * np.eye(train.sum()), y[train] - ym
    )
    pred = (xx @ w + ym).argmax(1)
    acc = (pred[val] == y[val].argmax(1)).mean()
    if best is None or acc > best[0]:
        best = (acc, alpha, w, pred)
acc, alpha, w, pred = best
for i, r in enumerate(rows):
    r["probe_material"] = MATERIALS[pred[i]]
for r in reviewed:
    r["probe_material"] = MATERIALS[int(((np.array(r.pop("features")) - mean) @ w + ym).argmax())]
np.savez(output / "siglip-material-probe.npz", mean=mean, weights=w, bias=ym)
summary = dict(
    rows=len(rows),
    visible=int(visible.sum()),
    zero_shot_exact=sum(
        r["material"] == r["astra"]["material"] for r in rows if r["astra"]["wall_visible"]
    ),
    train=int(train.sum()),
    validation=int(val.sum()),
    test=int(test.sum()),
    alpha=alpha,
    validation_accuracy=float(acc),
    test_probe_exact=int((pred[test] == y[test].argmax(1)).sum()),
    test_zero_exact=sum(
        r["material"] == r["astra"]["material"]
        for r in rows
        if r["split"] == "test" and r["astra"]["wall_visible"]
    ),
    test_majority=int((y[test].argmax(1) == y[train].sum(0).argmax()).sum()),
    seconds_per_wall=sum(r["seconds"] for r in rows) / len(rows),
)
write_json(output / "siglip.json", dict(summary=summary, rows=rows, reviewed=reviewed))
print(summary, flush=True)
