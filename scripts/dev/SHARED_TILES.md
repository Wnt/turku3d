# Shared local tile farm

The LAN service at `http://192.168.1.114:7950` builds committed local revisions on demand.
It never publishes to production. The server uses two workers (maximum four), a bounded
128-tile queue, and a shared content-addressed stage/input cache. Identical requests join
one job. Renderer-only revisions share completed products; generator edits reuse unchanged
stage outputs, including packaging when emitted inputs are identical.

From a loop checkout, commit the candidate, then register it:

```sh
scripts/dev/shared-tiles register --alias ground
scripts/dev/shared-tiles build --version ground t0_0_0
scripts/dev/shared-tiles status
```

Registration uploads an immutable `git archive` snapshot by SSH, with no push or merge.
Uncommitted edits are deliberately excluded. A branch is an alias to the selected commit,
not a live checkout. Register again after each judged/kept commit or candidate needing tiles.
The integration alias should only advance after the corresponding merges are committed.
`build` requests only the specified tiles; browser requests build other viewed tiles.

Run the proxy on your own companion port and point Vite's existing `TURKU3D_COARSE_PROXY`
at it. The config is read for every request, so version/scenery changes require no restart:

```json
{"service":"http://192.168.1.114:7950","version":"ground","scenery":"http://127.0.0.1:5746"}
```

```sh
node scripts/dev/shared-tile-proxy.mjs 5795 /absolute/path/to/tile-service.json
```

Use your own port, not 5795. Keep the horizon owner's `/coarse/scenery` endpoint as `scenery`.
The proxy pins every payload to its manifest's commit; changing an alias cannot mix versions.
A service cache miss returns 202, which the game's existing streaming loop polls. No stale
482-tile fallback is substituted for the requested revision.

Service owner (tiles) starts the daemon with:

```sh
python3 scripts/dev/shared_tiles.py --host 192.168.1.114 --port 7950 \
  --root /home/wnt/turku3d-local/loops/tiles/farm \
  --runner /home/wnt/turku3d-local/loops/tiles/shared-tiles-run.sh --workers 2
```

Source inputs remain read-only at `~/turku3d-local/combined/data`; missing source data is
reported as a failed build, never fetched into raw. Farm-owned outputs are under `farm/products`,
source snapshots under `farm/revisions`, and intermediates under `farm/cache`. Raw-file hash
memos use per-source locks; texture encodes also share a content-keyed cache across revisions. Per-tile locks
serialize preparation across versions; distinct tiles run concurrently. Stage-key locks prevent
duplicate stage work. The farm owns shared-cache eviction rather than a per-branch two-version cap.

`farm/events.jsonl` logs queued/completed/failed jobs, wall time and every stage's cache status/time.
`/health` reports requests, finished-product hits, coalesced requests, stage hits/misses, failures,
prepared-input hits/misses, active/queued jobs and free disk. Per-build stdout is in `farm/logs`.

Admission reserves 2 GiB per active build above a 25 GiB floor. Eviction removes old unpinned
products and their source snapshots, then idle shared intermediate entries in LRU order. Current aliases and active jobs
pin products; no other loop's output is removed. The worker monitor terminates only its own
build group if free space falls below 25.5 GiB, or the 30-minute per-build timeout expires.

Checks:

```sh
python3 -m unittest discover -s scripts/dev/tests -p test_shared_tiles.py
node --test scripts/dev/shared-tile-proxy.test.mjs
python3 scripts/dev/shared-tile-audit.py http://127.0.0.1:5795/coarse/ t0_0_0
bash pipeline/env/run.sh python -m pytest pipeline/tests/test_stage_cache.py -k 'shared or stage_dag' -q
```
