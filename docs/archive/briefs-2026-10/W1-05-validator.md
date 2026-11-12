# W1-05: Validator on real tiles: seams, buildings, curbs, stairs, car (Phase 1 task 1.11)

Read `docs/briefs/COMMON.md` and `AGENTS.md`. You own `tools/validator/`.

## Goal

The Node Rapier validator becomes the physics acceptance gate for every built tile. It already passes on synthetic
fixtures, but it skips buildings, curbs and stairs on the stub tiles and has no seam or car checks.

## Deliverables

1. **Seam identity check**: vertices on shared collision-tile edges must be bitwise identical in both tiles, with no
   T-junction gaps. Report the offending edges with coordinates.
2. **Car checks** with the game's own vehicle code: import it, do not re-implement it. If it is not importable, fix the
   game module so it is.
   - Drive scripted lines across every curb segment at 3/6/10 m/s and report the pitch spike and speed loss. Thresholds
     from ARCHITECTURE 1.14 apply once W1-07's curb-strike module lands; until then, report only.
   - Steps above 25 cm block the car at under 3 m/s.
   - At 30 m/s the car never tunnels into a building or through a quay wall.
3. **Real-tile runs**: walkers against building footprints, curb climbing along every `curb` line, a climb test on
   every stair flight, hole raycasts, and a never-below-ground check, using footprints, curb lines and stair lists
   taken from the layer outputs.
4. **Report**: JSON with per-check pass/fail, plus an HTML page with a top-down map of the failures over the ortho.
   `make tile-validate` exits non-zero on failure.
5. **Speed**: parallelise across collision tiles with worker threads. A full AOI run should take under 60 s; report
   the timing.

## Acceptance

- Planted defects in fixtures are each caught: a seam gap, a hole, a building gap, an unclimbable 30 cm curb, and a
  car tunnelling through a 0.1 m wall.
- On the current real AOI build, the report lists the real failures, if any. Do not "fix" them by loosening
  thresholds: list them in Follow-ups for the layer owners.
