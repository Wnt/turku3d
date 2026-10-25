"""Parallel, isolated, quota-guarded, resumable `codex exec` batch runner.

jobs.jsonl (one JSON object per line):
  {"id": "lr15_se_label",                  required, unique, [A-Za-z0-9_.-]
   "prompt": "..." | "prompt_file": "p.md", the prompt (stdin). prompt_file is relative to jobs.jsonl
   "vars": {"k": "v"},                      optional $k substitutions in the prompt (string.Template)
   "images": ["a.jpg", ...],                attached with -i (copied into the job's temp dir first)
   "files": ["b.json", ...],                copied into the temp dir, not attached
   "schema": "schemas/x.schema.json",       optional --output-schema (strict JSON answer)
   "model": "gpt-6-astra", "sandbox": "read-only", "timeout": 900,
   "expect_images": 0|1|n,                  >0: fail if the thread produced no PNG
   "prompt_version": "v1", "meta": {...}}   meta is copied to the result
Relative paths resolve against the jobs file's directory.

Output dir:
  out.jsonl     one record per finished job (append-only; the last record per id wins)
  failed.json   failures of the last pass ([{id, error, ...}])
  runner.log    human log; usage.jsonl: quota readings before/after every call
  logs/<id>.events.jsonl / .err / .last.txt     raw codex --json stream, stderr, last message
  images/<id>/<n>.png                            collected PNGs (all of them, primary flagged)

Cache: content hash sha256(model, sandbox, prompt text, schema text, input file hashes, prompt_version,
CACHE_VERSION) -> <cache>/<key[:2]>/<key>/record.json + images. A cache hit costs no Codex call.

Quota: before EVERY call the weekly usedPercent is read (usage.py, free). If it is >= max_pct (default
100) or unknown, no new call starts; running ones finish; exit code 3. Rerunning the same command
resumes: jobs with an ok record (same key) in out.jsonl are skipped.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import ValidationError, validate
from PIL import Image

from . import accounting, routing, usage
from . import rollout as ro

CACHE_VERSION = "1"
DEFAULT_MODEL = "gpt-6-astra"
EXIT_QUOTA = 3
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


@dataclass
class Job:
    id: str
    prompt: str
    images: list[Path] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    schema: Path | None = None
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    sandbox: str = "read-only"
    timeout: float = 900
    expect_images: int = 0
    prompt_version: str = "v1"
    meta: dict = field(default_factory=dict)

    def key(self) -> str:
        h = hashlib.sha256()
        parts = [
            CACHE_VERSION,
            self.model,
            self.effort,
            self.sandbox,
            self.prompt_version,
            self.prompt,
            self.schema.read_text() if self.schema else "",
            str(self.expect_images),
        ]
        for p in parts:
            h.update(p.encode())
            h.update(b"\0")
        for p in self.images + self.files:
            h.update(p.name.encode() + b"=" + sha256_file(p).encode() + b"\0")
        return h.hexdigest()


def load_jobs(path: Path) -> list[Job]:
    base = path.parent
    jobs, seen = [], set()
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        d = json.loads(line)
        jid = d["id"]
        if not _ID_RE.match(jid) or jid in seen:
            raise ValueError(f"{path}:{n}: bad or duplicate id {jid!r}")
        seen.add(jid)
        if "prompt" in d:
            text = d["prompt"]
        else:
            text = (base / d["prompt_file"]).read_text(encoding="utf-8")
        if d.get("vars"):
            text = string.Template(text).safe_substitute({k: str(v) for k, v in d["vars"].items()})
        rp = lambda s: (base / s).resolve()  # noqa: E731
        model, effort = routing.route(
            d.get("meta", {}).get("kind", "default"), d.get("model"), d.get("effort")
        )
        j = Job(
            id=jid,
            prompt=text,
            images=[rp(s) for s in d.get("images", [])],
            files=[rp(s) for s in d.get("files", [])],
            schema=rp(d["schema"]) if d.get("schema") else None,
            model=model,
            effort=effort,
            sandbox=d.get("sandbox", "read-only"),
            timeout=float(d.get("timeout", 900)),
            expect_images=int(d.get("expect_images", 0)),
            prompt_version=str(d.get("prompt_version", "v1")),
            meta=d.get("meta", {}),
        )
        names = [p.name for p in j.images + j.files]
        if len(set(names)) != len(names):
            raise ValueError(
                f"{path}:{n}: input file names must be unique within a job (copied flat)"
            )
        for p in j.images + j.files + ([j.schema] if j.schema else []):
            if not p.is_file():
                raise FileNotFoundError(f"{path}:{n}: {p}")
        jobs.append(j)
    return jobs


def parse_json_answer(text: str | None):
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    for cand in sorted(re.findall(r"[\[{].*[\]}]", text, re.S), key=len, reverse=True):
        try:
            return json.loads(cand)
        except ValueError:
            continue
    return None


class QuotaStop(Exception):
    pass


class Runner:
    def __init__(
        self,
        out_dir: Path,
        parallel: int = 4,
        max_pct: float = 100,
        cache_dir: Path | None = None,
        codex_bin: str | None = None,
        retries: int = 1,
        allow_unknown_usage: bool = False,
        extra_args: list[str] | None = None,
        use_cache: bool = True,
    ):
        self.out = Path(out_dir)
        self.parallel = max(1, parallel)
        self.max_pct = max_pct
        self.cache = Path(cache_dir) if cache_dir else None
        self.codex = codex_bin or os.environ.get("TURKUTEX_CODEX_BIN", "codex")
        self.retries = retries
        self.allow_unknown = allow_unknown_usage
        self.extra = extra_args if extra_args is not None else ["-c", "notify=[]"]
        self.use_cache = use_cache
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.stop_reason = None
        for d in ("logs", "images"):
            (self.out / d).mkdir(parents=True, exist_ok=True)

    # ---------- bookkeeping ----------
    def log(self, msg: str):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        with self.lock:
            with open(self.out / "runner.log", "a") as f:
                f.write(line + "\n")
        print(line, file=sys.stderr, flush=True)

    def _append(self, name: str, rec: dict):
        with self.lock:
            with open(self.out / name, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def done_records(self) -> dict[str, dict]:
        res = {}
        p = self.out / "out.jsonl"
        if p.exists():
            for line in open(p):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                res[r["id"]] = r
        return res

    def check_quota(self, job_id: str) -> int | None:
        with self.lock:
            if self.stop.is_set():
                raise QuotaStop(self.stop_reason)
            pct = usage.used_percent()
            if pct is None and not self.allow_unknown:
                self.stop_reason = "weekly usage unknown (codex app-server gave no rate limits)"
            elif pct is not None and pct >= self.max_pct:
                self.stop_reason = f"weekly usage {pct}% >= cap {self.max_pct}%"
            if self.stop_reason:
                self.stop.set()
                raise QuotaStop(self.stop_reason)
            return pct

    # ---------- cache ----------
    def _cache_path(self, key: str) -> Path | None:
        return self.cache / key[:2] / key if self.cache else None

    def _cache_get(self, job: Job, key: str) -> dict | None:
        cp = self._cache_path(key)
        if not (self.use_cache and cp and (cp / "record.json").exists()):
            return None
        rec = json.loads((cp / "record.json").read_text())
        if not self._valid_record(job, rec, cp):
            return None
        dst = self.out / "images" / job.id
        if dst.exists():
            shutil.rmtree(dst)
        imgs = []
        for im in rec.get("images", []):
            src = cp / Path(im["path"]).name
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst / src.name)
            imgs.append({**im, "path": str(dst / src.name)})
        rec.update(
            images=imgs,
            id=job.id,
            cached=True,
            meta=job.meta,
            primary=next((i["path"] for i in imgs if i.get("primary")), None),
        )
        return rec

    @staticmethod
    def _valid_record(job: Job, rec: dict, cache_path: Path | None = None) -> bool:
        if not rec.get("ok"):
            return False
        if job.expect_images and not rec.get("images"):
            return False
        for im in rec.get("images", []):
            path = cache_path / Path(im["path"]).name if cache_path else Path(im["path"])
            if not path.is_file() or sha256_file(path) != im.get("sha256"):
                return False
        if job.schema:
            try:
                validate(rec.get("answer"), json.loads(job.schema.read_text()))
            except ValidationError:
                return False
        return True

    def _cache_put(self, key: str, rec: dict):
        cp = self._cache_path(key)
        if not cp:
            return
        cp.mkdir(parents=True, exist_ok=True)
        for im in rec.get("images", []):
            shutil.copy2(im["path"], cp / Path(im["path"]).name)
        (cp / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))

    # ---------- one call ----------
    def _call(self, job: Job, key: str, attempt: int) -> dict:
        pct_before = self.check_quota(job.id)
        logs = self.out / "logs"
        tmp = Path(tempfile.mkdtemp(prefix=f"turkutex-{job.id[:40]}-"))
        try:
            for p in job.images + job.files:
                shutil.copy2(p, tmp / p.name)
            last = logs / f"{job.id}.last.txt"
            last.unlink(missing_ok=True)
            cmd = [
                self.codex,
                "exec",
                "-m",
                job.model,
                "-c",
                f'model_reasoning_effort="{job.effort}"',
                "-C",
                str(tmp),
                "--skip-git-repo-check",
                "--sandbox",
                job.sandbox,
                "--json",
                "-o",
                str(last),
                *self.extra,
            ]
            if job.schema:
                cmd += ["--output-schema", str(job.schema)]
            cmd.append("-")
            for p in job.images:
                cmd += ["-i", str(tmp / p.name)]
            t0 = time.time()
            accounting_id = str(uuid.uuid4())
            self.log(f"START {job.id} attempt={attempt} usage={pct_before}% key={key[:12]}")
            with (
                open(logs / f"{job.id}.events.jsonl", "w") as ev,
                open(logs / f"{job.id}.err", "w") as er,
            ):
                try:
                    pr = subprocess.run(
                        cmd, input=job.prompt, stdout=ev, stderr=er, text=True, timeout=job.timeout
                    )
                    rc = pr.returncode
                except subprocess.TimeoutExpired:
                    rc = -9
                except OSError:
                    rc = 127
            secs = round(time.time() - t0, 1)
            accounting.record_call(
                source=logs / f"{job.id}.events.jsonl",
                model=job.model,
                fallback_id=accounting_id,
                effort=job.effort,
                job_type=job.meta.get("kind", "texture"),
                job_id=job.id,
                events=(logs / f"{job.id}.events.jsonl").read_text(),
                seconds=secs,
                started=t0,
                ok=rc == 0,
                units={
                    "facade": len(job.meta.get("facade_ids", []))
                    or int(bool(job.meta.get("facade_id"))),
                    "view": len(job.meta.get("views", job.meta.get("views_used", []))),
                },
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        thread_id, usage_ev, err_ev = None, None, None
        for line in open(logs / f"{job.id}.events.jsonl"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "thread.started":
                thread_id = e.get("thread_id")
            elif e.get("type") == "turn.completed":
                turn_usage = e.get("usage") or {}
                usage_ev = usage_ev or {}
                for field, value in turn_usage.items():
                    if isinstance(value, (int, float)):
                        usage_ev[field] = usage_ev.get(field, 0) + value
            elif e.get("type") in ("error", "turn.failed"):
                err_ev = e.get("message") or json.dumps(e.get("error"))
        last_msg = last.read_text() if last.exists() else None
        rec = {
            "id": job.id,
            "key": key,
            "ok": False,
            "cached": False,
            "rc": rc,
            "seconds": secs,
            "attempt": attempt,
            "model": job.model,
            "effort": job.effort,
            "thread_id": thread_id,
            "accounting_id": accounting_id,
            "meta": job.meta,
            "prompt_version": job.prompt_version,
            "prompt_sha256": hashlib.sha256(job.prompt.encode()).hexdigest(),
            "inputs": [{"name": p.name, "sha256": sha256_file(p)} for p in job.images + job.files],
            "usage_before": pct_before,
            "tokens": usage_ev,
            "last_message": last_msg,
            "answer": None,
            "images": [],
            "primary": None,
            "imagegen_calls": [],
            "rollout": None,
            "error": None,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        rollout = None
        if thread_id:
            rp = ro.find_rollout(thread_id)
            if rp:
                rollout = ro.parse_rollout(rp)
                rec["rollout"] = str(rp)
                rec["usage_after_rollout"] = rollout.used_percent
                rec["imagegen_calls"] = [
                    {
                        "call_id": c.call_id,
                        "prompt": c.prompt,
                        "referenced_images": c.referenced_images,
                        "outputs": c.outputs,
                    }
                    for c in rollout.calls
                ]
                if rollout.tokens:
                    rec["rollout_tokens"] = rollout.tokens
            imgs = ro.collect_images(thread_id, rollout=rollout)
            prim = ro.pick_primary(imgs)
            dst = self.out / "images" / job.id
            if dst.exists():
                shutil.rmtree(dst)
            for n, im in enumerate(imgs):
                dst.mkdir(parents=True, exist_ok=True)
                f = dst / f"{n:02d}.png"
                shutil.copy2(im["path"], f)
                with Image.open(f) as image:
                    w, h = image.size
                rec["images"].append(
                    {
                        "path": str(f),
                        "src": im["path"],
                        "sha256": sha256_file(f),
                        "w": w,
                        "h": h,
                        "referenced": im["referenced"],
                        "prompt": im["prompt"],
                        "primary": im is prim,
                    }
                )
                if im is prim:
                    rec["primary"] = str(f)
        rec["answer"] = parse_json_answer(last_msg)
        if rc != 0:
            rec["error"] = f"codex exit {rc}" + (f": {err_ev}" if err_ev else "")
        elif not thread_id:
            rec["error"] = "no thread.started event"
        elif job.schema and rec["answer"] is None:
            rec["error"] = "schema job but last message is not JSON"
        elif job.expect_images and not rec["images"]:
            rec["error"] = f"expected {job.expect_images} image(s), thread produced none"
        if rec["error"] is None and job.schema:
            try:
                validate(rec["answer"], json.loads(job.schema.read_text()))
            except ValidationError as e:
                rec["error"] = f"schema validation: {e.message}"
        rec["ok"] = rec["error"] is None
        if job.expect_images and len(rec["images"]) > job.expect_images:
            rec["note"] = (
                f"{len(rec['images'])} PNGs for {job.expect_images} expected (hidden retries/variants)"
            )
        u_after = None
        try:
            u_after = usage.used_percent()
        except Exception:  # noqa: BLE001
            pass
        rec["usage_after"] = u_after
        self._append(
            "usage.jsonl",
            {
                "id": job.id,
                "t": time.time(),
                "before": pct_before,
                "after": u_after,
                "after_rollout_float": rec.get("usage_after_rollout"),
                "tokens": rec["tokens"],
                "seconds": secs,
                "ok": rec["ok"],
            },
        )
        self.log(
            f"{'OK  ' if rec['ok'] else 'FAIL'} {job.id} {secs}s rc={rc} thread={thread_id} "
            f"images={len(rec['images'])} usage {pct_before}->{u_after}% {rec['error'] or ''}"
        )
        return rec

    def run_job(self, job: Job) -> dict:
        key = job.key()
        hit = self._cache_get(job, key)
        if hit:
            self.log(f"CACHE {job.id} key={key[:12]}")
            self._append("out.jsonl", hit)
            return hit
        rec = None
        for attempt in range(1, self.retries + 2):
            rec = self._call(job, key, attempt)
            self._append("attempts.jsonl", rec)
            if rec["ok"]:
                break
        self._append("out.jsonl", rec)
        if rec["ok"]:
            self._cache_put(key, rec)
        return rec

    def run(self, jobs: list[Job], force: bool = False) -> int:
        done = self.done_records()
        todo = []
        for j in jobs:
            r = done.get(j.id)
            if not force and r and r.get("key") == j.key() and self._valid_record(j, r):
                continue
            todo.append(j)
        self.log(
            f"RUN {len(jobs)} jobs, {len(jobs) - len(todo)} already done, {len(todo)} to do, "
            f"parallel={self.parallel} max_pct={self.max_pct}"
        )
        failed, quota_stopped = [], []
        with cf.ThreadPoolExecutor(self.parallel) as ex:
            futs = {ex.submit(self.run_job, j): j for j in todo}
            for fu in cf.as_completed(futs):
                j = futs[fu]
                try:
                    r = fu.result()
                    if not r["ok"]:
                        failed.append(
                            {
                                "id": j.id,
                                "error": r["error"],
                                "thread_id": r.get("thread_id"),
                                "rc": r.get("rc"),
                            }
                        )
                except QuotaStop as e:
                    quota_stopped.append(j.id)
                    self.log(f"SKIP {j.id}: {e}")
                except Exception as e:  # noqa: BLE001
                    failed.append({"id": j.id, "error": f"{type(e).__name__}: {e}"})
                    self.log(f"ERROR {j.id}: {type(e).__name__}: {e}")
        (self.out / "failed.json").write_text(
            json.dumps(
                {
                    "failed": failed,
                    "not_started_quota": quota_stopped,
                    "stop_reason": self.stop_reason,
                    "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
                indent=1,
            )
        )
        if self.stop.is_set():
            self.log(
                f"STOP: {self.stop_reason}; {len(quota_stopped)} jobs not started. Rerun the same command to resume."
            )
            return EXIT_QUOTA
        self.log(f"DONE {len(todo) - len(failed)} ok, {len(failed)} failed")
        return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    from ..paths import textures_data_dir

    ap = argparse.ArgumentParser(
        prog="turkutex codex run",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("jobs", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("-j", "--parallel", type=int, default=4)
    ap.add_argument("--max-pct", type=float, default=float(os.environ.get("CODEX_MAX_PCT", 100)))
    ap.add_argument("--cache", type=Path, default=textures_data_dir("codex-cache"))
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument(
        "--force", action="store_true", help="rerun jobs even if done (cache still applies)"
    )
    ap.add_argument("--allow-unknown-usage", action="store_true")
    a = ap.parse_args(argv)
    r = Runner(
        a.out,
        a.parallel,
        a.max_pct,
        a.cache,
        retries=a.retries,
        allow_unknown_usage=a.allow_unknown_usage,
        use_cache=not a.no_cache,
    )
    return r.run(load_jobs(a.jobs), force=a.force)
