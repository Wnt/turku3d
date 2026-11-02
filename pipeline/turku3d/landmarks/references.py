"""Free Wikimedia references and parameter-only vision extraction, resumable per building.

Images stay under data/research/landmarks. The vision process emits JSON parameters,
never textures; only procedural.py turns these measurements into game pixels.
"""

import argparse
import hashlib
import json
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

import requests

AGENT = "Turku3D-landmark-research/1.0 (architectural parameter estimation; local research)"


def get_json(url, params=None):
    r = requests.get(url, params=params, headers={"User-Agent": AGENT}, timeout=40)
    r.raise_for_status()
    return r.json()


def gather(record, root, limit=4):
    directory = root / record["id"].replace("/", "_")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "references.json"
    if manifest.exists():
        old = json.loads(manifest.read_text())
        if all((directory / r["file"]).exists() for r in old.get("images", [])) and old.get(
            "images"
        ):
            old["record"] = record
            old["building_id"] = record.get("building_id", record["id"])
            manifest.write_text(json.dumps(old, indent=2, ensure_ascii=False) + "\n")
            return old
    tags = record.get("tags", {})
    name = tags.get("name:en") or record["name"]
    data = get_json(
        "https://commons.wikimedia.org/w/api.php",
        dict(
            action="query",
            format="json",
            generator="search",
            gsrsearch=f"{name} exterior",
            gsrnamespace=6,
            gsrlimit=limit,
            prop="imageinfo",
            iiprop="url|extmetadata",
            iiurlwidth=1600,
        ),
    )
    # P18 provides a building-specific photograph when name search has no exterior hit.
    if tags.get("wikidata"):
        entity = get_json(
            "https://www.wikidata.org/w/api.php",
            dict(action="wbgetentities", format="json", ids=tags["wikidata"], props="claims"),
        )
        claims = entity.get("entities", {}).get(tags["wikidata"], {}).get("claims", {})
        titles = [
            "File:" + r["mainsnak"]["datavalue"]["value"]
            for r in claims.get("P18", [])
            if "datavalue" in r.get("mainsnak", {})
        ]
        if titles:
            photos = get_json(
                "https://commons.wikimedia.org/w/api.php",
                dict(
                    action="query",
                    format="json",
                    titles="|".join(titles[:2]),
                    prop="imageinfo",
                    iiprop="url|extmetadata",
                    iiurlwidth=1280,
                ),
            )
            data.setdefault("query", {}).setdefault("pages", {}).update(
                photos.get("query", {}).get("pages", {})
            )
    images = []
    errors = []
    if record["id"] == "way/437169589":
        import shutil

        source = root.parent / "google3d-compare/streetview/10-pano-plaza-lookaround.jpg"
        if source.exists():
            target = directory / "local-plaza-lookaround.jpg"
            shutil.copyfile(source, target)
            images.append(
                dict(
                    file=target.name,
                    url="local:" + str(source),
                    sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                    licence="reference-only; never distribute",
                )
            )
    for page in sorted(
        data.get("query", {}).get("pages", {}).values(), key=lambda r: r.get("index", 0)
    ):
        if not page.get("imageinfo"):
            continue
        info = page["imageinfo"][0]
        url = info.get("thumburl") or info["url"]
        r = requests.get(url, headers={"User-Agent": AGENT}, timeout=40)
        if r.status_code != 200:
            errors.append(dict(url=url, status=r.status_code))
            continue
        if not r.headers.get("Content-Type", "").startswith("image/"):
            continue
        suffix = Path(urllib.parse.urlparse(url).path).suffix
        filename = f"commons-{page['pageid']}{suffix}"
        (directory / filename).write_bytes(r.content)
        meta = info.get("extmetadata", {})
        images.append(
            dict(
                file=filename,
                url=url,
                page=info.get("descriptionurl"),
                sha256=hashlib.sha256(r.content).hexdigest(),
                licence=meta.get("LicenseShortName", {}).get("value"),
                artist=meta.get("Artist", {}).get("value"),
            )
        )
    result = dict(
        building_id=record.get("building_id", record["id"]),
        record=record,
        images=images,
        download_errors=errors,
    )
    manifest.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


def parameter_schema(value):
    if isinstance(value, dict):
        return dict(
            type="object",
            properties={k: parameter_schema(v) for k, v in value.items()},
            required=list(value),
            additionalProperties=False,
        )
    if isinstance(value, list):
        item = (
            {"type": "number"}
            if value and all(isinstance(v, (int, float)) for v in value)
            else parameter_schema(value[0])
            if value
            else {"type": "number"}
        )
        return dict(type="array", items=item)
    if isinstance(value, str):
        return {"type": "string"}
    return {"type": "integer" if isinstance(value, int) else "number"}


def describe(reference, directory, output, model="gpt-6-astra"):
    from .descriptions import ROOT

    if not reference["images"]:
        raise ValueError("No reference images; refusing invented description")
    template = json.loads((ROOT / "103371570S.json").read_text())
    key = reference["building_id"]
    template["wall_material"] = "brick"
    template["windows"]["rows_m"] = [template["windows"]["sill_m"]]
    prompt = (
        "Estimate architectural parameters of the ONE named building in the supplied exterior photos. "
        "Return JSON only with this exact structure, changing values to this building. No painting or image generation. "
        "Physical dimensions are estimates: record confidence, source filenames, what is measured versus uncertain, "
        "and facade observations. Never transplant the example cathedral window arrangement to a different building. "
        "Use the supplied height evidence where present. Colours are estimated neutral albedo, not shadow pixels. "
        "All size_m and colour arrays must be finite positive numeric arrays of length 2 and 3 respectively. "
        "wall_material is brick, stone, render, wood, or concrete; brick.colour supplies main wall colour even for render. windows.type is rectangular, round-arched, or gothic-lancet. windows.rows_m lists all observed sill elevations above ground, not just one row. Roof material is copper, metal, zinc, slate, or tile. brick.bond is running, monk, or flemish. tower opening_rows are [sill_m,width_m,height_m,count]. "
        "Set tower.minimum_height_m high if there is no tower. Roof material must match actual evidence. "
        f"building_id must be {json.dumps(key)}. version=1. Example schema: {json.dumps(template)}\n"
        f"Building evidence: {json.dumps(reference)}"
    )
    with tempfile.TemporaryDirectory() as scratch:
        result = Path(scratch) / "description.json"
        schema = Path(scratch) / "schema.json"
        schema.write_text(json.dumps(parameter_schema(template)))
        cmd = [
            "codex",
            "exec",
            "-m",
            model,
            "-s",
            "read-only",
            "--skip-git-repo-check",
            "--output-schema",
            str(schema),
            "-C",
            scratch,
            "-o",
            str(result),
        ]
        for image in reference["images"]:
            cmd += ["-i", str(directory / image["file"])]
        run = subprocess.run(cmd + ["-"], input=prompt, text=True, capture_output=True, timeout=600)
        if run.returncode or not result.exists():
            raise RuntimeError(f"Vision extraction failed: exit {run.returncode}")
        text = result.read_text()
        (directory / "vision-response.txt").write_text(text)
        d = json.loads(text[text.find("{") : text.rfind("}") + 1])
    (directory / "vision-observation.json").write_text(
        json.dumps(d, indent=2, ensure_ascii=False) + "\n"
    )
    validate(d, key)
    d["reference_hashes"] = [r["sha256"] for r in reference["images"]]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    return d


def validate(d, key):
    import math

    if d.get("version") != 1 or d.get("building_id") != key:
        raise ValueError("Description identity mismatch")
    for material in ("brick", "stone"):
        for field in ("colour", "mortar_colour"):
            d[material][field] = [round(v) for v in d[material][field]]
        for field, count in [("size_m", 2), ("colour", 3), ("mortar_colour", 3)]:
            a = d[material][field]
            if len(a) != count or not all(
                isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in a
            ):
                raise ValueError(f"Invalid {material}.{field}")
        if not 0 < d[material]["mortar_m"] < min(d[material]["size_m"]):
            raise ValueError("Invalid mortar width")
    d["trim_colour"] = [round(v) for v in d["trim_colour"]]
    for field in ("width_m", "height_m", "spacing_m"):
        if not 0.1 <= d["windows"][field] <= 30:
            raise ValueError(f"Invalid window {field}")
    if not 0 <= d["windows"]["mullions"] <= 8:
        raise ValueError("Invalid mullion count")
    for row in d["tower"]["opening_rows"]:
        if len(row) != 4 or not all(math.isfinite(v) and v >= 0 for v in row) or row[3] > 20:
            raise ValueError("Invalid tower row")
    return d


def main():
    from turku3d import config as C

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ranked", type=Path, required=True)
    p.add_argument("--root", type=Path, default=C.data_root() / "research/landmarks")
    p.add_argument("--descriptions", type=Path, default=C.data_root() / "landmark-descriptions")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--describe", action="store_true")
    p.add_argument("--id", action="append")
    a = p.parse_args()
    rows = json.loads(a.ranked.read_text())["buildings"]
    done = []
    for r in [r for r in rows if r["tier"] == "landmark" and (not a.id or r["id"] in a.id)][
        : a.limit
    ]:
        try:
            ref = gather(r, a.root)
            key = ref["building_id"]
            directory = a.root / r["id"].replace("/", "_")
            target = a.descriptions / (key.replace("/", "_") + ".json")
            if a.describe and not target.exists():
                describe(ref, directory, target)
            result = dict(
                id=r["id"],
                references=len(ref["images"]),
                description=str(target) if target.exists() else None,
            )
        except Exception as error:
            result = dict(id=r["id"], error=str(error))
        done.append(result)
        print(json.dumps(result), flush=True)
    (a.root / "pipeline-results.json").write_text(json.dumps(done, indent=2) + "\n")


if __name__ == "__main__":
    main()
