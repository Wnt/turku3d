"""Regenerate tiny acceptance fixtures; run with the native pipeline Python from repo root."""

import json
from pathlib import Path

from turku3d.col.fixtures import MAT, Y0, Mesh, S
from turku3d.col.format import write_col

root = Path(__file__).resolve().parents[1] / "fixtures" / "acceptance"
root.mkdir(parents=True, exist_ok=True)
for i in range(2):
    for j in range(2):
        mesh = Mesh()
        mesh.ground(cell=S)
        target = root / "flat" / f"c_{i}_{j}.col"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(write_col(mesh.tile(i, j), encoding="raw"))
mesh = Mesh()
mesh.ground(cell=S)
mesh.box(20, 40, Y0, Y0 + 0.30, 20, 40, MAT["curb"], MAT["sidewalk"])
(root / "curb30.col").write_bytes(write_col(mesh.tile(0, 0), encoding="raw"))
(root / "curbs.geojson").write_text(
    json.dumps(
        {
            "type": "FeatureCollection",
            "turku3d_coords": "engine-xz",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"id": "planted-30cm", "step": 0.25},
                    "geometry": {"type": "LineString", "coordinates": [[22, 20], [38, 20]]},
                }
            ],
        },
        indent=2,
    )
    + "\n"
)

mesh = Mesh()
mesh.ground(cell=S / 5, holes=[(S * 0.4, S * 0.6, S * 0.4, S * 0.6)])
(root / "hole").mkdir(exist_ok=True)
(root / "hole" / "c_0_0.col").write_bytes(write_col(mesh.tile(0, 0), encoding="raw"))

mesh = Mesh()
mesh.ground(cell=S)
mesh.box(20, 50, Y0, Y0 + 0.12, 20, 50, MAT["curb"], MAT["sidewalk"])
(root / "curb12.col").write_bytes(write_col(mesh.tile(0, 0), encoding="raw"))
