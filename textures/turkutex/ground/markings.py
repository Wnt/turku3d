"""Vector road-paint polygons; no markings are inferred from aerial pixels."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Polygon, mapping
from shapely.ops import substring


def paint_polygons(
    line: LineString, kind: str, *, width: float, stripe=0.5, gap=0.5, dash=3.0
) -> list[Polygon]:
    """Crossing path runs across the road; lane/stop line runs along the painted line.

    Dimensions are metres, endpoints retain a common phase across visual tile clipping.
    """
    if (
        not line.is_simple
        or line.is_empty
        or line.length <= 0
        or min(width, stripe, dash) <= 0
        or gap < 0
    ):
        raise ValueError("invalid marking geometry/dimensions")
    if kind == "crosswalk":
        painted, period = stripe, stripe + gap
    elif kind == "lane_dashed":
        painted, period = dash, dash + gap
    elif kind in ("lane_solid", "stop_line"):
        return [line.buffer(width / 2, cap_style="flat", join_style="mitre")]
    else:
        raise ValueError(f"unsupported marking kind {kind}")
    result = []
    for start in np.arange(0, line.length, period):
        segment = substring(line, float(start), min(float(start + painted), line.length))
        if segment.geom_type == "LineString":
            result.append(segment.buffer(width / 2, cap_style="flat", join_style="mitre"))
    return result


def from_osm(path: Path, carriageway) -> tuple[list[dict], dict]:
    """OSM gives meaning; city carriageway polygons clip the paint to surveyed road bounds.

    Only explicit line geometry is usable. Point crossings and untagged lane/stop positions
    remain unresolved, rather than receiving invented headings or lane widths.
    """
    import geopandas as gpd

    frame = gpd.read_parquet(path)
    result, unresolved = [], []
    for row in frame.itertuples():
        tags = json.loads(row.tags)
        geometry = row.geometry
        crossing = tags.get("footway") == "crossing" or tags.get("highway") == "crossing"
        explicit = tags.get("road_marking")
        if not crossing and explicit not in ("stop_line", "lane_solid", "lane_dashed"):
            continue
        if tags.get("crossing:markings") == "no" or tags.get("crossing") == "unmarked":
            continue
        if geometry.geom_type != "LineString":
            unresolved.append({"osm_id": int(row.osm_id), "reason": "needs crossing line geometry"})
            continue
        kind = "crosswalk" if crossing else explicit
        try:
            width = float(
                tags.get("width", 3 if crossing else 0.5 if kind == "stop_line" else 0.12)
            )
            if not math.isfinite(width) or width <= 0:
                raise ValueError("invalid width")
        except (ValueError, TypeError):
            unresolved.append({"osm_id": int(row.osm_id), "reason": "unusable width"})
            continue
        for i, poly in enumerate(paint_polygons(geometry, kind, width=width)):
            clipped = poly.intersection(carriageway)
            if clipped.is_empty or clipped.area < 0.005:
                continue
            result.append(
                {
                    "type": "Feature",
                    "geometry": mapping(clipped),
                    "properties": {
                        "object_id": f"osm_{row.osm_id}_paint_{i}",
                        "kind": kind,
                        "material_key": "road_paint",
                        "source": f"osm/{row.osm_id}",
                        "width_m": width,
                        "width_assumed": "width" not in tags,
                        "position_source": "GIS:Katualueet clip",
                    },
                }
            )
    counts = {
        kind: sum(f["properties"]["kind"] == kind for f in result)
        for kind in ("crosswalk", "lane_solid", "lane_dashed", "stop_line")
    }
    return result, {
        "counts": counts,
        "unresolved": unresolved,
        "missing_kinds": [kind for kind, count in counts.items() if count == 0],
    }


def main(argv=None):
    import argparse

    import geopandas as gpd
    from shapely.ops import unary_union
    from turku3d import config as C

    from ..paths import TEXTURES_DIR
    from ..post.pipeline import sha

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--area", default="work-3x3")
    ap.add_argument("--osm", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=C.work_root() / "textures/ground/markings.geojson")
    a = ap.parse_args(argv)
    city_path = C.raw_dir("wfs") / a.area / "GIS_Katualueet.parquet"
    city = gpd.read_parquet(city_path)
    # Only carriageways, never sidewalk/cycleway polygons. City source values are Finnish.
    carriageway = unary_union(
        city.loc[
            city.Katuosalaji.astype(str).str.lower().str.contains("ajorata"), "geometry"
        ].values
    )
    if carriageway.is_empty:
        raise ValueError("no city carriageway polygons; cannot constrain crossing paint")
    features, report = from_osm(a.osm, carriageway)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": "ground-road-markings",
                "crs": {"type": "name", "properties": {"name": C.CRS_HORIZONTAL}},
                "features": features,
            }
        )
        + "\n"
    )
    report["inputs"] = [{"path": str(p), "sha256": sha(p)} for p in [city_path, a.osm]]
    report["source_licenses"] = json.loads(
        (TEXTURES_DIR / "library/ground-sources.json").read_text()
    )
    report["coordinates"] = C.CRS_HORIZONTAL
    report["drape"] = (
        "Assembler must project these polygons onto ground triangles and offset 0.005 m; never flat y=0 in shipped tiles"
    )
    a.out.with_suffix(".report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "polygons": len(features),
                "counts": report["counts"],
                "missing_kinds": report["missing_kinds"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
