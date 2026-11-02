# Shared local tile farm

The LAN service at `http://192.168.1.114:7950` builds committed local revisions on demand.
It never publishes to production. The server uses four workers (maximum four), a bounded
128-tile queue, and a shared content-addressed stage/input cache. Urgent camera tiles lead, then visible tiles, explicit CLI requests and background demand;
revisions share turns within each category.
Queued speculative requests expire after three minutes without requests or live demand. Identical requests join
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
Forward GET `/coarse/demand` and `/coarse/want`; they require no POST. The demand query
carries the existing client ID, tiles, urgent tiles and visible tiles. Manifest GETs queue
builds independently; GETs without the browser distance `d` are explicit requests.
The proxy pins every payload to its manifest's commit; changing an alias cannot mix versions.
A cache miss returns the newest complete retained farm product, or an accepted fallback
listed in `farm/fallbacks.json`, while the requested version builds. Its manifest explicitly
sets `stale: true`, `requested_revision`, actual `revision`, and `source_kind`; payload URLs
remain pinned to the actual retained source. Judging must reject stale manifests. If no
complete retained pair exists the response is 202 and the streaming loop polls.

Service owner (tiles) starts the daemon with:

```sh
python3 scripts/dev/shared_tiles.py --host 192.168.1.114 --port 7950 \
  --root /home/wnt/turku3d-local/loops/tiles/farm \
  --runner /home/wnt/turku3d-local/loops/tiles/shared-tiles-run.sh --workers 4
```

Source inputs remain read-only at `~/turku3d-local/combined/data`; missing source data is
reported as a failed build, never fetched into raw. Farm-owned outputs are under `farm/products`,
source snapshots under `farm/revisions`, and intermediates under `farm/cache`. Raw-file hash
memos use per-source locks; texture encodes also share a content-keyed cache across revisions. Final-product locks
are per generation/tile, so different versions can build concurrently. Prepared inputs publish atomically. Stage-key locks prevent
duplicate stage work. The farm owns shared-cache eviction rather than a per-branch two-version cap.

`farm/events.jsonl` logs queued/completed/failed jobs, wall time and every stage's cache status/time.
`/health` reports requests, finished-product hits, coalesced requests, stage hits/misses, failures,
prepared-input hits/misses, active revision/PID/runtime, the ordered queue and free disk.
Build receipts separate queue wait from generator runtime, including adopted workers. Per-build stdout is in `farm/logs`.

Compare completed products without queuing work:

```sh
scripts/dev/shared-tiles compare --before OLD_REVISION --after NEW_REVISION t0_0_0
```

The report lists changed input/stage keys, hits, misses and their timings. An unchanged key
that missed is listed separately: eviction or a cold cache can require recomputation without
a producer change. Aliases are resolved once before reading. Missing full-product receipts
fail the comparison; stale fallbacks are never used as comparison evidence.
Metrics describe the original completed build. `same_product: true` means the two references
share that existing product; the comparison itself performs no rebuilding.

Admission reserves 2 GiB per active build above a 25 GiB floor. Eviction removes old unpinned
products and their source snapshots, then old intermediate entries in LRU order. Requested areas and held content-key locks
protect stage/input entries while other areas can be evicted during active builds. Current aliases and active jobs
pin products; no other loop's output is removed. The worker monitor terminates only its own
build group if free space falls below 25.5 GiB, or the 30-minute per-build timeout expires.

Checks:

```sh
python3 -m unittest discover -s scripts/dev/tests -p test_shared_tiles.py
node --test scripts/dev/shared-tile-proxy.test.mjs
python3 scripts/dev/shared-tile-audit.py http://127.0.0.1:5795/coarse/ t0_0_0
bash pipeline/env/run.sh python -m pytest pipeline/tests/test_stage_cache.py -k 'shared or stage_dag' -q
```

## Read-only source overlays

`shared_sources.py` fetches explicitly inventoried lidar/WFS gaps into farm-owned downloads,
merges checksum registries privately, and atomically publishes `sources/versions/<hash>`.
Existing raw files remain read-only symlinks; neither acquisition nor publication writes into
the shared input root. Validation covers all four 250 m quadrants of every requested block.
Outside-city lidar blocks are excluded from acquisition.

To pin a registered commit to a published overlay (farm API v2):

```sh
scripts/dev/shared-tiles register --alias ground --source-version HASH
```

This returns a new immutable revision ID while retaining the original Git commit in metadata.
The old revision and its payload URLs continue to reference their original data. Unchanged
prepared inputs, stages and texture encodes are shared across source versions too. Advancing
an existing alias inherits its source overlay unless another explicit source version is supplied.

The pending queue and running worker PIDs survive daemon restarts. The owner can use
`shared-tiles-upgrade.py` for a brief scheduler handoff: it preserves build sessions and the
replacement daemon adopts them instead of duplicating work. Use only the farm's own PID/root.
Completed payloads have a 15-minute idle grace lease after an alias moves, in addition to
active-version pins, so in-flight old manifest URLs remain available during a switch.

Farm-owned source overlays also accept `supplements/<work-directory>/...`. Their content
hashes participate in the source version; workers mount these directories into their private
work root without modifying shared raw or another loop's work. For example the accepted
Port Arthur survey uses `supplements/lidar-supplement/23458000_6704500.laz`.

POST `/cancel` with `{"revision":"alias-or-id","tiles":["t0_0_0"]}` drops only queued jobs.
It reports already-running tiles and lets those generators complete normally.

The owner can add a known-broken Git revision and reason to `farm/retired.json`. Old clients
then receive `revision-retired` (or a clearly marked retained full pair) instead of repeatedly
queuing a deterministic failure. Already running work requires explicit owner handling.

## Capture preparation without a browser

Reserve a browser only after the required immutable tile revision is complete:

```sh
node scripts/dev/loop-wait-tiles.mjs --service http://192.168.1.114:7950 \
  --version COMMIT_OR_SOURCE_REVISION --timeout 600 --out /tmp/ready-tiles.json t0_0_0 t0_-1_0
```

The command launches no browser. It waits through pending builds and complete but stale
fallbacks, records served revisions and visual hashes, and fails if any requested tile cannot
become fresh. The capture tool must still verify the actual resident hashes after loading.

## Shared decoding caches

Workers mount content-keyed roof, vegetation and lidar decoding stores under
`farm/cache/raw-derived/<kind>/<producer-hash>`. Producer namespaces include the decoding
implementation and coordinate configuration, so geometry or texture changes reuse these
expensive whole-source preparations. Existing private worker directories stay stable.
POST `/seed` with `{}` adopts complete private results by hard link; source generations and
destinations remain pinned during adoption. Lidar manifests must contain every declared
shard with the expected typed-array shape and byte length before atomic publication.
Raw decoding stores can be evicted between builds; source inputs are never removed.

Adopted workers retain their original runtime deadline and disk-floor monitor. The daemon
verifies PID identity and output ownership before stopping its own process group, and keeps
product/cache pins until that process exits.

The farm also serves the client's existing `sorted-col-v1` collision bundle: sixteen
unchanged cells concatenated in sorted ID order, with a SHA-256 descriptor. Bundles live
in farm-owned delivery storage even when their source is an accepted fallback. Identical
cells across versions share the same bundle. The audit verifies both the full bundle and
each cell slice. Game `/want`, `/demand` and version health counts cover only the requested
generation; root `/health` remains the operator's complete farm view.
