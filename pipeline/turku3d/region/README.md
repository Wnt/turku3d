# Region runner

Run in a sourced sandbox environment. Shared `raw/` is read-only. No paid services or
model calls are made by estimates. The current producer adapters are capability checks:
missing producers are explicitly skipped, and failures from available producers stop the run.

```sh
make region AREA=office-block
make region AREA=next-ring DRY=1
make region AREA=office-block STEPS="fetch assemble validate" REGION_WORKERS=4
make region-test
make cache-gc                  # size and reclaimable partial-file report
make cache-gc APPLY=1          # delete interrupted writes older than 24 hours
```

`STEPS` selects steps in dependency order; it does not silently rebuild omitted prerequisites.
Fetch, ground, structures, buildings, facades, ground textures, assemble, validate is the full
order. The legacy packager reproduces the current AOI until `package.assemble` is installed.
The ground CLI supports arbitrary tile lists. Current buildings/structures CLIs only support
the office block; they are explicitly skipped for expansion areas. Facade and ground-texture
area adapters remain unavailable until their owners publish them. No Codex job is fabricated.

Each sandbox serializes region runs because producers share their layer manifests. A batch
producer owns its parallelism (`buildings --jobs`); independent tile validators run in a bounded
thread pool. Shared manifests and dist trees are never concurrently rewritten per tile.
Logs, estimates, successful-step fingerprints and final summaries are in
`$TURKU3D_WORK/reports/region/<area>/`. A failed step has no success record. Resuming skips
successful steps only when code/input/dependency manifest hashes and output inventory match.
JSON and source files are content-hashed; large binary artifacts use size/mtime signatures.
Deleting a binary or changing its size/mtime invalidates the cache. This is not a full binary
integrity audit; the assembler/validator own that check.

The cached `work-3x3` inputs serve areas inside its coverage. For larger areas the fetcher
creates `$TURKU3D_WORK/cache/region-inputs/raw`, hardlinking immutable cached payloads and
copying mutable registries before downloading missing data there. It never modifies shared
raw files. Existing fetch registries provide checksums/resume. The mirrored input root is
recorded and passed to subsequent builders. New areas must still be supported by the chosen
producer; unsupported fixed-AOI producers are reported, not passed misleading area flags.

## Area YAML

Built-in `office-block` and `work-3x3` keep their canonical config definitions. New files live
in `data-manifests/areas/<name>.yaml`. Choose exactly one selection:

```yaml
name: two-tiles
crs: EPSG:3877+3900
margin_m: 50
tiles: [t0_0_0, t0_1_0]
```

```yaml
name: triangle
crs: EPSG:3877+3900
margin_m: 50
polygon_en: [[23459500, 6704500], [23460000, 6704500], [23459500, 6704000]]
```

Polygons select L0 tiles with positive-area intersection; edge contact alone does not count.
Builds ship whole selected tiles. Negative indices and disconnected tile lists are supported.
The next ring is 12 tiles surrounding the existing four, without rebuilding those four.
LAZ coverage includes the 50 m meshing margin around selected tiles.

## Estimates and quota

Cached WFS footprints supply building counts when available; otherwise the documented density
is 136 buildings per 500 m source tile. Street facades use 2 per building; panos use 1 per 3
facades. These are planning placeholders, not a survey. Estimates assume 150 MB/LAZ, 25 MB/pano,
100 MB products/tile and 450 CPU seconds/tile, plus model time divided by worker count.
Network time, contention, and existing panorama coverage are not measured.

When installed, `scripts/dev/codex-cost --by job_type --json` supplies known per-call token and
wall-time averages. Unknown usage/time rows retain placeholders. Otherwise per-call planning
values are label 12k tokens/60 s, improve 20k/180 s, ground texture 16k/120 s. An explicit
`python -m turku3d.region --area next-ring --dry-run --averages averages.json` can supply
`{"job_types":{"facade_label":{"tokens":12000,"seconds":60,"weekly_pct":null}}}`.
Cached and reasoning tokens are subsets, not additional tokens.

`REGION_QUOTA_CAP` defaults to 100. A spending Step must declare its jobs; the executor checks
fresh usage and estimated aggregate weekly percentage before starting any subprocess.
Unknown/nonfinite usage or cost fails closed. Token totals are never converted to quota
percent without calibration. Current area adapters spend no Codex quota; future spending
adapters must supply measured job costs and the existing free quota reader to `execute`.

## Shared panorama cache

New `PanoCache` downloads default to `$TURKU3D_DATA/cache/streetlevel`, keyed by provider,
panorama ID and resolution. Processes lock the shared cache and atomically publish complete
files, so identical requests reuse the same image. `TURKUTEX_STREETVIEW` remains an explicit
cache-root override. Derived reference crops, generated textures and jobs remain in WORK.
Existing W1-09 caches are neither moved nor deleted. Legacy WORK/cache/streetview, shared
raw/streetview and colon-separated TURKUTEX_READ_CACHE roots are reused read-only. The optional administrative import
`python -m turku3d.region.cache --import-cache /path/to/old/cache` preserves source files and
uses content-addressed hardlinks to deduplicate imported images. Do not import an active
writer's cache. Conflicting existing keys are retained. GC never deletes complete images;
it only removes stale partials while holding the same cache lock. Size reports count unique
inodes separately from logical paths.
