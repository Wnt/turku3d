"""Dialogue and generated announcement lines -> normalized Ogg Opus clips + manifest.json.

Each voiced line has two keys:
- synth key: engine identity and seed + speaker preset + spoken text (after the pronunciation lexicon).
  Raw model output is cached under it.
- clip key: synth key + output settings. The manifest records it; an unchanged clip key with an intact
  file means the line is neither synthesized nor re-encoded.
The synthesizer is created only when at least one line misses both the manifest and the cache.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .content import load_voices, voiced_lines
from .encode import encode_opus
from .loudness import normalize, trim

# Bump when trimming/normalization/encoding changes in a way the settings do not capture.
PIPELINE_VERSION = 1


def digest(value):
    data = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    )
    return hashlib.sha256(data).hexdigest()


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def synth_key(voices, line):
    preset = voices.preset(line.speaker)
    return digest({"engine": voices.engine, "preset": preset, "text": voices.spoken(line.text)})


def clip_key(voices, line):
    return digest(
        {"synth": synth_key(voices, line), "output": voices.output, "pipeline": PIPELINE_VERSION}
    )


@dataclass
class Report:
    synthesized: list = field(default_factory=list)
    encoded: list = field(default_factory=list)
    reused: list = field(default_factory=list)
    removed: list = field(default_factory=list)


def _read_manifest(path):
    if not path.exists():
        return {}
    return {row["id"]: row for row in json.loads(path.read_text(encoding="utf-8"))["lines"]}


def _intact(out_dir, row, key):
    path = out_dir / row["file"]
    return (
        row.get("key") == key
        and path.exists()
        and digest(path.read_bytes()) == row.get("fileSha256")
    )


def _cached(cache_dir, key):
    path = cache_dir / f"{key}.npz"
    if not path.exists():
        return None
    with np.load(path) as data:
        return data["samples"], int(data["rate"])


def build(
    dialogue, voices_path, out_dir, cache_dir, synthesizer_factory, log=print, extra_lines=()
):
    """Write <out_dir>/<id>.ogg and manifest.json. synthesizer_factory(voices) returns synth(line, preset)."""
    voices = load_voices(voices_path)
    lines = [*voiced_lines(dialogue), *extra_lines]
    out_dir, cache_dir = Path(out_dir), Path(cache_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    previous = _read_manifest(manifest_path)
    report, rows, synth = Report(), [], None
    output = voices.output
    for line in lines:
        key = clip_key(voices, line)
        row = previous.get(line.id)
        if row and _intact(out_dir, row, key):
            rows.append(row)
            report.reused.append(line.id)
            continue
        skey = synth_key(voices, line)
        raw = _cached(cache_dir, skey)
        if raw is None:
            if synth is None:
                synth = synthesizer_factory(voices)
            log(f"synthesize {line.id} ({line.speaker})")
            samples, rate = synth(line, voices.preset(line.speaker))
            raw = np.asarray(samples, dtype=np.float32), int(rate)
            np.savez(cache_dir / f"{skey}.npz", samples=raw[0], rate=raw[1])
            report.synthesized.append(line.id)
        samples, rate = raw
        clip = normalize(trim(samples, rate), rate, output["lufs"], output["peak_dbfs"])
        path = out_dir / f"{line.id}.ogg"
        encode_opus(clip, rate, path, output["bitrate_kbps"])
        report.encoded.append(line.id)
        rows.append(
            {
                "id": line.id,
                "speaker": line.speaker,
                "file": path.name,
                "seconds": round(len(clip) / rate, 3),
                "text": line.text,
                "textSha256": text_sha256(line.text),
                "key": key,
                "fileSha256": digest(path.read_bytes()),
            }
        )
    keep = {row["file"] for row in rows}
    for stale in sorted(out_dir.glob("*.ogg")):
        if stale.name not in keep:
            stale.unlink()
            report.removed.append(stale.stem)
    manifest = {"version": 1, "engine": voices.engine, "lines": rows}
    text = json.dumps(manifest, indent=1, ensure_ascii=False) + "\n"
    if not manifest_path.exists() or manifest_path.read_text(encoding="utf-8") != text:
        manifest_path.write_text(text, encoding="utf-8")
    return report


def check(dialogue, voices_path, out_dir, extra_lines=()):
    """Problems that make the committed clips differ from what build would produce (no synthesis)."""
    voices = load_voices(voices_path)
    out_dir = Path(out_dir)
    previous = _read_manifest(out_dir / "manifest.json")
    problems, wanted = [], set()
    for line in [*voiced_lines(dialogue), *extra_lines]:
        wanted.add(line.id)
        row = previous.get(line.id)
        if not row:
            problems.append(f"{line.id}: no clip")
        elif not _intact(out_dir, row, clip_key(voices, line)):
            problems.append(f"{line.id}: clip is stale (text, preset or output settings changed)")
    problems += [f"{name}: clip has no dialogue line" for name in sorted(set(previous) - wanted)]
    files = {p.name for p in out_dir.glob("*.ogg")}
    problems += [
        f"{name}: file not in manifest"
        for name in sorted(files - {r["file"] for r in previous.values()})
    ]
    return problems
