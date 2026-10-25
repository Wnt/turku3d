# W2-02: Region runner and cost estimator for expanding beyond the office block

Read `AGENTS.md`. Timebox: about 30 minutes; split follow-ups rather than overrun.

## Why

After the office block, the city is built outward block by block, then district by district. This needs one command
that takes an area and runs the whole chain, with a cost and time estimate **before** it spends Codex quota. Layer
producers (ground, structures, buildings, facades, ground textures) are being finished by other agents right now.
Wire the orchestration and estimation, not the layer internals.

## Deliverables

1. **Area definitions** `data-manifests/areas/*.yaml` (the office-block and work-3x3 ones exist): add a way to define
   an area by polygon or tile list, and a ready "next ring" area around the current AOI.
2. **`make region AREA=<name> [STEPS=...] [DRY=1]`** (thin Make target plus a Python orchestrator in
   `pipeline/turku3d/region/`):
   - Fetch (cached), then per-layer builds in dependency order, then assemble, then validate. Each step is skipped
     when its inputs are unchanged, using the layer MANIFEST hashes.
   - Parallel across tiles with a worker limit, resumable, with per-step logs and a final summary.
   - Layers not yet available on main are reported as skipped, not failed.
3. **Estimator** (`DRY=1`): for an area it reports:
   - tile counts, LAZ tiles to download, building and street-facade counts, panorama counts needed
   - estimated Codex calls and tokens per job type, using the ledger averages from W2-01's `codex-cost` once it
     exists, otherwise documented placeholders
   - estimated wall time and disk use

   It refuses to start a Codex-spending step when the estimate would push the weekly quota over a configured cap.
4. **Disk hygiene**:
   - A shared, deduplicated panorama and image cache under `~/turku3d/data/cache/streetlevel/`, so sandboxes stop
     re-downloading. One W1-09 sandbox cache is already 20 GB.
   - A `make cache-gc` with a size report.
   - Coordinate with W1-09 through the board before changing the textures/ cache paths.
5. Tests on synthetic areas (fast). A dry-run report for the "next ring" area is attached in the result.

## Acceptance

- `make region AREA=office-block` reproduces the current AOI build (no-op when cached).
- `make region AREA=<next ring> DRY=1` prints a sensible estimate.
- The shared cache is in place, `cache-gc` works, and the gate is green.
