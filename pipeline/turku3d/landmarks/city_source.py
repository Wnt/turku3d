"""Extract the complete municipality from the existing read-only Finland snapshot."""

import hashlib
import json
import subprocess
from pathlib import Path


def extract(source, directory, relation=399906):
    """Turku's OSM administrative relation is 399906 (admin_level=8).

    All derived files live in research storage, never under data/raw. A source
    content hash and municipality identity make the reusable inventory explicit.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    from pyproj import Transformer
    from shapely.affinity import affine_transform
    from shapely.geometry import shape
    from shapely.ops import transform

    from turku3d import config as C

    directory.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    converter = hashlib.sha256(
        Path(__file__).read_bytes() + json.dumps(C.ORIGIN).encode()
    ).hexdigest()
    manifest = directory / "manifest.json"
    output = directory / "buildings.parquet"
    if manifest.exists() and output.exists():
        old = json.loads(manifest.read_text())
        if (
            old.get("source_sha256") == digest
            and old.get("relation") == relation
            and old.get("converter_sha256") == converter
        ):
            return output, old

    def run(*args):
        return subprocess.run(["osmium", *map(str, args)], check=True, capture_output=True).stdout

    boundary_pbf = directory / "boundary.osm.pbf"
    run("getid", "-r", source, f"r{relation}", "--overwrite", "-o", boundary_pbf)
    features = json.loads(run("export", boundary_pbf, "-f", "geojson", "-a", "type,id"))["features"]
    boundary = next(
        f
        for f in features
        if f["properties"].get("@type") == "relation"
        and int(f["properties"].get("@id", 0)) == relation
        and shape(f["geometry"]).area > 0
    )
    properties = boundary["properties"]
    if properties.get("boundary") != "administrative" or properties.get("name") != "Turku":
        raise ValueError("Municipality boundary does not identify Turku")
    polygon = shape(boundary["geometry"])
    boundary_json = directory / "boundary.geojson"
    boundary_json.write_text(json.dumps(dict(type="FeatureCollection", features=[boundary])))
    clip = directory / "municipality.osm.pbf"
    run("extract", "--strategy", "smart", "-p", boundary_json, source, "--overwrite", "-o", clip)
    buildings = directory / "buildings.osm.pbf"
    run(
        "tags-filter",
        clip,
        "wr/building",
        "wr/historic=castle,manor,church",
        "--overwrite",
        "-o",
        buildings,
    )
    data = run("export", buildings, "-f", "geojsonseq", "-a", "type,id")
    projected = Transformer.from_crs(4326, 3877, always_xy=True)
    rows = []
    for line in data.splitlines():
        if not line.strip():
            continue
        f = json.loads(line.lstrip(b"\x1e"))
        geometry = shape(f["geometry"])
        if geometry.area == 0 or not polygon.covers(geometry.representative_point()):
            continue
        props = dict(f["properties"])
        kind, key = props.pop("@type"), int(props.pop("@id"))
        world = transform(projected.transform, geometry)
        local = affine_transform(world, [1, 0, 0, -1, -C.ORIGIN_E, C.ORIGIN_N])
        rows.append(
            dict(
                osm_type=kind,
                osm_id=key,
                tags=json.dumps(props, ensure_ascii=False),
                geometry_local=local.wkb,
            )
        )
    temporary = output.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_pylist(rows), temporary)
    temporary.replace(output)
    info = dict(
        source=source.name,
        source_sha256=digest,
        converter_sha256=converter,
        relation=relation,
        scope="complete Turku administrative area; footprint representative point inside boundary",
        municipality=properties,
        features=len(rows),
        licence="OpenStreetMap ODbL 1.0",
        boundary_sha256=hashlib.sha256(boundary_json.read_bytes()).hexdigest(),
        inventory_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
    )
    manifest.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n")
    # Geometry and hashes suffice for reuse; intermediate extracts are reproducible.
    clip.unlink()
    buildings.unlink()
    return output, info


def main():
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(extract(a.source, a.out)[1]))


if __name__ == "__main__":
    main()
