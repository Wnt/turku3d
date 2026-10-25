#!/usr/bin/env python3
"""Fake `codex` CLI for runner tests. Mimics codex-cli 0.160 `exec --json` closely enough:
thread.started on stdout, a rollout under $CODEX_HOME/sessions/YYYY/MM/DD/, PNGs under
$CODEX_HOME/generated_images/<thread_id>/, the image_gen call as a JS `exec` custom_tool_call, the
"Generated images are saved to ..." output hint, token_count with rate_limits, -o last message.

Prompt keywords: GENERATE (1 image), TWICE (2 images), FAIL (exit 1), NOJSON (non-JSON answer).
Every invocation is appended to $FAKE_CODEX_LOG.
"""

import json
import os
import struct
import sys
import uuid
import zlib
from pathlib import Path


def png(path: Path, w=8, h=4, rgb=(200, 100, 50)):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def main():
    a = sys.argv[1:]
    if a[:1] == ["app-server"]:
        # minimal JSON-RPC for usage.rate_limits()
        for line in sys.stdin:
            m = json.loads(line)
            if m.get("method") == "account/rateLimits/read":
                pct = int(os.environ.get("FAKE_USAGE", "5"))
                print(
                    json.dumps(
                        {
                            "id": m["id"],
                            "result": {
                                "rateLimits": {
                                    "primary": {
                                        "usedPercent": pct,
                                        "windowDurationMins": 10080,
                                        "resetsAt": 0,
                                    },
                                    "planType": "pro",
                                }
                            },
                        }
                    ),
                    flush=True,
                )
            elif "id" in m:
                print(json.dumps({"id": m["id"], "result": {}}), flush=True)
        return 0
    assert a[0] == "exec", a
    prompt = sys.stdin.read()
    home = Path(os.environ["CODEX_HOME"])
    tid = str(uuid.uuid4())
    out_last = a[a.index("-o") + 1] if "-o" in a else None
    schema = a[a.index("--output-schema") + 1] if "--output-schema" in a else None
    imgs = [a[i + 1] for i, x in enumerate(a) if x == "-i"]
    cwd = a[a.index("-C") + 1]
    if os.environ.get("FAKE_CODEX_LOG"):
        with open(os.environ["FAKE_CODEX_LOG"], "a") as f:
            f.write(json.dumps({"tid": tid, "args": a, "prompt": prompt[:200]}) + "\n")
    print(json.dumps({"type": "thread.started", "thread_id": tid}), flush=True)
    if "FAIL" in prompt:
        print(json.dumps({"type": "error", "message": "fake failure"}), flush=True)
        return 1
    ses = home / "sessions" / "2026" / "10" / "25"
    ses.mkdir(parents=True, exist_ok=True)
    gen = home / "generated_images" / tid
    rows = [
        {"type": "session_meta", "payload": {"id": tid, "cwd": cwd}},
        {"type": "turn_context", "payload": {"model": a[a.index("-m") + 1]}},
    ]
    n = 2 if "TWICE" in prompt else (1 if "GENERATE" in prompt else 0)
    for k in range(n):
        gen.mkdir(parents=True, exist_ok=True)
        f = gen / f"exec-{uuid.uuid4()}.png"
        png(f, rgb=(10 * k, 100, 50))
        os.utime(f, (1000 + k, 1000 + k))
        js = (
            '// @exec: {"yield_time_ms": 120000}\nconst r = await tools.image_gen__imagegen({referenced_image_paths:'
            + json.dumps(imgs)
            + ",prompt:"
            + json.dumps(f"fake prompt {k}: {prompt[:40]}")
            + "});"
        )
        rows.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": f"c{k}",
                    "name": "exec",
                    "input": js,
                },
            }
        )
        if k == n - 1 or "TWICE" not in prompt:
            rows.append(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call_output",
                        "call_id": f"c{k}",
                        "output": [
                            {"type": "input_text", "text": "Script completed"},
                            {
                                "type": "input_text",
                                "text": f"Generated images are saved to {gen} as {f} by default.",
                            },
                        ],
                    },
                }
            )
    ans = {"facade_id": "x", "bays": 5} if schema else {"notes": "ok"}
    msg = "this is not json" if "NOJSON" in prompt else json.dumps(ans)
    rows.append(
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {"input_tokens": 1000, "output_tokens": 10}},
                "rate_limits": {"primary": {"used_percent": 1.5}},
            },
        }
    )
    rows.append(
        {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": msg}}
    )
    (ses / f"rollout-2026-10-25T00-00-00-{tid}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows)
    )
    if out_last:
        Path(out_last).write_text(msg)
    print(
        json.dumps(
            {"type": "turn.completed", "usage": {"input_tokens": 1000, "output_tokens": 10}}
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
