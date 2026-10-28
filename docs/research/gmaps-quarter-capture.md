# Quarter-area reference capture

The private Google 3D comparison tools support independent square extents inside
an L0 draft tile. The game tile grid and published layer contracts are unchanged.
Reference images, meshes, selection evidence and detailed findings remain local.

A private capture keeps its parent `tile` and adds `coverage_id` and
`extent_en: [emin, nmin, emax, nmax]` in the project's EPSG:3877 coordinates.
A 125 m quarter has its own metadata completeness audit, decoded mesh selection,
reference raster, matching own-build raster and coverage entry. Independent
orthophoto confirmation crops the same quarter before resampling; it must not
resize the whole parent tile into the quarter. Own-raster caches distinguish
extents belonging to the same parent.

Round 2 records seed `20261101` and selects 40 quarters across a 5 by 4 spatial
stratification of the requested box. It excludes existing captured parents and
quarters with at least 50% mapped water/forest, requires mapped buildings or an
urban road, and reserves examples containing bridge, tunnel, rail, park and quay
features. The reproducible plan and source-file inventory are private.

Two separate sandbox Chrome for Testing profiles each retain one Maps SPA tab.
A batch pans between quarters without reload recovery. Every rotation, zoom and
ordinary pan synchronizes the steering position from the effective native URL;
otherwise rotation/zoom translations accumulate position drift. A native URL is
a steering observation, not a calibrated N2000 camera pose. Captured metadata
independently proves whether the intended extent loaded. Leaving the normal 3D
map for a photo overlay fails that view. Bounded local pan retries retain partial
data without accepting it as a qualified reference.

A later native context-menu probe showed that the URL position and the visible
map point differ in tilted views. Synchronizing the URL prevents accumulated
camera drift but does not prove that the intended surface is centred. Further
capture expansion needs a controlled visible-point targeting check. The private
browser diagnosis retains the traces and an unpromoted prototype; it makes no
speed-improvement claim from that interrupted comparison.

Headed and headless initial captures of the same close 3D scene were visually
compared before choosing headless mode. Per-view timings, native positions,
loading queues, per-extent sessions and host browser accounting remain private.
Both concurrent browser runtimes count against the task's 120 browser-minute
allowance. An attempted quarter is not automatically a complete 0.015625 km²
reference.

OCI snapshots accept only full-quality, non-stale manifest responses. They freeze
the advertised GLB and referenced shared textures with SHA256 verification. Each
score retains the exact own-build hash and source/scorer identities; a later
producer deployment does not mutate that snapshot. Incomplete metadata,
unsupported calibration or unavailable semantic evidence causes an abstention.

The private findings summary ranks textures and bridges first, then ground,
wall and object geometry. Roof form, building height and roof colour remain
informational. Ground material appearance groups do not independently establish
a semantic material type. Manual type reviews record their independent imagery
and limits separately. Small props, wall geometry, bridge undersides and tunnel
interiors remain explicitly unmeasured where independent semantic scoring is
unavailable.

After the browser lanes close, an offline reconciliation can reuse original,
hash-identical response bodies observed by either browser. Each extent receives
another independent epoch/completeness check. This performs no new provider
requests and does not exempt missing nodes. Original inventories remain private.

An explicit round-2 review authorization also permits at most 5% missing required
terminal nodes when metadata is resolved and nonempty. The missing-node projected
footprints are excluded from both calibration and every score mask. The report
labels this partial qualification; empty captures and unresolved metadata remain
unmeasured. The round ends by scoring eligible extents with four processes and
handing off remaining capture work separately.

## Local tools and evidence

Tools: `~/turku3d/private/gmaps-extract/` (`round2_select.py`, `capture.mjs`,
`round2_oci.py`, `round2_process.py`, `round2_reconcile.py`,
`round2_summary.py`, `round2_map.py`).

Evidence root: `~/turku3d/private/gmaps-data/round2/`:

- `plan.json`: seed, selected extents, source inventory and exclusion evidence.
- `coverage.json`: per-quarter qualification, timing and score availability.
- `oci-inventory.json`: immutable own-build snapshots and accepted fetch times.
- `findings.md` and `findings.json`: ranked findings and explicit abstentions.
- `google-reference-coverage.png`: previous full tiles plus new quarter states.
- `headless-validation.json`: initial headed/headless rendering comparison.

From a sourced project sandbox, the focused regression checks are:

```sh
bash pipeline/env/run.sh python \
  /Users/wnt/turku3d/private/gmaps-extract/run_tests.py \
  /Users/wnt/turku3d/private/gmaps-extract/test_extent.py
```

The broader private suite is `test_private.py test_loop.py test_extent.py` in that
same directory. It covers extent isolation, own-cache separation and independent
orthophoto cropping alongside existing decoder, completeness and scoring cases.
No provider imagery or raw reference data is part of this document or the game.

The private test runner creates its own QA parent directories before selecting a
pytest temporary directory. This also works in a fresh frozen post-deploy
workspace; an absent QA directory must not turn unrelated fixture setup into a
false regression.

## Round 3: synthetic native-client requests

The round-2 diagnosis first led to a native-coordinate camera proof. That proof
qualified `t0_-2_-6_q10` at a maximum accepted target error of 3.423 m, with
417/435 terminal nodes and 4.138% masked required nodes missing. Its successful
capture took 128.351 s, or 205.568 s through scoring. A rejected 8.20 m targeting
attempt added 120.453 browser-seconds. The subsequently started camera sweep was
stopped on Jonni's instruction. The superseded automated pan code was removed;
its source remains available at commit `8768f73b4d39`.

Jonni then explicitly authorized synthetic node requests through the Maps page's
native transport. This supersedes the earlier no-replay capture-only restriction
for this workflow. `gmaps_synthetic_transport.mjs` instruments the native XHR
constructor/open/send path before Maps initializes. It preserves an observed
request template, native credentials mode and explicit headers. Missing bulk
metadata is requested first; every node ID and epoch comes from the independently
resolved metadata tree for the requested extent. Terminal payloads follow with
16 concurrent in-page XHRs. The tool invents no session tokens, keys or endpoint
format, and does not move the camera.

A fresh headless proof loaded **435/435** terminal nodes, resolved all metadata
and scored with zero missing nodes. Capture work took **3.489 s**, native
initialization **4.804 s**, and scoring **31.079 s**: **39.372 s** total. This
controlled score used the exact immutable OCI GLB from the camera proof, already
retained locally; the live OCI draft was rebuilding. Transport batches resolved
24 metadata branches and requested 392 missing payloads without transport errors.
The capture comparison is about 15 times faster including native initialization;
end-to-end timings also differ in OCI download/cache work and score support.

The repository runner is `tools/refs/gmaps_synthetic_run.py`. It reads the same
40-quarter private plan (seed 20261101) and installed private decoder/scorer
modules, without editing their source. Products live under
`$TURKU3D_WORK/gmaps-round3/synthetic/`, outside the published tree. One
content-addressed catalogue per browser session serves every new/resumed extent
through hard links. An append journal avoids rewriting the entire growing index
per response; each audit compacts the index. Each extent independently verifies
nonempty metadata, epoch-matched payloads and completeness. Zero missing with an
empty denominator never qualifies. Accepted missing-node footprints remain
masked from calibration and every score.

Independent processes score eligible extents while capture continues. Current
OCI snapshots require a full, non-stale manifest and SHA256-verified GLB/textures.
Each scorer renews one nonurgent visible-tile demand lease every two seconds,
uses that same client on manifest polls, and releases it after freezing bytes.
Legacy unleased polls can be pruned while players are active.

`coverage.json`, `findings.md` and `findings.json` update incrementally. Each ten
scored quarters triggers an orchestrator board note. Findings rank textures and
bridges first, ground/wall/object geometry next, and roofs/heights last. Unsupported
wall, prop, bridge-underside and tunnel-interior semantics remain abstentions.

Run from the assigned checkout with its sandbox sourced:

```sh
scripts/dev/codex-task wait-cmd -- bash pipeline/env/run.sh python tools/refs/gmaps_synthetic_run.py proof
scripts/dev/codex-task wait-cmd -- bash pipeline/env/run.sh python tools/refs/gmaps_synthetic_run.py sweep
node --test tools/refs/test_gmaps_capture_state.mjs tools/refs/test_gmaps_synthetic_transport.mjs
bash pipeline/env/run.sh python tools/refs/test_gmaps_oci.py
```

`GMAPS_TOOLS` may name the installed private decoder directory; its default is
`~/turku3d/private/gmaps-extract`. Chrome uses the sandbox's profiles and host
admission/budget accounting. The runner never builds a local world tile. A fresh
proof must be eligible and scored before the sweep starts. A manual capture
window remains a fallback if the native synthetic route is blocked; no manual
window was needed for the successful proof.

The scoring adapter also scopes decoding before unpacking geometry: cached
metadata AABBs intersect the quarter plus 25 m, conservatively wider than the
decoder's 20 m clip. The full response catalogue stays intact. A separate decoder
input view uses hard links and reuses existing products; interruption does not
replace the original capture inventory. Spatial metadata caches are addressed
by response hash and parser/radius identity. Disk accounting counts hard-linked
bodies once and tolerates atomic temporary-file renames.

The selection retained every source mesh in fourteen completed unscoped decodes.
A representative 7,307-response quarter reduced to 930 decoder inputs while
preserving all 574 mesh records and producing bit-identical height, RGB, normal
and LOD rasters. This representative validation took 13.50 s, excluding its
sandbox-copy setup; no area rebuild or browser was needed.

A complete reference can still lack enough independent open ground for vertical
calibration. Such a score is explicitly **abstained**, with its reason and own
snapshot recorded in `score-abstention.json`. It is not counted as measured and
is not retried indefinitely. Calibration thresholds are unchanged. Transient OCI
availability failures remain retryable.

For the authorized fast-capture extension, `gmaps_extra_select.py` selects twenty
more urban quarters within the original requested parent tiles, prioritizing
bridges, retaining the same OSM exclusions and seeded order. It writes private
`extra-plan.json` and `expanded-plan.json`. `GMAPS_PLAN` selects an explicit plan
for the runner; the original forty remain its prefix. Existing eligible captures
and completed scores are reused. Milestone notices retain their previous count
across restarts.

### Completed round-3 result

All **60** quarter references qualified: the unchanged original forty plus twenty
additional urban quarters in their existing parents. **57 scored** against frozen
OCI drafts, covering **0.890625 km² of nominal new scored footprints**; **1,807 m²**
of accepted missing-node footprints were excluded from calibration and scores.
The original forty produced 37 scores and three calibration abstentions
(`t0_2_-3_q11`, `t0_2_0_q11`, `t0_0_-1_q11`); all twenty extension quarters scored.

Successful synthetic capture including native initialization averaged about
**15.93 s/quarter** across the sixty. Core per-quarter work excluding initialization
and final catalogue flushes averaged **15.41 s**. Total task browser use, including
the stopped camera sweep and proof/setup failures, was **26.069/120 minutes**.
All capture browsers are closed. Scoring completed with four independent workers;
renewable OCI leases and physical-file quota accounting avoid the legacy demand
pruning and repeated hard-link scans.

Milestone handoffs were sent at 10, 20, 30, 40 and 50 measured quarters, followed
by the final handoff. Final confirmed counts are **textures 0, bridges 0,
ground/wall/object geometry 0, roofs/heights 18**. High-priority reference-only
appearance and bridge candidates remain review inputs, not confirmed defects.
The facade-characterization task received the read-only paired-colour contract.

Private final evidence is under
`$TURKU3D_WORK/gmaps-round3/synthetic/{coverage.json,summary.json,findings.md,findings.json}`.
The reports retain exact own/reference/scorer identities and all abstention reasons.
`bash pipeline/env/run.sh python tools/refs/gmaps_summary.py` refreshes the area and
timing summary without opening a browser or issuing provider requests.
