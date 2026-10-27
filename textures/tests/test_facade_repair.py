import numpy as np
from turku3d import config
from turkutex.facade_repair import repair_jobs
from turkutex.streetview.facades import Facade


def test_repair_only_failed_walls_near_spawn_with_25_cap(monkeypatch, tmp_path):
    monkeypatch.setattr("turkutex.building_jobs.reference_matches", lambda f, refs: True)
    monkeypatch.setattr(
        "turkutex.facade_repair._views",
        lambda path: [
            {
                "part": "full",
                "score": 1,
                "view_id": "v0",
                "files": {"rectified": "rectified.jpg", "outline": "outline.jpg"},
            }
        ],
    )
    walls = [
        Facade(
            f"wall{i}",
            np.array([config.OFFICE_E + i, config.OFFICE_N]),
            np.array([config.OFFICE_E + i + 6, config.OFFICE_N]),
            8,
            props={"building_id": f"building{i}"},
        )
        for i in range(31)
    ]
    walls.append(
        Facade(
            "far",
            np.array([config.OFFICE_E + 100, config.OFFICE_N]),
            np.array([config.OFFICE_E + 106, config.OFFICE_N]),
            8,
            props={"building_id": "far"},
        )
    )
    labels = {f.id: {"usable": False, "problem": "scaffolding"} for f in walls}
    jobs = list(repair_jobs(walls, tmp_path, labels))
    assert len(jobs) == 25
    assert all(j["meta"]["facade_id"] != "far" and j["expect_images"] == 1 for j in jobs)
