"""Bounded parallel judging; stop submitting work when any worker hits the quota guard."""

import json
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from qalib.paths import resolve

from .judge import QuotaError, judge


def judge_pairs(pairs, output, *, workers=3, cap=None, with_ids=True, judge_fn=judge):
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    output.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()

    def one(pair):
        if stop.is_set():
            return {"ok": False, "error": "quota stopped batch", "quota": True}
        try:
            return judge_fn(
                resolve(pair["ref"]),
                resolve(pair["render"]),
                resolve(pair["id_image"]) if with_ids and pair.get("id_image") else None,
                context=pair.get("context", ""),
                cap=cap,
            )
        except QuotaError as e:
            stop.set()
            return {"ok": False, "error": str(e), "quota": True}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    results = {}
    todo = iter(pairs)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {}

        def submit():
            p = next(todo, None)
            if p is not None:
                pending[pool.submit(one, p)] = p

        for _ in range(workers):
            submit()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                p = pending.pop(future)
                result = future.result()
                results[p["pair_id"]] = result
                (output / f"{p['pair_id']}.json").write_text(json.dumps(result, indent=2))
                print(
                    f"{p['pair_id']}: score={(result.get('result') or {}).get('similarity')} ok={result.get('ok')}",
                    flush=True,
                )
            for _ in done:
                if not stop.is_set():
                    submit()
    return results
