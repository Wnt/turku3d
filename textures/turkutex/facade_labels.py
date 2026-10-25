"""Label several photo-referenced walls in each quota-guarded Codex call."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .building_jobs import latest
from .codex.runner import Runner, load_jobs
from .codex.usage import used_percent
from .facade_batch import write_json
from .facade_direct import best_view
from .jobs import _write
from .paths import SCHEMAS_DIR, textures_data_dir
from .streetview.facades import load_facades


def jobs_for(facades, refs: Path, batch_size: int = 8):
    available = []
    for f in facades:
        try:
            view = best_view(f, refs)
        except ValueError:
            continue
        image = refs / f.id / view["files"]["outline"]
        available.append((f, image))
    for offset in range(0, len(available), batch_size):
        batch = available[offset : offset + batch_size]
        details = [
            {
                "facade_id": f.id,
                "image": image.name,
                "width_m": round(f.length, 2),
                "height_m": round(f.height_m, 2),
                "geometry_bays": f.props.get("bay_count"),
            }
            for f, image in batch
        ]
        yield {
            "id": f"office_facades_{batch[0][0].id}_{batch[-1][0].id}",
            "prompt": "Label each outlined Turku street facade from its own attached photo. "
            "Return one entry for each facade_id exactly, in English. Describe visible wall "
            "material, unshadowed main color, floor count and repeated window bays. Use null "
            "when floors or bays cannot be read; do not infer from dimensions. Set usable=false "
            "and describe the problem when scaffolding, trees, vehicles, a wrong building or "
            "another occluder hides most of the architecture. Do not generate "
            "images or run commands.\n" + json.dumps(details),
            "images": [str(image) for _, image in batch],
            "schema": str(SCHEMAS_DIR / "facade_label_batch.schema.json"),
            "model": "gpt-6-luna",
            "effort": "low",
            "expect_images": 0,
            "prompt_version": "facade-label-batch-v1",
            "meta": {"kind": "building_label", "facade_ids": [f.id for f, _ in batch]},
        }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("facades", type=Path)
    ap.add_argument("--refs", type=Path, required=True)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--batch-size", type=int, default=8)
    a = ap.parse_args(argv)
    quota = used_percent()
    if quota is None or quota >= 70:
        raise RuntimeError(f"Codex quota guard: weekly used percent is {quota}")
    a.run.mkdir(parents=True, exist_ok=True)
    labels_path = a.run / "labels.json"
    previous = json.loads(labels_path.read_text()) if labels_path.exists() else {}
    job_path = a.run / "label_jobs.jsonl"
    _write(
        list(
            jobs_for(
                (f for f in load_facades(a.facades) if f.id not in previous), a.refs, a.batch_size
            )
        ),
        job_path,
    )
    runner = Runner(
        a.run / "run_label", parallel=2, max_pct=70, cache_dir=textures_data_dir("codex-cache")
    )
    result = runner.run(load_jobs(job_path))
    labels = dict(previous)
    for record in latest(a.run / "run_label/out.jsonl").values():
        if not record.get("ok"):
            continue
        expected = set(record["meta"]["facade_ids"])
        received = {v["facade_id"] for v in record["answer"]["facades"]}
        if received != expected or len(record["answer"]["facades"]) != len(expected):
            continue
        labels.update({v["facade_id"]: v for v in record["answer"]["facades"]})
    write_json(labels_path, labels)
    print(json.dumps({"quota_before": quota, "labels": len(labels), "runner_exit": result}))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
