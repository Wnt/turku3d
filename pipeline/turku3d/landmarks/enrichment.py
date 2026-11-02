"""Refresh free heritage and page-view evidence for ranked city buildings."""

import argparse
import datetime as dt
import json
from pathlib import Path
from urllib.parse import quote

import requests

from .references import get_json

WFS = "https://geoserver.museovirasto.fi/geoserver/rajapinta_suojellut/wfs"


def heritage(root):
    """Cache only the Turku-region extract, never write shared raw data."""
    layers = {}
    for name in ("rky_alue", "suojellut_rakennukset_piste"):
        path = root / (name + ".geojson")
        if not path.exists():
            data = get_json(
                WFS,
                dict(
                    service="WFS",
                    version="2.0.0",
                    request="GetFeature",
                    typeNames="rajapinta_suojellut:" + name,
                    outputFormat="application/json",
                    srsName="EPSG:3877",
                    bbox="23447000,6694000,23478000,6726000,EPSG:3877",
                    count=10000,
                ),
            )
            if data.get("numberMatched", len(data.get("features", []))) > len(
                data.get("features", [])
            ):
                raise ValueError("Heritage response truncated; pagination required")
            path.write_text(json.dumps(data) + "\n")
        layers[name] = json.loads(path.read_text())["features"]
    return layers


def pageviews(article):
    language, title = article.split(":", 1)
    end = dt.date.today().replace(day=1) - dt.timedelta(days=1)
    start = end.replace(day=1)
    url = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{language}.wikipedia.org/all-access/user/{quote(title.replace(' ', '_'), safe='')}/monthly/{start:%Y%m%d}00/{end:%Y%m%d}00"
    data = get_json(url)
    return dict(
        monthly_pageviews=sum(row["views"] for row in data.get("items", [])),
        pageviews_period=f"{start:%Y-%m}",
        pageviews_source=url,
    )


def main():
    import shapely
    from shapely.affinity import affine_transform
    from shapely.geometry import shape

    from turku3d import config as C

    from .ranking import rank

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ranked", type=Path, required=True)
    p.add_argument("--pageviews", action="store_true")
    a = p.parse_args()
    root = a.ranked.parent / "registers"
    root.mkdir(parents=True, exist_ok=True)
    data = json.loads(a.ranked.read_text())
    layers = heritage(root)
    polygons = [shape(r["geometry"]) for r in layers["rky_alue"]]
    points = [shape(r["geometry"]) for r in layers["suojellut_rakennukset_piste"]]
    trees = [shapely.STRtree(polygons), shapely.STRtree(points)]
    viewcache = root / "pageviews.json"
    views = json.loads(viewcache.read_text()) if viewcache.exists() else {}
    for row in data["buildings"]:
        x, z = row["location_xz"]
        p = shapely.Point(C.ORIGIN_E + x, C.ORIGIN_N - z)
        e = row["evidence"]
        matches = trees[0].query(p, predicate="intersects")
        e["rky"] = [layers["rky_alue"][i]["properties"] for i in matches] or False
        footprint = (
            affine_transform(
                shapely.from_wkt(row["footprint_wkt"]), [1, 0, 0, -1, C.ORIGIN_E, C.ORIGIN_N]
            )
            if row.get("footprint_wkt")
            else p
        )
        matches = trees[1].query(footprint.buffer(3), predicate="intersects")
        e["protected"] = [
            layers["suojellut_rakennukset_piste"][i]["properties"] for i in matches
        ] or False
        if row["name"] == row["id"] and e["protected"]:
            item = e["protected"][0]
            row["name"] = (
                " ".join(
                    str(item.get(k, "")).strip() for k in ("kohdenimi", "rakennusnimi")
                ).strip()
                or row["id"]
            )
        e["heritage_source"] = WFS
        e["protected_match"] = "register point inside building footprint with 3m location tolerance"
        article = row["tags"].get("wikipedia")
        if article and a.pageviews:
            if article not in views:
                try:
                    views[article] = pageviews(article)
                except (requests.RequestException, ValueError) as error:
                    views[article] = {"pageviews_error": str(error)}
                viewcache.write_text(json.dumps(views, indent=2) + "\n")
            e.update(views[article])
        row.update(rank(row["tags"], e))
    data["buildings"].sort(key=lambda r: (-r["score"], r["id"]))
    data["heritage_attribution"] = (
        "Finnish Heritage Agency (Museovirasto), CC BY 4.0; includes RKY and special-law protected register, not all local-plan protections."
    )
    a.ranked.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                rky=sum(bool(r["evidence"]["rky"]) for r in data["buildings"]),
                protected_candidates=sum(
                    bool(r["evidence"]["protected"]) for r in data["buildings"]
                ),
                landmarks=sum(r["tier"] == "landmark" for r in data["buildings"]),
            )
        )
    )


if __name__ == "__main__":
    main()
