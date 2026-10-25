"""Parse Codex session rollouts and collect generated images by thread_id.

Facts (docs/research/ai-textures.md, Verification 1b/1c, re-checked on CLI 0.160):
- `codex exec --json` prints `{"type":"thread.started","thread_id":...}` first; image_gen calls do NOT
  appear in that stream.
- The rollout `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<thread_id>.jsonl` holds every call. In 0.160
  image_gen is invoked from the JS `exec` tool: a `custom_tool_call` whose `input` contains
  `tools.image_gen__imagegen({... prompt:"..." ...})`. Its `custom_tool_call_output` contains the
  hint "Generated images are saved to <dir> as <dir>/exec-<uuid>.png". Older builds logged a direct
  `custom_tool_call` named image_gen; both forms are handled.
- Files land in `~/.codex/generated_images/<thread_id>/exec-<uuid>.png`; one call can produce more than
  one PNG (hidden retries/variants), so we return all of them, ordered, and mark the ones referenced
  by an output hint as `referenced`.
- `event_msg` / `token_count` events carry token totals and `rate_limits.primary.used_percent` (float).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def find_rollout(thread_id: str, home: Path | None = None) -> Path | None:
    home = home or codex_home()
    hits = sorted((home / "sessions").glob(f"*/*/*/rollout-*-{thread_id}.jsonl"))
    return hits[-1] if hits else None


def generated_dir(thread_id: str, home: Path | None = None) -> Path:
    return (home or codex_home()) / "generated_images" / thread_id


_SAVED_RE = re.compile(r"saved to \S+ as (\S+?\.png)")
_PROMPT_RE = re.compile(
    r"""prompt\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)""", re.S
)
_REFS_RE = re.compile(r"referenced_image_paths\s*:\s*\[([^\]]*)\]", re.S)


def _js_string(lit: str) -> str:
    q = lit[0]
    body = lit[1:-1]
    if q == '"':
        try:
            return json.loads(lit)
        except ValueError:
            pass
    return body.encode("utf-8").decode("unicode_escape", errors="replace") if "\\" in body else body


@dataclass
class ImageGenCall:
    call_id: str
    prompt: str | None
    referenced_images: list[str]
    outputs: list[str] = field(default_factory=list)  # paths from the output hint
    timestamp: str | None = None


@dataclass
class Rollout:
    path: Path
    thread_id: str
    calls: list[ImageGenCall]
    tokens: dict | None
    used_percent: float | None
    last_message: str | None
    model: str | None


def parse_rollout(path: Path) -> Rollout:
    calls: dict[str, ImageGenCall] = {}
    order: list[str] = []
    tokens = None
    used = None
    last_msg = None
    model = None
    thread_id = ""
    for line in open(path, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        p = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        t = r.get("type")
        if t == "session_meta":
            thread_id = p.get("id") or p.get("session_id") or thread_id
        elif t == "turn_context":
            model = p.get("model") or model
        pt = p.get("type")
        if pt in ("custom_tool_call", "function_call"):
            name = p.get("name", "")
            inp = p.get("input") if pt == "custom_tool_call" else p.get("arguments")
            inp = inp if isinstance(inp, str) else json.dumps(inp or "")
            if name.startswith("image_gen") or name == "imagegen" or "image_gen__imagegen(" in inp:
                prompt = None
                refs: list[str] = []
                if name.startswith("image_gen") or name == "imagegen":
                    try:
                        a = json.loads(inp)
                        prompt, refs = a.get("prompt"), a.get("referenced_image_paths") or []
                    except ValueError:
                        pass
                if prompt is None:
                    m = _PROMPT_RE.search(inp)
                    prompt = _js_string(m.group(1)) if m else None
                    m2 = _REFS_RE.search(inp)
                    if m2:
                        refs = [
                            _js_string(s)
                            for s in re.findall(r"""("(?:[^"\\]|\\.)*"|'[^']*')""", m2.group(1))
                        ]
                cid = p.get("call_id") or str(len(order))
                calls[cid] = ImageGenCall(cid, prompt, refs, timestamp=r.get("timestamp"))
                order.append(cid)
        elif pt in ("custom_tool_call_output", "function_call_output"):
            cid = p.get("call_id")
            if cid in calls:
                out = p.get("output")
                texts = []
                if isinstance(out, list):
                    texts = [o.get("text", "") for o in out if isinstance(o, dict)]
                elif isinstance(out, str):
                    texts = [out]
                for tx in texts:
                    for f in _SAVED_RE.findall(tx):
                        if f not in calls[cid].outputs:
                            calls[cid].outputs.append(f)
        elif t == "event_msg" and pt == "token_count":
            info = p.get("info") or {}
            tokens = info.get("total_token_usage") or tokens
            rl = (p.get("rate_limits") or {}).get("primary") or {}
            if rl.get("used_percent") is not None:
                used = float(rl["used_percent"])
        elif t == "event_msg" and pt == "task_complete":
            last_msg = p.get("last_agent_message") or last_msg
    if not thread_id:
        m = re.search(
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", path.name
        )
        thread_id = m.group(1) if m else ""
    return Rollout(path, thread_id, [calls[c] for c in order], tokens, used, last_msg, model)


def collect_images(
    thread_id: str, home: Path | None = None, rollout: Rollout | None = None
) -> list[dict]:
    """All PNGs for a thread, oldest first: [{path, bytes, mtime, referenced, call_id, prompt}]."""
    d = generated_dir(thread_id, home)
    if not d.is_dir():
        return []
    ref = {}
    if rollout:
        for c in rollout.calls:
            for o in c.outputs:
                ref[Path(o).name] = c
    files = sorted(d.glob("*.png"), key=lambda f: (f.stat().st_mtime, f.name))
    res = []
    for f in files:
        c = ref.get(f.name)
        res.append(
            {
                "path": str(f),
                "bytes": f.stat().st_size,
                "mtime": f.stat().st_mtime,
                "referenced": c is not None,
                "call_id": c.call_id if c else None,
                "prompt": c.prompt if c else None,
            }
        )
    return res


def pick_primary(images: list[dict]) -> dict | None:
    """The deliverable when a call made several PNGs: the newest referenced one, else the newest."""
    if not images:
        return None
    refd = [i for i in images if i["referenced"]]
    return (refd or images)[-1]
