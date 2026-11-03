# Handover: rulesmith (edit sessions to generator rules)

Jonni corrects street objects in the in-game editor. Each edit session is evidence of a generator rule that went
wrong. The job is to turn clusters of edits into better city-wide rules in the generator, plus a tile migration for
existing tiles. Edits are never replayed as per-object overrides.

- Contract: `~/turku3d/loops/editor/EDIT-FORMAT.md` (row format, provenance, context, tolerances, §9 metadata gaps).
- Loop state: `~/turku3d/loops/rulesmith/STATUS.md`; scratch tools: `~/turku3d/loops/rulesmith/scratch/` (local only).
- Sessions: `~/turku3d/edits/<session>/` (`session.json`, `edits.jsonl`, `shots/`; screenshots never go into git).

## Per-session workflow

1. **Collect.** `python3 scripts/dev/edit_sessions.py clusters <session>...` (and `effective`). Check that the
   session is closed and that `session.json` counts match the rows in `edits.jsonl`. The editor's outbox once held
   rows back; ask the coordinator to recover them before trusting the count. Look at the before/after screenshots
   of a few edits per cluster, and note whether the point cloud was on (`build.url` has `pointcloud=1`). Positions
   Jonni placed against the point cloud are survey evidence; positions placed without it are judgement.
2. **Get the generator's exact inputs.** On osgallery-dev, prepare the tile's inputs from the base's source version
   (`TURKU3D_DATA=<farm>/sources/versions/52a82e51…`, your own `TURKU3D_WORK`), with the pipeline's own loaders:
   `coarse.inputs._sources(tile, Sources())` + `_prepare_one(tile, dataset, paths)` for `poles`, `barriers`, `wfs`,
   `water`. Copy the pickles and the pinned tile's manifest and visual GLB (from the farm) to the Mac.
3. **Reproduce.** Decode each edited object from the pinned GLB and match it to the inputs by `object_id`. The
   props batch is meshopt-compressed; use `migrations.furniture.read_accessor`. The generator's yaw is in the
   `poles` input. Since `furniture@1`, rows also carry `yaw_deg` and `orientation`. The editor's `original.yaw_deg`
   is only a long-axis estimate (mod 180°); the true yaw after an edit is the generator's yaw plus `delta.yaw_deg`.
4. **Check the evidence.** Probe the survey LAZ (`raw/laz/pistepilvi`) at the original and edited positions:
   count returns 0.3–1.1 m above the local class-2 ground within 0.7–1 m. A bench gives 20–60 returns (class 4, or
   12 in overlap strips). Check register attributes (`GIS_Varusteet`: no status or date that separates real from
   missing benches was found). Measure distances to OSM barriers, facades and the shore.
5. **Form a rule, and check it against all sessions.** Write it as one function shared by the generator stage and
   the migration. Run it offline on the session tiles and score every edit with the EDIT-FORMAT tolerances
   (position ≤ 0.5 m, or 25 % of a move over 2 m; yaw ≤ 10°). Re-check older sessions whenever a new one arrives:
   session 2 overturned two of session 1's candidate rules.
6. **Check side effects.** Run the rule on 5–10 tiles from other districts. Count moved and turned objects, and
   check that none lands in a building, in water or on a carriageway area.
7. **Ship.** Add unit tests in `pipeline/tests/` and run the affected tests within 30 s. Commit, then merge one SHA
   per call: `python3 ~/turku3d/tools/codex-loop/merge.py rulesmith <sha> --what "…" --check none`. Register a test
   alias of your own and apply it to the train test tiles and the session tiles:
   `scripts/dev/tile-migrate register --base bulkgen2-3d1445b2 --rebuilds-from bulkgen2-3d1445b2+maa2cf1ce --alias rulesmith-mN`,
   then `apply`. The `wires@2` step costs 3–7 min per surveyed tile, so run tiles in parallel. Decode the served
   products, check them against the edits, and apply a second time to check determinism. Then write
   `READY <step> <sha>` with the per-tile CPU cost into `~/turku3d/loops/lidarwires/notes-rulesmith.md` (the train
   owner runs the city pass). Never repin `tile-service.json`.
8. **Write sidecars.** Write `~/turku3d/edits/<session>/incorporated.json` (EDIT-FORMAT §7.8). Each edit's value is
   `incorporated: …` or `deferred: <why>`.

## Rules in `furniture@1` (`pipeline/turku3d/props/seating.py`)

Called by `coarse/furniture.produce` (generator) and by `migrations/furniture.py`, the last step of the chain.
The rules are tried in order:

1. **`lidar_holds`.** The survey shows seat returns at the register point (within `HOLD` 0.8 m, more than 8
   returns with nothing 1.25–2.4 m above them): the bench stays where it is.
2. **`lidar_snap`.** The survey covers the point but shows no bench there: the bench moves onto the nearest
   unclaimed bench-shaped blob within 6 m. A blob is a cluster of returns 0.3–1.1 m above ground with nothing in
   the clear band above, 1.0–2.6 m long by 0.25–1.5 m wide (5–95 % extents) and dense. Benches and blobs are paired
   by minimum-cost matching, one bench per blob. The axis and back come from a barrier within reach, else from the
   blob's backrest side, else from the old heading.
3. **`barrier_back`.** Within 3 m of a hedge, wall, city wall, retaining wall, fence, railing or building facade,
   the bench turns parallel to it with its back to it, facing the open side. If it overlaps the barrier, it steps
   out to 0.15 m off the face (at most 2 m).
4. **`water_view`.** Otherwise, within 6 m of a shore, the bench faces the water.
5. **Street heading.** Otherwise the old heading from `props/orientation.py` stays.

- **Absence is measured, not acted on** (`bench_lidar_absent_candidate` in the tile's `manifest.furniture.benches`
  counts).
- **Metadata on every prop row:** `yaw_deg`, `orientation`, and `adjusted` when a rule moved it (EDIT-FORMAT §9).
- **Migration mechanics.** Changed bench rows get new POSITION/NORMAL as plain buffer views. GLBs without a BIN
  chunk get a new shared `.bin` instead. A dropped row collapses to zero-area triangles and leaves `objects`. Bench
  collision boxes are spliced into the cells' PRIM section; the meshopt triangle sections stay byte for byte.
  Drawn rows fit the posed model within 5 cm.
- **Cost.** About 0 for a tile without bench rows; 13–200 s CPU on tiles with benches (median about 60 s).

## Incorporated sidecars

| Session | Edits | Incorporated |
|---|---|---|
| `20261103-221501-zd82ho` (point cloud on) | 13 | 5 (bench moves 1, 7, 8, 9, 10) |
| `20261103-222447-fnf9i8` (no point cloud) | 42 | 10 (path benches 26–28, 30–32, 34–37) |

Everything else is `deferred` with its reason: deletes, the square's bench pairs, signs, bike racks, the lamp, the
bin, the lifebuoy and the trees.

## Open items, ranked

1. **`furniture@2`: bike racks, then signs.**
   - Bike racks: 8 of 9 edits turn a rack about 90° (mod 180°) from the `kerb_parallel` heading in
     `props/orientation.py`, on 4 tiles. The likely rule is racks perpendicular to the kerb; check it in other
     districts first.
   - Signs: 7 moves of 2–9 m, mostly from the kerb or carriageway toward the sidewalk or facade, plus 1 delete. They
     need clustering against `GIS_Liikennemerkit` placement and `props.roadside`.
   - Also open: the paired benches on a square (session 2 rows 15–23, which became back-to-back double benches; this
     needs more than one square of evidence), and benches 24/25 (0.5 m nudges).
2. **Pizzeria mission-point clearance (missions).** `PIZZERIA` in `game/src/gameplay/content/locations.ts`
   (77.1, 187.6) is a hand-set point 1.24 m from a register lamp post (77.0, 188.8). Jonni moved the lamp 7 m (row 13
   of session 1). The fix belongs in mission-point placement (a clearance from posts and furniture), not in a lamp
   rule.
3. **Willow deletes (lidartrees).** Session 2 deletes two lidar canopy trees, `tree:t0_2_-1:lidar:589.75:-207.75`
   and `tree:t0_-1_1:lidar:-76.25:383.75`. They are noted to lidartrees in
   `~/turku3d/loops/lidartrees/notes-rulesmith.md`.

## Open question for Jonni

**Should survey absence delete register benches?** In session 1 (point cloud on), you deleted 5 benches where the
survey shows nothing at bench height despite dense ground returns; 3 were tagged "doesn't exist". In session 2
(point cloud off), you kept and paired 5 benches with the same evidence (rows 15, 16, 18, 21, 22). Absence is
common: on t0_3_3 it is 11 of 16 benches, so a delete rule would remove many benches city-wide. It may also catch
benches newer than the survey. The rule is ready to switch on as a `furniture@N` step once you decide. Possible
answers are "yes", "no", or "only with another signal" (for example a second source that also lacks it, or the
ortho).

## Decision from Jonni (2026-11-04)

"Bench deletes: should a bench missing from the LiDAR be deleted?" **No.** Survey absence must not delete register
benches; the orientation is what needs correcting ("I think many benches face the wrong way"). `furniture@1`
already deletes nothing, so it stands; future furniture rules keep register benches and fix their facing.
