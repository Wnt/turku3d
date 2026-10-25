#!/usr/bin/env python3
"""Judge one (reference, render) image pair with Codex GPT-6-Astra.

  codex exec -m gpt-6-astra --sandbox read-only --output-schema qa/judge/qa.schema.json \
      --json -o last.json - -i 1_reference.jpg -i 2_render.png [-i 3_object_ids.png] < prompt

Quota guard: before every call the weekly Codex usage is read with judge/codex_usage.py (no model
call). If it is >= --cap (default $QA_CODEX_CAP or 100) or cannot be read, the call is refused
(exit code 3). The result records usage before/after, thread id, tokens and wall time.

Usage:
  qa/.venv/bin/python qa/judge/judge.py --ref data/qa/refs/office_front.jpg \
      --render data/qa/runs/X/renders/office_front.png --out /tmp/j.json
  ... --dry-run   # print the prompt and command, no Codex call
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import jsonschema

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "qa.schema.json"
PROMPT = HERE / "prompt.md"
USAGE = HERE / "codex_usage.py"
DEFAULT_MODEL = "gpt-6-astra"


class QuotaError(RuntimeError):
    pass


def usage_percent() -> int | None:
    try:
        r = subprocess.run(
            [sys.executable, str(USAGE), "--percent"], capture_output=True, text=True, timeout=60
        )
        return int(r.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None


def check_quota(cap: float) -> int:
    if not math.isfinite(cap) or not 0 < cap <= 100:
        raise ValueError("quota cap must be finite and in (0, 100]")
    pct = usage_percent()
    if pct is None:
        raise QuotaError(
            "could not read Codex usage; refusing to call (fix codex login or codex_usage.py)"
        )
    if pct >= cap:
        raise QuotaError(f"Codex weekly usage {pct}% >= cap {cap}%")
    return pct


def build_prompt(context: str = "", with_id: bool = False) -> str:
    p = PROMPT.read_text()
    idn = (
        "3. **Image 3 = OBJECT-ID PASS** of the render: flat colours, one colour per game object "
        "(building, tile, prop). Use it only to understand object boundaries; do not judge its colours."
        if with_id
        else ""
    )
    ctx = f"Context for this view: {context.strip()}" if context.strip() else ""
    return p.replace("{{ID_IMAGE_NOTE}}", idn).replace("{{CONTEXT}}", ctx)


def _clean(result: dict) -> dict:
    for it in result.get("issues", []):
        b = [min(1.0, max(0.0, float(v))) for v in it["bbox"]]
        x0, x1 = sorted((b[0], b[2]))
        y0, y1 = sorted((b[1], b[3]))
        it["bbox"] = [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]
    result["issues"] = sorted(result.get("issues", []), key=lambda i: -i["severity"])
    return result


def judge(
    ref,
    render,
    id_image=None,
    context="",
    model=DEFAULT_MODEL,
    cap=None,
    timeout=900,
    dry_run=False,
    retries=1,
) -> dict:
    cap = float(cap if cap is not None else os.environ.get("QA_CODEX_CAP", 100))
    prompt = build_prompt(context, id_image is not None)
    with tempfile.TemporaryDirectory(prefix="qa-judge-") as directory:
        return _judge_in_dir(
            Path(directory),
            ref,
            render,
            id_image,
            context,
            model,
            cap,
            timeout,
            dry_run,
            retries,
            prompt,
        )


def _judge_in_dir(
    tmp, ref, render, id_image, context, model, cap, timeout, dry_run, retries, prompt
):
    imgs = [("1_reference", ref), ("2_render", render)] + (
        [("3_object_ids", id_image)] if id_image else []
    )
    args = []
    for name, src in imgs:
        dst = tmp / f"{name}{Path(src).suffix.lower()}"
        shutil.copy(src, dst)
        args += ["-i", str(dst)]
    last = tmp / "last.json"
    cmd = [
        "codex",
        "exec",
        "-m",
        model,
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "-C",
        str(tmp),
        "--output-schema",
        str(SCHEMA),
        "--json",
        "-o",
        str(last),
        "-",
    ] + args
    if dry_run:
        return {"dry_run": True, "cmd": cmd, "prompt": prompt}
    schema = json.loads(SCHEMA.read_text())
    before = check_quota(cap)
    attempts = []
    result = None
    t0 = time.time()
    for attempt in range(retries + 1):
        if attempt:
            check_quota(cap)
        ta = time.time()
        last.unlink(missing_ok=True)
        try:
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            attempts.append(
                {"rc": -1, "error": "judge timeout", "secs": round(time.time() - ta, 1)}
            )
            continue
        thread_id, tokens = None, None
        for line in p.stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "thread.started":
                thread_id = ev.get("thread_id")
            elif ev.get("type") == "turn.completed":
                tokens = ev.get("usage")
        rec = {
            "rc": p.returncode,
            "thread_id": thread_id,
            "tokens": tokens,
            "secs": round(time.time() - ta, 1),
        }
        try:
            if p.returncode:
                raise ValueError(f"codex exited {p.returncode}")
            cand = json.loads(last.read_text())
            jsonschema.validate(cand, schema)
            result = _clean(cand)
            attempts.append(rec)
            break
        except (OSError, ValueError, jsonschema.ValidationError) as e:
            rec["error"] = f"{type(e).__name__}: {str(e)[:300]}"
            rec["stderr_tail"] = p.stderr[-800:]
            attempts.append(rec)
    after = usage_percent()
    return {
        "model": model,
        "ref": str(ref),
        "render": str(render),
        "id_image": str(id_image) if id_image else None,
        "context": context,
        "result": result,
        "ok": result is not None,
        "attempts": attempts,
        "secs": round(time.time() - t0, 1),
        "usage_before_pct": before,
        "usage_after_pct": after,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--ref", required=True)
    ap.add_argument("--render", required=True)
    ap.add_argument("--id-image")
    ap.add_argument("--context", default="")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument(
        "--cap", type=float, default=None, help="refuse at >= this weekly usage %% (default 100)"
    )
    ap.add_argument("--out")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        r = judge(a.ref, a.render, a.id_image, a.context, a.model, a.cap, dry_run=a.dry_run)
    except QuotaError as e:
        print(f"QUOTA: {e}", file=sys.stderr)
        return 3
    s = json.dumps(r, indent=1, ensure_ascii=False)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(s)
    print(s)
    return 0 if (r.get("ok") or a.dry_run) else 1


if __name__ == "__main__":
    sys.exit(main())
