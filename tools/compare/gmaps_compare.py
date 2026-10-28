#!/usr/bin/env python3
"""Capture one explicitly requested Google aerial reference beside the game, locally only."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.compare.pose import GooglePose, HeightReference, Pose, from_google, to_google  # noqa: E402


def local_game_url(value: str, base_port: int) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme != "http"
        or parts.hostname not in ("localhost", "127.0.0.1", "[::1]", "::1")
        or parts.username
        or parts.password
        or parts.port not in range(base_port, base_port + 10)
        or parts.port in (5171, 5175)
    ):
        raise ValueError("game URL must use this sandbox's localhost port range (never 5171/5175)")
    return value


def output_dir(work: Path, label: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", label):
        raise ValueError("label must contain 1..80 letters, numbers, underscores or hyphens")
    parent = (work / "qa/gmaps-compare").resolve()
    if parent.is_relative_to(ROOT):
        raise ValueError("comparison imagery must be outside the repository")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    out = parent / f"{stamp}-{label}"
    out.mkdir(parents=True, exist_ok=False)
    return out


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--cam", help="E,N,H,yaw,pitch,vertical_fov; H is N2000")
    source.add_argument("--google-url")
    source.add_argument("--at", help="camera E N; default H=80 N2000, yaw=135, pitch=-45, fov=35")
    p.add_argument("--label", default="view")
    p.add_argument("--url", help="running game/dist/draft service URL in this sandbox")
    p.add_argument(
        "--height-datum",
        choices=("ellipsoid", "ground", "n2000"),
        required=True,
        help="explicit, UNVERIFIED hypothesis for consumer URL a; see README",
    )
    p.add_argument(
        "--height-offset",
        type=float,
        required=True,
        help="ellipsoid: local geoid undulation; ground: ground H_N2000; n2000: 0",
    )
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=900)
    p.add_argument("--settle-seconds", type=float, default=8)
    p.add_argument("--pose-only", action="store_true", help="emit JSON only; no browser or server")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if not 320 <= args.width <= 3840 or not 240 <= args.height <= 2160:
        raise ValueError("viewport outside 320..3840 x 240..2160")
    if not 1 <= args.settle_seconds <= 60:
        raise ValueError("settle time must be 1..60 seconds")
    heights = HeightReference(args.height_datum, args.height_offset)
    if args.google_url:
        google = GooglePose.parse(args.google_url)
        pose = from_google(google, heights)
    else:
        if args.at:
            coords = args.at.split()
            if len(coords) != 2:
                raise ValueError('--at requires "E N"')
            pose = Pose(float(coords[0]), float(coords[1]), 80, 135, -45, 35)
        else:
            pose = Pose.parse(args.cam)
        google = to_google(pose, heights)
    base_port = int(os.environ["TURKU3D_PORT_BASE"])
    url = local_game_url(
        args.url or f"http://127.0.0.1:{base_port + 1}/?tiles=/tiles/tileset.json", base_port
    )
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update(cam=pose.cam(), hud="0")
    record = {
        "schema_version": 1,
        "python": sys.executable,
        "label": args.label,
        "game_pose": asdict(pose),
        "google_pose": asdict(google),
        "height_reference": asdict(heights),
        "calibration": "UNVERIFIED: consumer URL altitude datum and fov require landmark validation",
        "google_url": google.url(),
        "game_url": urlunsplit(parts._replace(query=urlencode(query))),
        "width": args.width,
        "height": args.height,
        "settle_seconds": args.settle_seconds,
        "imagery_policy": "LOCAL ONLY; Google Maps reference, never geometry or texture input",
    }
    if args.pose_only:
        print(json.dumps(record, indent=2))
        return 0
    work = Path(os.environ["TURKU3D_WORK"]).resolve()
    if os.environ.get("TURKU3D_SANDBOX") in (None, "main"):
        raise ValueError("source your isolated sandbox.env before capturing")
    out = output_dir(work, args.label)
    record["status"] = "pending"
    record_path = out / "comparison.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    (out / "views.jsonl").write_text(
        json.dumps(
            {
                "id": "game",
                "pose": asdict(pose),
                "width": args.width,
                "height": args.height,
            }
        )
        + "\n"
    )
    command = [
        sys.executable,
        str(ROOT / "scripts/dev/host_slots.py"),
        "browser",
        "--",
        "node",
        str(ROOT / "tools/compare/capture.mjs"),
        str(record_path),
    ]
    result = subprocess.run(command, check=False)
    record = json.loads(record_path.read_text())
    if result.returncode or record.get("status") != "captured-unverified":
        print(f"Comparison failed; diagnostics: {record_path}", file=sys.stderr)
        return result.returncode or 1
    # Preserve the complete Google frame, including the attribution/footer.
    with Image.open(out / "game.png") as game, Image.open(out / "google.png") as google_image:
        pair = Image.new("RGB", (args.width * 2, args.height + 38), "#202020")
        pair.paste(game.convert("RGB"), (0, 38))
        pair.paste(google_image.convert("RGB"), (args.width, 38))
        draw = ImageDraw.Draw(pair)
        draw.text((12, 12), "GAME - N2000 camera", fill="white")
        draw.text(
            (args.width + 12, 12), "GOOGLE MAPS - LOCAL REFERENCE - POSE UNVERIFIED", fill="white"
        )
        pair.save(out / "gmaps-compare.png")
    print(f"Local comparison (pose unverified): {out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc
