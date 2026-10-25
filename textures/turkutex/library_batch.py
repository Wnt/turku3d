"""Generate only missing library materials, with mandatory flatten, tiling, DeepBump and KTX2."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .building_jobs import latest
from .codex.runner import Runner, load_jobs
from .facade_batch import log_usage, post_code_hash, write_json
from .jobs import _write
from .paths import PROMPTS_DIR, textures_data_dir
from .post.maps import deepbump_available
from .post.pipeline import POST_VERSION, process, sha
from .streetview.facades import load_facades

# Each patch's metres are explicit so an assembler can bind metre-based UVs.
MATERIALS = {
    "render_smooth": ("fine-grained warm off-white Finnish lime render, subtle age, no cracks or windows", "render", 2, 2),
    "wood_clapboard_horizontal": ("traditional pale yellow painted horizontal timber clapboards, 14 cm board exposure", "wood_painted", 2, 2),
    "brick_exposed": ("Nordic red fired brick in running bond, 270 by 75 mm modules with grey mortar", "brick", 2, 2),
    "plinth_granite": ("Finnish grey-pink rusticated granite plinth blocks, roughly 0.6 by 0.3 metres", "stone", 2.4, 1.2),
    "roof_standing_seam_metal": ("dark charcoal painted Finnish standing-seam sheet-metal roof, parallel seams every 0.6 metres vertically", "metal_painted", 2.4, 2.4),
    "roof_clay_pantile": ("Finnish interlocking double-S red clay roof tiles, NOT Mediterranean barrel tiles, 0.3 by 0.4 metre modules", "clay_tile", 2.4, 2.4),
    "roof_felt": ("charcoal bituminous flat-roof felt, fine mineral granules, restrained weathering", "asphalt", 2, 2),
    "ground_asphalt": ("Finnish dark-grey street asphalt, 2 to 10 mm aggregate, no road markings", "asphalt", 2, 2),
    "ground_granite": ("Turku grey and pink granite street setts, irregular worn 10 to 15 cm stones, narrow sandy joints", "stone", 2, 2),
}


def required_keys(facades_path: Path) -> set[str]:
    keys = {"plinth_granite", "ground_asphalt", "ground_granite"}
    for f in load_facades(facades_path):
        if not f.props.get("street_facing", True):
            keys.add(f.props.get("material_key", "render_smooth"))
        keys.add(f.props.get("roof_material_key", "roof_standing_seam_metal"))
    return keys


def build_jobs(keys: set[str]) -> list[dict]:
    rules = "\n".join((PROMPTS_DIR / "fragments" / f"{part}.md").read_text()
                      for part in ("style", "albedo_rules", "negatives"))
    out = []
    for key in sorted(keys):
        if key not in MATERIALS:
            raise ValueError(f"No registered library recipe for {key}")
        desc, preset, width, height = MATERIALS[key]
        out.append({"id": key + ".library", "prompt":
                    f"Use built-in image_gen exactly once to generate one opaque photorealistic game albedo texture. "
                    f"Material: {desc}. Orthographic top-down surface filling all edges, patch covers {width} by {height} metres. "
                    "No perspective, objects, text, shadows or directional illumination; seamless on both axes. "
                    "Do not run commands, write or copy files. Reply in English with a short note.\n" + rules,
                    "expect_images": 1, "timeout": 900, "prompt_version": "library-v1", "meta": {
                        "kind": "library", "material_key": key, "preset": preset, "width_m": width, "height_m": height}})
    return out


def material_valid(directory: Path, key: str | None = None) -> bool:
    path = directory / "material.json"
    if not path.exists():
        return False
    rec = json.loads(path.read_text())
    if key is not None and rec.get("key") != key:
        return False
    if not (rec.get("qa", {}).get("pass_seam") and rec.get("qa", {}).get("pass_lowfreq") and rec.get("gltf_basisu_valid")):
        return False
    return bool(rec.get("hashes")) and all((directory / name).is_file() and sha(directory / name) == digest
                                          for name, digest in rec["hashes"].items())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("facades", type=Path)
    ap.add_argument("--run", type=Path, default=textures_data_dir("aoi/library-run"))
    ap.add_argument("--output", type=Path, default=textures_data_dir("library"))
    ap.add_argument("-j", type=int, default=6)
    ap.add_argument("--max-pct", type=float, default=100)
    a = ap.parse_args(argv)
    a.run.mkdir(parents=True, exist_ok=True)
    job_path = a.run / "jobs.jsonl"
    _write(build_jobs(required_keys(a.facades)), job_path)
    log_usage(a.run, "library_before")
    runner = Runner(a.run, parallel=a.j, max_pct=a.max_pct, cache_dir=textures_data_dir("codex-cache"))
    rc = runner.run(load_jobs(job_path))
    log_usage(a.run, "library_after")
    if rc:
        return rc
    if not deepbump_available():
        raise RuntimeError("Library production requires DeepBump")
    records = latest(a.run / "out.jsonl")
    for job in load_jobs(job_path):
        rec = records[job.id]
        if not rec.get("ok"):
            continue
        meta, source = rec["meta"], Path(rec["primary"])
        out = a.output / meta["material_key"]
        key = hashlib.sha256(json.dumps([job.key(), sha(source), POST_VERSION, post_code_hash(facade=False), "1024x1024"], sort_keys=True).encode()).hexdigest()
        if material_valid(out, key):
            continue
        post = process(source, out, tile="xy", flatten="xy", size="1024x1024", material=meta["preset"])
        if not (post["qa_after"]["pass_seam"] and post["qa_after"]["pass_lowfreq"]):
            raise RuntimeError(f"{meta['material_key']}: seam QA failed")
        write_json(out / "material.json", {"key": key, **meta, "uv_scale": [1 / meta["width_m"], 1 / meta["height_m"]],
                   "gltf_basisu_valid": all(v["gltf_basisu_valid"] for v in post["ktx2"].values()),
                   "qa": post["qa_after"], "normal_method": post["maps"], "thread_id": rec.get("thread_id"),
                   "imagegen_calls": len(rec.get("imagegen_calls", [])),
                   "hashes": {f"{m}.ktx2": sha(out / f"{m}.ktx2") for m in ("albedo", "normal", "orm")}})
        print(f"{meta['material_key']}: ready", flush=True)
    return 0
