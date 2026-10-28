"""Recover missing quarter inventories from already observed native responses.

No network requests or browser launches. Completeness is independently re-audited;
missing payloads remain missing. Only the invoking sandbox is writable.
"""

import json
import os
import shutil
import sys
from pathlib import Path

import gmaps_round3 as base

sys.path.insert(0, str(base.TOOLS))
from detail import metadata  # noqa: E402


def main():
    root = Path(os.environ["TURKU3D_WORK"]) / "gmaps-round3/synthetic"
    observations = {}
    for catalogue in (root / "sessions").glob("*/catalogue.json"):
        for row in json.loads(catalogue.read_text()):
            observations[row["url"], row["sha256"]] = row, catalogue.parent / row["file"]
    summary = []
    for quarter in base.PLAN["quarters"]:
        key = quarter["coverage_id"]
        out = root / "captures" / key
        path = out / "capture.json"
        if path.exists() and json.loads(path.read_text()).get("inventory_state") == "eligible":
            continue
        record = base.config(quarter, "offline-reuse")
        record.update(
            responses=[],
            errors=[],
            finished=True,
            inventory_state="incomplete",
            capture_mode="Previously observed native responses, offline reuse",
            missing_node_policy=dict(
                max_missing_fraction=0.05,
                mask_missing_nodes=True,
                authorization="Round3 masked policy",
            ),
        )
        out.mkdir(parents=True, exist_ok=True)
        (out / "responses").mkdir(exist_ok=True)
        rows = dict(observations)
        prior = Path.home() / "turku3d/private/gmaps-data/round2/captures" / key / "capture.json"
        if prior.exists():
            for row in json.loads(prior.read_text()).get("responses", []):
                source = prior.parent / row["file"]
                if source.exists():
                    rows.setdefault((row["url"], row.get("sha256")), (row, source))
        for row, source in rows.values():
            destination = out / row["file"]
            if not destination.resolve().is_relative_to(out.resolve()):
                raise ValueError("Response path escapes capture")
            if not destination.exists():
                if source.is_relative_to(root):
                    os.link(source, destination)
                else:
                    shutil.copy2(source, destination)
            record["responses"].append(row)
        base.atomic_json(path, record)
        proof = metadata(out)
        record.update(
            inventory_state="eligible" if proof["complete"] else "incomplete",
            detail_progress=proof,
            stop_reason="offline observed-response completeness audit",
        )
        base.atomic_json(path, record)
        result = dict(
            coverage_id=key,
            complete=proof["complete"],
            available=proof["terminal_data_nodes"],
            loaded=proof["terminal_loaded"],
        )
        summary.append(result)
        print(json.dumps(result), flush=True)
    base.atomic_json(root / "offline-reuse.json", summary)


if __name__ == "__main__":
    main()
