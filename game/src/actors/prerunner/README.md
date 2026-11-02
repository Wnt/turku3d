# KORPI 440

Original fictional Turku-built unlimited off-road racer. The tube frame, swept body panels,
eight-stack V8, beadlocks, terrain tread, safety webbing and fictional livery are authored
here; no vehicle mesh, texture, brand or livery is taken from a commercial game.

`TruckVisual` retains the existing `truck` physics/network kind, +X forward, +Y up,
wheel order FL/FR/RL/RR. All dimensions come from `TRUCK` in physics/constants.ts.
`model.ts` caches immutable meshes shared by every local and remote actor.
`rig.ts` applies authoritative wheel poses to separate steering/spin pivots and four
instanced running-gear draws. Remote scene clones bind their own instance matrices.
Springs vary pitch without scaling the wire cross-section; damper barrels stay fixed
while their chromed shafts slide. The raycast controller supplies a vertical wheel path;
rigid wishbones follow an approximate lateral arc, connected to that physical hub by
an upright. It is a visual rig, not a second physics solver.

Close geometry has about 28.5k triangles plus the articulated rig; at 35m it switches
to six vertex-colour draws, and at 90m approximately 5.2k triangles. Small suspension
parts are removed at 35m. LOD changes use 10% hysteresis. These are mesh budgets,
not a claim of measured in-city FPS.

The generator's existing 1/1000 oversized truck bays supply safe rare spawn locations.
`world/prerunnerSchedule.ts` gives each one an appearance window for one of four
shared-clock days. Claimed and rolling vehicles remain after the window closes.

For close-up inspection, open `/prerunner-lab.html` on the development server.
`window.__dev.prerunner` exposes `pose`, `orbit`, `articulate`, `drive`, `stopDrive`,
`driveStats` and `snapshot`; model edits use HMR without reloading the studio.
`drive()` uses the actual Rapier Vehicle implementation on a kicker/kerb/rock fixture.
It does not animate the chassis along a prerecorded path.

Checks:

```sh
npm --prefix game run typecheck
npm --prefix game test -- tests/unit/prerunner-mesh.test.ts tests/unit/prerunner-course.test.ts tests/unit/prerunner-schedule.test.ts tests/unit/parked-occupancy.test.ts tests/unit/vehicle-fleet.test.ts
```
