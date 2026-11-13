"""The readers' model call over the Messages API, as scripts/dev/scene-judge.mjs judges with TURKU3D_JUDGE_TRANSPORT=api.

TURKU3D_READER_TRANSPORT=api sends observe/reader.py's and observe/light.py's call to the Messages API instead of the
`claude` CLI. Only the transport differs: the same instructions as the system prompt, the same task text, the same
JSON schema and the same parsing of the answer. The CLI run reads the files the task names with its Read tool; here
every such file is in the user turn, each after its file name.

The schema is given the way the CLI's --json-schema gives it: as the input schema of an answer tool, checked here
with jsonschema, and a failed check is sent back as the tool's error for the model to correct (at most twice). The
API's constrained structured output cannot take these schemas (it allows 24 optional fields; the light reader's
has 54, the building reader's several hundred).

TURKU3D_READER_SDK names a directory holding the `anthropic` Python package (kept outside Git, `pip install
--target`). The zero-argument client takes the active `ant auth` profile, so ANTHROPIC_API_KEY and
ANTHROPIC_AUTH_TOKEN are cleared in this process. No server-side fallback: the reading comes from the model asked or
not at all; a refusal (or an answer cut at max_tokens) is asked once more from the start, then raised. The cost is
computed from the responses' usage and every call is appended to observe/api-usage.jsonl under the work root.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import jsonschema

from turku3d import config

# USD per million tokens: input, output. Cache writes cost 1.25 and cache reads 0.1 times the input price.
PRICES = {"claude-fable-5-2": (8.0, 40.0), "claude-opus-5-5": (4.0, 20.0)}
MAX_TOKENS = 64000
TIMEOUT_S = 20 * 60
CORRECTIONS = 2  # answers that fail the schema check sent back for correction, per attempt
MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
TOOL = "answer"
ANSWER = (f"Give your final answer by calling the `{TOOL}` tool once, with the whole answer as its input. "
          "Its input schema is the required answer format.")


def wanted() -> bool:
    return os.environ.get("TURKU3D_READER_TRANSPORT") == "api"


def _client():
    sdk = os.environ.get("TURKU3D_READER_SDK")
    if sdk and sdk not in sys.path:
        sys.path.insert(0, sdk)
    import anthropic

    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    return anthropic.Anthropic(max_retries=4, timeout=TIMEOUT_S)


def content(files: list[Path], task: str) -> list[dict]:
    """The user turn: the files the task names, each after its file name, then the task itself."""
    blocks = [{"type": "text", "text": "The files the task names follow, each after its file name."}]
    for path in files:
        media = MEDIA.get(path.suffix.lower())
        if media:
            blocks += [{"type": "text", "text": f"=== {path} ==="},
                       {"type": "image", "source": {"type": "base64", "media_type": media,
                                                    "data": base64.b64encode(path.read_bytes()).decode()}}]
        else:
            blocks.append({"type": "text", "text": f"=== {path} ===\n{path.read_text()}"})
    blocks.append({"type": "text", "text": task})
    return blocks


def cost(model: str, usage: dict) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-fable-5-2"])
    return round((usage["input_tokens"] * pin + usage["cache_creation_input_tokens"] * pin * 1.25
                  + usage["cache_read_input_tokens"] * pin * 0.1 + usage["output_tokens"] * pout) / 1e6, 4)


def problems(schema: dict, answer) -> list[str]:
    """The schema check of an answer, worst first, as short lines."""
    check = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message[:300]}"
            for e in sorted(check.iter_errors(answer), key=lambda e: len(e.absolute_path))][:20]


def ask(system: str, files: list[Path], task: str, schema: dict, *, model: str, effort: str, what: str) -> tuple[dict, dict]:
    """(answer, meta) with the answer checked against `schema`; raises when the model gives no valid answer."""
    client = _client()
    tool = {"name": TOOL, "description": ANSWER, "input_schema": schema}
    first = content(files, task) + [{"type": "text", "text": ANSWER}]
    usage = dict.fromkeys(("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"), 0)
    started, served, turns, why = time.time(), model, 0, ""
    for attempt in (1, 2):
        turn = [{"role": "user", "content": first}]
        for _ in range(1 + CORRECTIONS):
            turns += 1
            with client.messages.stream(model=model, max_tokens=MAX_TOKENS, system=system, messages=turn,
                                        tools=[tool], tool_choice={"type": "auto"},
                                        output_config={"effort": effort}) as stream:
                message = stream.get_final_message()
            for k in usage:
                usage[k] += getattr(message.usage, k, None) or 0
            served = message.model
            if message.stop_reason in ("refusal", "max_tokens"):
                details = getattr(message, "stop_details", None)
                why = f"stop {message.stop_reason}" + (f" {details.model_dump() if hasattr(details, 'model_dump') else details}"
                                                       if details else "")
                break  # asked once more from the start
            calls = [b for b in message.content if b.type == "tool_use" and b.name == TOOL]
            turn.append({"role": "assistant", "content": message.content})
            if not calls:
                why = "no answer tool call"
                turn.append({"role": "user", "content": ANSWER})
                continue
            wrong = problems(schema, calls[0].input)
            if not wrong:
                return calls[0].input, _record(what, served, effort, usage, attempt, turns, time.time() - started, "ok")
            why = f"schema: {wrong[0]}"
            turn.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": c.id, "is_error": True,
                 "content": ("The answer does not match the schema:\n" + "\n".join(wrong) + "\nCall the tool again "
                             "with the whole corrected answer.") if c is calls[0] else "Only one answer is read."}
                for c in calls]})
    meta = _record(what, served, effort, usage, 2, turns, time.time() - started, why)
    raise RuntimeError(f"api: no valid answer ({why}); ${meta['cost_usd']} spent")


def _record(what, model, effort, usage, attempts, turns, seconds, outcome) -> dict:
    meta = {"model": model, "effort": effort, "transport": "api", "seconds": round(seconds, 1),
            "cost_usd": cost(model, usage), "attempts": attempts, "turns": turns, "outcome": outcome, **usage,
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    log = config.work_root() / "observe/api-usage.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as handle:
        handle.write(json.dumps({"what": what, **meta}, ensure_ascii=False) + "\n")
    return meta
