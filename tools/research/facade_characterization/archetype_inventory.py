"""Count deterministic assignments over every unique building in the cached city register.

Reads shared raw inputs only. Results, including stable IDs, stay in the sandbox.
Example: PYTHONPATH=pipeline:textures python tools/research/facade_characterization/archetype_inventory.py
"""

import argparse
import json
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

from turku3d import config as C
from turku3d.coarse.register import attributes, district, read_register
from turkutex.facade_archetypes import ARCHETYPES, archetype


def inventory(raw):
    paths = sorted(raw.rglob("bldg_Building_LOD2.parquet"))
    buildings = {}
    for path in paths:
        names = pq.read_schema(path).names
        columns = [
            k
            for k in (
                "feature_key",
                "pysyvarakennustunnus",
                "yearOfConstruction",
                "valmistunut",
                "kerrosluku",
                "storeysAboveGround",
                "usage",
                "function",
                "kiinteistotunnus",
            )
            if k in names
        ]
        for batch in pq.ParquetFile(path).iter_batches(columns=columns):
            for row in batch.to_pylist():
                key = row.get("pysyvarakennustunnus") or row.get("feature_key")
                if not key or str(key) in buildings:
                    continue
                attrs = attributes(
                    dict(
                        kerrosluku=row.get("kerrosluku") or row.get("storeysAboveGround"),
                        kottovuosi=row.get("yearOfConstruction") or row.get("valmistunut"),
                    )
                )
                attrs.update(
                    use=row.get("usage") or row.get("function") or "unknown",
                    district=district(row.get("kiinteistotunnus")),
                )
                buildings[str(key)] = attrs
    register_paths = sorted(
        [*raw.rglob("GIS_Rakennukset.parquet"), *raw.rglob("kanta_Rakennus.parquet")]
    )
    for key, attrs in read_register(register_paths).items():
        buildings.setdefault(key, {}).update(attrs)
    counts = Counter(archetype(attrs) for attrs in buildings.values())
    return dict(
        scope="All unique IDs in cached LOD2/GIS/Kanta register files; no claim of uncached city coverage",
        lod2_files=len(paths),
        register_files=len(register_paths),
        buildings=len(buildings),
        missing_year=sum(not b.get("construction_year") for b in buildings.values()),
        missing_storeys=sum(not b.get("levels") for b in buildings.values()),
        distribution={name: counts[name] for name in ARCHETYPES},
        assignments={key: archetype(attrs) for key, attrs in sorted(buildings.items())},
        limitations="OSM and footprint/frontage covariates are enriched per generated tile; this inventory uses register fields only.",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=C.data_root() / "raw/wfs")
    parser.add_argument(
        "--out", type=Path, default=C.work_root() / "reports/archetypes/register-distribution.json"
    )
    args = parser.parse_args()
    result = inventory(args.raw)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "assignments"}, indent=2))


if __name__ == "__main__":
    main()
