# Shared local tile farm

The LAN service at `http://192.168.1.114:7950` builds committed local revisions on demand.
It never publishes to production. The server uses four workers (maximum twelve; `POST /limits {"workers": N}` changes it live), a bounded
128-tile queue, and a shared content-addressed stage/input cache. Urgent camera tiles lead, then visible tiles, explicit CLI requests and background demand;
revisions share turns within each category.
The farm follows the game's version: game requests by distance from an address in
`ROOT/pin-clients.json` (the game's tile proxy) set the pin, persisted in `ROOT/pin.json`. A
later-registered version moves it at once; an earlier one only after a minute without requests
for the current pin. When the pin moves, every queued and running build of an earlier-registered
version is cancelled. The tiles games still demand are queued at the new version. Completed stages
stay cached, so a restarted tile reruns only what changed. Requests for earlier-registered versions
get `409 superseded`. A CLI request sees its rejection status, e.g. `429 queue-full`.
Game manifests stay `200` and report `queue_status` and `queue_state`.
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

All coarse LAZ consumers share source-order `.npy` columns under
`cache/raw-derived/decoded/<decoder-hash>/<source-sha256>.npy`. Atomic publication
and a source lock admit one decoder across workers and revisions. Coordinates,
classes, returns and intensity retain their exact values and order; RGB/GPS data
is skipped by codecs that support selective decompression. Downstream spatial
shards and reducers reuse these mappings. Existing raw-cache eviction also owns
these entries. The extra uncompressed working set is 29 bytes per source point.

The daemon divides the available CPU count by its worker count and passes that
budget to Rayon (LAZ), Numba and explicit job pools. BLAS/OpenMP stay at one thread
to avoid nested parallelism. Four workers on the 16-core host get four threads each.

Workers mount content-keyed roof, vegetation and lidar decoding stores under
`farm/cache/raw-derived/<kind>/<producer-hash>`. Producer namespaces include the decoding
implementation and coordinate configuration, so geometry or texture changes reuse these
expensive whole-source preparations. Existing private worker directories stay stable.
POST `/seed` with `{}` adopts complete private results by hard link; source generations and
destinations remain pinned during adoption. Lidar manifests must contain every declared
shard with the expected typed-array shape and byte length before atomic publication.
Raw decoding stores can be evicted between builds; source inputs are never removed.

Ground material maps are cached per channel under `cache/surface-maps/<content-hash>`.
Keys include producer code and source-array bytes, excluding branch paths and timestamps.
One producer lock shares extraction/procedural work across loops. Private library overlays
hard-link completed maps, so eviction of old shared entries cannot break a tile being packaged.
The base library keeps its existing material precedence; emitted PNG/material bytes are unchanged.

Adopted workers retain their original runtime deadline and disk-floor monitor. The daemon
verifies PID identity and output ownership before stopping its own process group, and keeps
product/cache pins until that process exits.

The farm also serves the client's existing `sorted-col-v1` collision bundle: sixteen
unchanged cells concatenated in sorted ID order, with a SHA-256 descriptor. Bundles live
in farm-owned delivery storage even when their source is an accepted fallback. Identical
cells across versions share the same bundle. The audit verifies both the full bundle and
each cell slice. Game `/want`, `/demand` and version health counts cover only the requested
generation; root `/health` remains the operator's complete farm view.

## Migrations: edit existing tiles without regenerating

A migration is a versioned, deterministic transform `(name, version, function)` over a finished
tile product (manifest and payloads), in `pipeline/turku3d/migrations/`. The ordered chain is
`registry.py`; append only. The generator applies the same chain at the end of every build
(`turku3d.coarse.build`, no flag), so a whole-city regeneration equals base + migrations.

```python
# pipeline/turku3d/migrations/merge_draws.py
from .core import Migration, Product, glb_chunks, glb_pack

def transform(product: Product) -> None:
    entry = product.manifest["visual"][product.tile]
    document, binary = glb_chunks(product.read(entry["url"]))
    ...                                             # deterministic edit
    product.replace(entry, glb_pack(document, binary))  # new vis.<hash>.glb, sha256/bytes updated

MIGRATION = Migration("merge-draws", 1, transform)
```

`product.put("../shared/{hash}.glb", data)` and `product.put("name.{hash}.json", data)` add
content-addressed payloads. An existing file is never modified; a byte-identical result keeps
the existing payload. Transforms update derived entry numbers (`tris`, `brotli_bytes`) themselves.
New client-visible manifest keys also need an entry in `PUBLIC` in `shared_tiles.py`.

Identity: each step's generation is `sha256(parent generation, name@version, code hash)`; the code
hash covers the transform's module, the framework and every `turku3d` module they import
statically (lazy imports included, `registry.py` excluded). The derived generation is the last
step's id. Each migrated tile's manifest records `migrations` (the chain, also in fresh builds)
and `migration_base` (base revision, generation and manifest SHA-256), and passes `Farm.validate`.

From a committed checkout:

```sh
scripts/dev/tile-migrate register --base bulkgen2-3d1445b2 [--alias NAME]   # default <base>+m<id8>
scripts/dev/tile-migrate apply --version NAME t0_0_0 t0_1_0 t0_-1_0          # foreground
scripts/dev/tile-migrate run --version NAME --jobs 6 --watch 120             # city, background
scripts/dev/tile-migrate status --version NAME
scripts/dev/tile-migrate parity --version NAME --generated GENERATOR_ALIAS t0_12_-12
```

`register` uploads the snapshot, runs `tile_migrate.py derive` on the farm host (writes
`revisions/<derived>/revision.json` with `kind: migrated`, base, code snapshot, parents and
per-step ids) and registers the alias. It never repins the game; the coordinator does.
`run` migrates every finished base tile with at most six nice'd processes: hard links for
unchanged payloads and referenced shared assets, a new manifest, `Farm.validate`, then
`ready.json`. It is resumable (finished tiles whose base manifest is unchanged are kept), starts at
the centre, and `--watch` rescans for new base tiles until two idle hours. A chain that extends an
existing derived generation starts from its finished products and applies only the new steps.
Results: `farm/migrations/<generation>/run.jsonl` and `status.json`; log `farm/logs/migrate-*.log`.
A no-op migration of the 3,440-tile city took 102 s and 0.29 GiB (new manifests only).

Serving: `/v/<migrated alias>/coarse/<tile>/manifest.json` serves the migrated product. A tile
not yet migrated is served as its own base product (`stale: true`, `source_kind: base`) while a
migration job runs beside the build workers (two light slots). A missing base tile first builds
at the base revision, then migrates. A migrated generation has its base's registration vintage,
so switching the game to it supersedes nothing the base would not, and base builds continue. Its
alias pins the base and prefix generations and the code snapshot against eviction. A
content-addressed payload a migration left unchanged is served under its base URL, so a client
switching to the migrated alias reloads only the tiles the chain changed (a no-op chain: none).
`apply`/`run --code REF` run a newer runner snapshot over an existing alias; a snapshot whose
transform code hashes differ from the derived revision's is refused.

`parity` compares a fresh generator build at the migration commit (registered with the base's
source version, built explicitly as one tile) with the migrated base product: every referenced
payload must be identical; manifest keys that only record build provenance (`built`, `seconds`,
`code`, `collision_code`, `migration_base`) are listed apart.

Checks: `bash pipeline/env/run.sh python -m pytest pipeline/tests/test_migrations.py -q` and
`python3 -m unittest tests.test_shared_tiles.MigratedRevisionTests` (from `scripts/dev`).
