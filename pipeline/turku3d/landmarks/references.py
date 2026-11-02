"""Free Wikimedia references and parameter-only vision extraction, resumable per building.

Images stay under data/research/landmarks. The vision process emits JSON parameters,
never textures; only procedural.py turns these measurements into game pixels.
"""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path

import requests

AGENT = "Turku3D-landmark-research/1.0 (architectural parameter estimation; local research)"
_NEXT_REQUEST = 0


def get_json(url, params=None):
    global _NEXT_REQUEST
    time.sleep(max(0, _NEXT_REQUEST - time.monotonic()))
    _NEXT_REQUEST = time.monotonic() + 2
    r = requests.get(url, params=params, headers={"User-Agent": AGENT}, timeout=40)
    r.raise_for_status()
    return r.json()


def local_reference_views(record, root, directory):
    """Find nearby, facing local panoramas/3D views without building-specific lists."""
    from turku3d import config as C

    poses = root.parent / "turku3d-compare/cameras.json"
    if not poses.exists():
        return []
    e = C.ORIGIN_E + record["location_xz"][0]
    n = C.ORIGIN_N - record["location_xz"][1]
    candidates = []
    for pose in json.loads(poses.read_text())["cameras"]:
        pe, pn, _h, yaw, _pitch, fov = map(float, pose["cam"].split(",")[:6])
        distance = math.hypot(e - pe, n - pn)
        bearing = math.degrees(math.atan2(e - pe, n - pn))
        error = abs((bearing - yaw + 180) % 360 - 180)
        horizontal = math.degrees(math.atan(math.tan(math.radians(fov / 2)) * 16 / 9))
        if distance > 300 or error > horizontal - 5:
            continue
        for subdir, suffix in (("streetview", "jpg"), ("google3d", "png")):
            for image in (root.parent / "google3d-compare" / subdir).glob(
                f"*-{pose['id']}-*.{suffix}"
            ):
                candidates.append((distance, error, image, pose))
    images = []
    for distance, error, image, pose in sorted(candidates, key=lambda item: item[:2])[:3]:
        target = directory / ("local-" + image.name)
        if not target.exists():
            shutil.copyfile(image, target)
        images.append(
            dict(
                file=target.name,
                url="local:" + str(image),
                sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                licence="reference-only; never distribute",
                pose=pose["cam"],
                distance_m=round(distance, 1),
                bearing_error_deg=round(error, 1),
            )
        )
    return images


def gather(record, root, limit=4):
    directory = root / record["id"].replace("/", "_")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "references.json"
    if manifest.exists():
        old = json.loads(manifest.read_text())
        if (
            old.get("gather_version") == 2
            and all((directory / r["file"]).exists() for r in old.get("images", []))
            and old.get("images")
        ):
            old["record"] = record
            old["building_id"] = record.get("building_id", record["id"])
            known = {image["sha256"] for image in old["images"]}
            old["images"].extend(
                image
                for image in local_reference_views(record, root, directory)
                if image["sha256"] not in known
            )
            manifest.write_text(json.dumps(old, indent=2, ensure_ascii=False) + "\n")
            return old
    tags = record.get("tags", {})
    name = tags.get("name:en") or record["name"]
    names = list(
        dict.fromkeys(
            filter(
                None,
                [
                    name,
                    record["name"],
                    tags.get("alt_name:en"),
                    tags.get("alt_name"),
                    tags.get("name:sv"),
                ],
            )
        )
    )
    data = get_json(
        "https://commons.wikimedia.org/w/api.php",
        dict(
            action="query",
            format="json",
            generator="search",
            gsrsearch=" OR ".join('"' + n.replace('"', "") + '"' for n in names)
            + " filetype:bitmap",
            gsrnamespace=6,
            gsrlimit=limit,
            prop="imageinfo",
            iiprop="url|extmetadata",
            iiurlwidth=1600,
        ),
    )
    # Exact names avoid unrelated book scans; entity P18 photographs sort first.
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
    if not any(page.get("imageinfo") for page in data.get("query", {}).get("pages", {}).values()):
        from pyproj import Transformer

        from turku3d import config as C

        x, z = record["location_xz"]
        lon, lat = Transformer.from_crs(3877, 4326, always_xy=True).transform(
            C.ORIGIN_E + x, C.ORIGIN_N - z
        )
        data = get_json(
            "https://commons.wikimedia.org/w/api.php",
            dict(
                action="query",
                format="json",
                generator="geosearch",
                ggscoord=f"{lat}|{lon}",
                ggsradius=100,
                ggsnamespace=6,
                ggslimit=limit,
                prop="imageinfo",
                iiprop="url|extmetadata",
                iiurlwidth=1600,
            ),
        )
    images = local_reference_views(record, root, directory)
    errors = []
    for page in sorted(
        data.get("query", {}).get("pages", {}).values(), key=lambda r: r.get("index", 0)
    ):
        if not page.get("imageinfo"):
            continue
        info = page["imageinfo"][0]
        if Path(urllib.parse.urlparse(info["url"]).path).suffix.lower() not in (
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
        ):
            continue
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
                title=page["title"],
                url=url,
                page=info.get("descriptionurl"),
                sha256=hashlib.sha256(r.content).hexdigest(),
                licence=meta.get("LicenseShortName", {}).get("value"),
                artist=meta.get("Artist", {}).get("value"),
            )
        )
    result = dict(
        gather_version=2,
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
    template["reference_match"] = "verified"
    template["reference_confidence"] = 0.8
    template["windows"]["rows_m"] = [template["windows"]["sill_m"]]
    prompt = (
        "Estimate architectural parameters of the ONE named building in the supplied exterior photos. "
        "Return JSON only with this exact structure, changing values to this building. No painting or image generation. "
        "Physical dimensions are estimates: record confidence, source filenames, what is measured versus uncertain, "
        "and facade observations. Never transplant the example cathedral window arrangement to a different building. "
        "First verify that the photos depict this named building. Set reference_match to verified only if they do; otherwise rejected, with reference_confidence=0 and explain the mismatch in observations. No invented parameter defaults for rejected photos will be published. "
        "Use the supplied height evidence where present. Colours are estimated neutral albedo, not shadow pixels. "
        "All size_m and colour arrays must be finite positive numeric arrays of length 2 and 3 respectively. "
        "wall_material is brick, stone, render, wood, or concrete; brick.colour supplies main wall colour even for render. windows.type is rectangular, round-arched, or gothic-lancet. windows.rows_m describes ONLY the main nave or primary building body, with the SAME window height on every row. NEVER mix tower, belfry, clerestory, turrets and side-wings into this global array. Successive rows must differ by at least windows.height_m. Keep uncertain global rows empty and record region-specific observations in prose instead. Roof material is copper, metal, zinc, slate, or tile. brick.bond is running, monk, or flemish. tower opening_rows are [sill_m,width_m,height_m,count]. "
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
        for attempt in range(2):
            run = subprocess.run(
                cmd + ["-"], input=prompt, text=True, capture_output=True, timeout=600
            )
            if run.returncode or not result.exists():
                raise RuntimeError(f"Vision extraction failed: exit {run.returncode}")
            text = result.read_text()
            (directory / f"vision-response-{attempt}.txt").write_text(text)
            d = json.loads(text[text.find("{") : text.rfind("}") + 1])
            (directory / f"vision-observation-{attempt}.json").write_text(
                json.dumps(d, indent=2, ensure_ascii=False) + "\n"
            )
            (directory / "vision-observation.json").write_text(
                json.dumps(d, indent=2, ensure_ascii=False) + "\n"
            )
            try:
                validate(d, key)
                break
            except ValueError as error:
                if d.get("building_id") == key and (
                    d.get("reference_match") != "verified"
                    or d.get("reference_confidence", 0) < 0.5
                ):
                    # Persist the negative finding too: an older automatic
                    # estimate must not survive newly disproved identity.
                    publish_description(d, reference, output)
                if attempt or d.get("reference_match") != "verified":
                    raise
                prompt += f"\nPrevious estimate failed physical validation: {error}. Correct this without inventing unseen features. Previous estimate: {json.dumps(d)}"
    publish_description(d, reference, output)
    return d


def publish_description(d, reference, output):
    d["reference_hashes"] = [r["sha256"] for r in reference["images"]]
    d["evidence_hash"] = evidence_hash(reference)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(output)


def evidence_hash(reference):
    return hashlib.sha256(
        json.dumps(reference["record"].get("evidence", {}), sort_keys=True).encode()
    ).hexdigest()


def validate(d, key):
    import math

    if d.get("version") != 1 or d.get("building_id") != key:
        raise ValueError("Description identity mismatch")
    if d.get("reference_match", "verified") != "verified" or d.get("reference_confidence", 1) < 0.5:
        raise ValueError("References do not confidently identify this building")
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
    rows = sorted(d["windows"].get("rows_m", [d["windows"]["sill_m"]]))
    if any(b - a < d["windows"]["height_m"] for a, b in zip(rows[:-1], rows[1:], strict=True)):
        raise ValueError(
            "Global window rows overlap: use main-body rows only; move other architectural regions into observations"
        )
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
    queue = [r for r in rows if r["tier"] == "landmark" and (not a.id or r["id"] in a.id)][
        : a.limit
    ]
    for index, r in enumerate(queue):
        try:
            ref = gather(r, a.root)
            key = ref["building_id"]
            directory = a.root / r["id"].replace("/", "_")
            target = a.descriptions / (key.replace("/", "_") + ".json")
            existing = json.loads(target.read_text()) if target.exists() else {}
            current = (
                existing.get("reference_match") == "verified"
                and existing.get("evidence_hash") == evidence_hash(ref)
                and existing.get("reference_hashes") == [r["sha256"] for r in ref["images"]]
            )
            if a.describe and not current:
                describe(ref, directory, target)
            result = dict(
                id=r["id"],
                references=len(ref["images"]),
                description=str(target) if target.exists() else None,
            )
        except Exception as error:
            result = dict(id=r["id"], error=str(error))
            if isinstance(error, requests.HTTPError) and error.response.status_code == 429:
                retry = error.response.headers.get("Retry-After", "120")
                result["retry_after"] = retry
                done.extend(
                    [
                        result,
                        *[
                            dict(
                                id=item["id"],
                                error="deferred: Commons rate limit",
                                retry_after=retry,
                            )
                            for item in queue[index + 1 :]
                        ],
                    ]
                )
                print(json.dumps(result), flush=True)
                break
        done.append(result)
        print(json.dumps(result), flush=True)
    (a.root / "pipeline-results.json").write_text(json.dumps(done, indent=2) + "\n")


if __name__ == "__main__":
    main()
