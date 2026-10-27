"""python -m voicegen build|check [--dialogue P] [--voices P] [--out DIR] [--cache DIR]"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .announcements import extra_lines, published_addresses
from .build import build, check

REPO = Path(__file__).resolve().parents[3]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="voicegen")
    parser.add_argument("command", choices=["build", "check", "fetch"])
    parser.add_argument(
        "--dialogue", type=Path, default=REPO / "game/src/gameplay/content/dialogue.json"
    )
    parser.add_argument("--voices", type=Path, default=REPO / "audio/voice/voices.toml")
    parser.add_argument("--out", type=Path, default=REPO / "game/public/assets/voice")
    work = Path(os.environ.get("TURKU3D_WORK", Path.home() / "turku3d/data"))
    parser.add_argument("--cache", type=Path, default=work / "cache/voice")
    args = parser.parse_args(argv)
    addresses = published_addresses() if args.command == "build" else None
    extras = (
        extra_lines(REPO, addresses)
        if args.command != "fetch"
        and args.dialogue == REPO / "game/src/gameplay/content/dialogue.json"
        else ()
    )
    if args.command == "check":
        problems = check(args.dialogue, args.voices, args.out, extras)
        for problem in problems:
            print(problem)
        if problems:
            print("run `make voice-build` to regenerate the clips")
        return 1 if problems else 0
    # The pinned weights live outside the repo (make voice-setup); the make targets pass the same default.
    os.environ.setdefault("HF_HOME", str(Path.home() / "turku3d/tools/voice/hf"))
    from .engine import fetch, synthesizer

    if args.command == "fetch":
        print(fetch(args.voices))
        return 0
    start = time.perf_counter()
    report = build(
        args.dialogue, args.voices, args.out, args.cache, synthesizer, extra_lines=extras
    )
    if addresses is not None and args.dialogue == REPO / "game/src/gameplay/content/dialogue.json":
        (REPO / "audio/voice/addresses.json").write_text(
            json.dumps(addresses, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(
        f"voice: {len(report.synthesized)} synthesized, {len(report.encoded)} encoded, {len(report.reused)} reused, "
        f"{len(report.removed)} removed in {time.perf_counter() - start:.1f} s -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
