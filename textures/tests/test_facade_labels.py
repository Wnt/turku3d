from pathlib import Path

import numpy as np
from turkutex.facade_labels import jobs_for, merge_labels
from turkutex.streetview.facades import Facade


def test_reviewed_alternate_label_survives_resume():
    old = {"facade_id": "wall", "usable": False}
    reviewed = {"facade_id": "wall", "usable": True}
    new = {"facade_id": "new", "usable": True}
    records = [
        {"ok": True, "meta": {"facade_ids": ["wall"]}, "answer": {"facades": [old]}},
        {"ok": True, "meta": {"facade_ids": ["new"]}, "answer": {"facades": [new]}},
    ]
    assert merge_labels({"wall": reviewed}, records) == {"wall": reviewed, "new": new}


def test_eight_facades_share_one_low_cost_label_job(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "turkutex.facade_labels.best_view",
        lambda facade, refs: {"files": {"outline": f"{facade.id}_outline.jpg"}},
    )
    walls = [
        Facade(
            f"wall{i}",
            np.array([i, 0]),
            np.array([i + 1, 0]),
            8,
            props={"building_id": f"building{i}"},
        )
        for i in range(9)
    ]
    batches = list(jobs_for(walls, Path(tmp_path)))
    assert [len(job["meta"]["facade_ids"]) for job in batches] == [8, 1]
    assert all(job["model"] == "gpt-6-luna" and job["effort"] == "low" for job in batches)
    assert all(job["expect_images"] == 0 for job in batches)
