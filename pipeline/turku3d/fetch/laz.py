"""City lidar LAZ tiles -> raw/laz/{pistepilvi,maanpinta}/<xmin>_<ymax>.laz (curl -L, resumable)."""

from __future__ import annotations

import csv

from .. import config
from .common import Registry, curl_download, curl_head, human, kind_dir


def known_tiles() -> set[tuple[str, str]]:
    """(dataset, name) pairs listed in data-manifests/laz-tiles.csv (empty set if the csv is missing)."""
    out: set[tuple[str, str]] = set()
    if not config.LAZ_TILES_CSV.exists():
        return out
    with open(config.LAZ_TILES_CSV, newline="") as f:
        for row in csv.DictReader(f):  # columns: dataset,tile,xmin,ymin,xmax,ymax,url
            out.add((row["dataset"].strip(), row["tile"].strip()))
    return out


def fetch_area(area: config.Area, datasets=config.LAZ_DATASETS, verify: bool = False, log=print) -> list[dict]:
    root = kind_dir("laz")
    reg = Registry(root)
    listed = known_tiles()
    recs = []
    for t in area.laz_tiles:
        for ds in datasets:
            if listed and (ds, t.name) not in listed:
                log(f"laz  {ds}/{t.name}: not in laz-tiles.csv, skipped")
                continue
            dest = root / ds / f"{t.name}.laz"
            url = t.url(ds)
            if reg.is_complete(dest, verify=verify):
                r = reg.get(dest)
                log(f"laz  {ds}/{t.name}: cached ({human(r['bytes'])})")
                recs.append(r)
                continue
            head = curl_head(url)
            if head["status"] != 200:
                raise RuntimeError(f"HEAD {url} -> {head['status']}")
            r, now = curl_download(url, dest, reg, expected_bytes=head["content_length"], verify=verify,
                                   dataset=ds, tile=t.name, last_modified=head["last_modified"],
                                   etag=head["etag"], final_url=head["url"])
            log(f"laz  {ds}/{t.name}: {'downloaded' if now else 'cached'} {human(r['bytes'])} sha256 {r['sha256'][:12]}")
            recs.append(r)
    return recs
