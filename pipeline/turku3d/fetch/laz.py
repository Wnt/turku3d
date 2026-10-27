"""City lidar LAZ tiles -> raw/laz/{pistepilvi,maanpinta}/<xmin>_<ymax>.laz (curl -L, resumable)."""

from __future__ import annotations

import csv
import os

from .. import config
from .common import Registry, curl_download, curl_head, human, kind_dir, sha256_file


def known_tiles() -> set[tuple[str, str]]:
    """(dataset, name) pairs listed in data-manifests/laz-tiles.csv (empty set if the csv is missing)."""
    out: set[tuple[str, str]] = set()
    if not config.LAZ_TILES_CSV.exists():
        return out
    with open(config.LAZ_TILES_CSV, newline="") as f:
        for row in csv.DictReader(f):  # columns: dataset,tile,xmin,ymin,xmax,ymax,url
            out.add((row["dataset"].strip(), row["tile"].strip()))
    return out


def fetch_area(
    area: config.Area, datasets=config.LAZ_DATASETS, verify: bool = False, log=print
) -> list[dict]:
    return fetch_tiles(area.laz_tiles, datasets, verify=verify, log=log)


def fetch_tiles(
    tiles: list[config.LazTile],
    datasets=config.LAZ_DATASETS,
    verify: bool = False,
    log=print,
) -> list[dict]:
    root = kind_dir("laz")
    reg = Registry(root)
    listed = known_tiles()
    recs = []
    for t in tiles:
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
            # Register the already supplied 3x3 mirror, checking its original digest first.
            supplied = config.raw_dir("laz-3x3") / dest.name
            sums = supplied.parent / "SHA256SUMS"
            if ds == "pistepilvi" and supplied.exists() and sums.exists():
                expected = {
                    line.split()[1].lstrip("*").removeprefix("./"): line.split()[0]
                    for line in sums.read_text().splitlines()
                    if line.strip()
                }
                digest = sha256_file(supplied)
                if expected.get(supplied.name) != digest:
                    raise ValueError(f"supplied LAZ checksum mismatch: {supplied.name}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    os.link(supplied, dest)
                elif sha256_file(dest) != digest:
                    raise ValueError(f"existing LAZ differs from supplied tile: {dest.name}")
                recs.append(
                    reg.record(
                        dest,
                        url=url,
                        sha256=digest,
                        dataset=ds,
                        tile=t.name,
                        imported_from="raw/laz-3x3",
                    )
                )
                log(f"laz  {ds}/{t.name}: registered supplied file ({human(dest.stat().st_size)})")
                continue
            head = curl_head(url)
            if head["status"] != 200:
                raise RuntimeError(f"HEAD {url} -> {head['status']}")
            r, now = curl_download(
                url,
                dest,
                reg,
                expected_bytes=head["content_length"],
                verify=verify,
                dataset=ds,
                tile=t.name,
                last_modified=head["last_modified"],
                etag=head["etag"],
                final_url=head["url"],
            )
            log(
                f"laz  {ds}/{t.name}: {'downloaded' if now else 'cached'} {human(r['bytes'])} sha256 {r['sha256'][:12]}"
            )
            recs.append(r)
    return recs
