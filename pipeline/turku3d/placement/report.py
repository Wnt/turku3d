"""Self-contained orthophoto map and one audit row per violation."""

import base64
import io
import json
from collections import Counter
from html import escape

from turku3d import config as C


def ortho_image(bounds, source_area):
    import rasterio
    from PIL import Image
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    path = C.raw_dir("ortho") / "ortho2022to" / f"{source_area}.vrt"
    with rasterio.open(path) as ds:
        window = from_bounds(
            C.ORIGIN_E + bounds.xmin,
            C.ORIGIN_N - bounds.zmax,
            C.ORIGIN_E + bounds.xmax,
            C.ORIGIN_N - bounds.zmin,
            ds.transform,
        )
        rgb = ds.read(
            [1, 2, 3], window=window, out_shape=(3, 1200, 1200), resampling=Resampling.average
        )
    buffer = io.BytesIO()
    Image.fromarray(rgb.transpose(1, 2, 0)).save(buffer, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def write_report(layer, bounds, records, hard, source_area):
    root = C.work_root() / "reports/placement" / layer
    root.mkdir(parents=True, exist_ok=True)
    report = {
        "layer": layer,
        "bounds": bounds.as_tuple(),
        "counts": dict(Counter(r["status"] for r in records)),
        "hard_violations": hard,
        "objects": records,
        "follow_ups": [
            "Model Aurasilta and other bridge decks in structures; re-run omitted objects."
        ],
    }
    path = root / "placement.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    rows, markers = [], []
    for r in records:
        p = r["after"] or r["before"]
        x = (p["x"] - bounds.xmin) / (bounds.xmax - bounds.xmin) * 1200
        z = (p["z"] - bounds.zmin) / (bounds.zmax - bounds.zmin) * 1200
        color = {"dropped": "#ff5544", "corrected": "#ffcc22", "accepted": "#44ff88"}[r["status"]]
        markers.append(
            f'<circle cx="{x}" cy="{z}" r="4" fill="{color}"><title>'
            f"{escape(r['object_id'])}: {escape(', '.join(r['violations']))}"
            "</title></circle>"
        )
        for violation in r["violations"]:
            rows.append(
                "<tr>"
                + "".join(
                    f"<td>{escape(str(v))}</td>"
                    for v in (
                        r["object_id"],
                        r["kind"],
                        violation,
                        r["status"],
                        [round(p[k], 3) for k in ("x", "y", "z")],
                    )
                )
                + "</tr>"
            )
    image = ortho_image(bounds, source_area)
    (root / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Placement audit</title>'
        "<style>body{font:15px system-ui;background:#182027;color:#fff;margin:24px}"
        "svg{width:min(90vw,1000px)}td,th{padding:8px;border-bottom:1px solid #556}"
        "table{border-collapse:collapse}</style>"
        f"<h1>{escape(layer)} placement audit</h1><p>{escape(str(report['counts']))}</p>"
        "<p>Green: accepted · Yellow: corrected · Red: omitted. Coordinates: engine X/Y/Z metres.</p>"
        f'<svg viewBox="0 0 1200 1200"><image href="{image}" width="1200" height="1200"/>'
        + "".join(markers)
        + "</svg><table><tr><th>Object</th><th>Kind</th><th>Violation</th>"
        "<th>Resolution</th><th>Position</th></tr>" + "".join(rows) + "</table>"
    )
    return path
