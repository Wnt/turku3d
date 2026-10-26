# Shared instance placement

`turku3d.placement` checks props (including signs), vegetation and `traffic_static`
before their CLI producers publish layer manifests. It consumes **published** ground
and optional structures collision triangles, cached city surface/road/building vectors,
building-layer footprints, and cached OSM water, bridges, parking and access geometry.
Missing ground or required semantic inputs fails the build; there is no lidar fallback.
Input hashes include surface manifests, semantic sources, other layer inventories,
placement code and the audit report.

## Rebuild and inspect

Run from this worktree with its sandbox environment. Ground and buildings must already
be published under `$TURKU3D_WORK/build/layers`. Rebuild sequentially so each producer
sees the other inventories; the last producer checks against every preceding layer.

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.props --area phase1
bash pipeline/env/run.sh python -m turku3d.vegetation
bash pipeline/env/run.sh python -m turku3d.parked --seed 16
bash pipeline/env/run.sh python -m turku3d.placement
bash pipeline/env/run.sh python -m pytest -q -n 2 pipeline/tests/test_placement.py pipeline/tests/test_props.py pipeline/tests/test_vegetation.py pipeline/tests/test_parked.py
scripts/dev/gate --base main
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile-show
```

Props cover the Phase 1 envelope in this command; vegetation covers the office core
plus its margin, and parked cars cover the office core. Signs belong to props, not a
separate producer. The last two commands assemble and serve on sandbox port base + 1;
there is no main deployment or merge.

Reports are `$TURKU3D_WORK/reports/placement/<layer>/{placement.json,index.html}`.
HTML embeds the cached orthophoto and marks accepted/corrected/omitted objects, with
one row per source violation. JSON retains source and final transforms and residual
hard violations. Prop/car candidates initially have unassigned height zero: their
source `buried` entries describe unsnapped candidates, not measured live-build defects.
A rejected source candidate is recorded as omitted; any violation remaining on an
accepted output raises an error before publication.

For fixed-camera **layer previews** (not live-game evidence), snapshot the original
props, vegetation and traffic_static layer directories to
`$TURKU3D_WORK/reports/placement/before/` before rebuilding, then run:

```sh
bash pipeline/env/run.sh python -m turku3d.placement.preview
```

This writes `aurasilta-{before,after}.png` and `rantakatu-{before,after}.png`, loads
actual GLBs with ground/building context, and uses an isolated Chrome profile and
sandbox port base + 3. The register names the bridge **Auransilta**, centred around
engine x305/z230. Ground/building materials in these previews are unassembled.

## Rules and limits

- Exact barycentric triangle heights at the centre and four base corners. Upright
  assets snap to the highest support sample; >0.15 m relief/gap and >0.02 m burial
  fail. Nonwalkable (>40 degree) triangles are excluded. No nearest-vertex heights.
- Benches/furniture require walkable paving, grass or similar surfaces, excluding
  carriageways and water. Trees require grass/soil or explicit OSM tree-pit evidence.
  Cars require mapped parking or explicitly tagged road-edge parking; candidate bay
  and lane selection still belongs to the parked producer.
- Complete base footprints clear buildings, mapped crossings/driveways and other
  object bases by 0.15 m. Trees use trunk dimensions, not their overhanging crowns.
- Signs/posts require nearby roads. Props use class-specific +Z headings: signs face
  oncoming right-hand traffic, benches face away from the kerb, lamps reach over the
  carriageway, and kerb furniture follows its tangent. Direction is checked only within 12 m; missing/coincident directions are listed as
  `orientation_unverified` in the combined audit, without inventing a heading.
- A deterministic 0.25 m search grid finds the closest sampled valid position within
  2 m, adjusting height and bench/sign orientation on each trial. No valid sampled
  position means omission; a grid search is not a proof that no continuous solution exists.
- Missing bridge-deck objects are omitted without relocation. A ground triangle with
  material 24 from a 2D street polygon does not count: a deck must be an actual
  `structures` or `bridges` collision triangle carrying `bridge_deck`.
- Semantic completeness is bounded by the cached maps. Unmapped driveway/tree-pit
  evidence and tilted suspension fitting remain outside this validator. Cross-layer
  inventories must be rebuilt sequentially; concurrent publication is unsupported.

After publishing bridge decks, rebuild the recorded omitted bridge objects. The
validator keeps rejecting them until actual support is present.

The assembler preserves existing GPU-instanced tree nodes and clones shared prop
primitives before baking each node transform. Reusing the mutable primitive caused
cumulative transforms and incorrect world positions even when source layers were valid.
Synthetic package tests cover both paths, including decoded world bounds.
