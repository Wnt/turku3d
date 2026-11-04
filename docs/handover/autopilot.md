# Handover: autopilot (A-to-B driving, mission hook, bridge and underpass sweep, multiplayer test)

2026-11-04, native Claude agent, branch `local/autopilot`, merged into `local/loop-integration` and deployed to
city.lab. Tool docs and pass/fail limits: `tools/drivetest/README.md`.

## Autopilot (`game/src/gameplay/autopilot/`)

```ts
const trip = __game.autopilot.goTo({ x, z }, { mode: 'drive' | 'walk', lane?, path?, speed?, style?, pace?, timeout?, takeover? });
trip.progress;          // s, length, fraction, speed, target, lateral, recoveries, replans, elapsed
await trip.done;        // { state: 'arrived' | 'failed' | 'cancelled', reason, progress }
trip.cancel();
__game.autopilot.followLane({ tile, id });   // one lane exactly, extended 70 m before and 40 m after
```

- **Input path.** `Input.driver` (`input/index.ts`) gets the merged device actions every frame, and the autopilot
  overwrites throttle, brake, steer and handbrake, or the stick, sprint, walk and jump.
  - Physics, collisions and multiplayer pose sync therefore see a player.
  - Nothing teleports or writes physics state.
  - A human stick or pedal input over 0.35 cancels the trip (`takeover: false` for cutscenes).
- **Routing** (`lanes.ts`). The ambient traffic's lanes (each tile's own roads.json, `trafficRoads(json, true)`
  keeps service roads and yards too), with the autopilot's own junction graph:
  - every lane leaving a junction, keyed by node position so seam junctions join, with no U-turns except at dead
    ends;
  - a seam end that the network left unjoined takes the other tile's start within 2.5 m;
  - any lane start within 8 m heading the same way continues a lane, at a 15 m penalty;
  - multi-source and multi-goal A*: lanes near the car or target, other heading or far side at a cost.
  - On 36 centre tiles 2768 of 3204 lanes form one component, and routes take 4–11 ms.
  - The traffic flow's own movements are not used. They fold crossing splinters into the straightest way through,
    which left the graph in closed one-way pockets of about 160 lanes.
- **Following** (`follow.ts`):
  - pure pursuit from the rear axle (lookahead 4–18 m) plus Stanley-like cross-track damping;
  - a speed plan from curvature (2.6 m/s² lateral on `normal`), the lane limit and the stop;
  - stuck recovery: reverse with opposite lock, then re-plan;
  - on the real Rapier car (dev tiles) it tracks a straight within 0.35 m and a 25 m curve within 0.9 m
    (`tests/unit/autopilot.test.ts`).
- **Walking:** the foot network through the street router; the stick is camera-relative.
- **Multiplayer:** each client drives only its own player, and the lane graph is a pure function of the tile data.

## Mission hook

- `gameplay/missions/driveMeThere.ts` drives (or walks) to the active mission target, the one the minimap routes
  to. It stops if the target changes.
- Triggers: `__game.driveMeThere()`, `?autopilot=target` and `?autopilot=x,z[,walk|drive][,kmh]`.
- The trip feeds `CoarseTiles.setRouteHint`.

## Bridge, tunnel and underpass sweep

- **Run:** Node on the real physics, sharded and resumable. Run it on osgallery-dev
  (`~/turku3d-local/autopilot/{repo,sweep}`); the browser pass makes the screenshots.
- **Version:** tile pin `bulkgen2-3d1445b2+maa2cf1ce`.
- **Report:** `~/turku3d/loops/autopilot/sweep-v2/report.md` (local, not in git).
- **Coverage:** 312 structures were found (60 bridges, 119 tunnels incl. building passages, 133 underpasses incl.
  road under rail). 309 were driven, 747 lanes in all, and 607 lanes reached their structure.
- **Pass/fail of the measured lanes:**
  - bridges 4 pass / 10 warn / 143 fail;
  - tunnels 2 / 3 / 166;
  - underpasses 25 / 41 / 213.
- **Cross-checks:**
  - On flat dev tiles the trace reads < 0.001 m/s².
  - The car's own trace corroborates 92 % of the ground jumps over 30 cm and 80 % of the 4–30 cm steps (a jolt, wheel
    lift, impact or stop within 5 m).
  - The browser pass on 8 lanes agrees with Node on failure and cause.
- **Geometry bugs** (one row per structure, report §"Geometry bugs, ranked"):
  - 74 holes or fall-throughs (worst: service tunnels at E 23459215 N 6704321, a 25 m fall; the trunk road under the
    rail bundle at E 23458463 N 6704667, a 2 m surface jump);
  - 72 obstacles on the lane (the car stuck or hitting something: Pahaniemensilta, Aninkaistensilta,
    Lemminkäisensilta, Hämeenramppi);
  - 52 wheel lifts or hard jolts (Martinsilta deck end 8.4 m/s suspension jolt, Uhrilähteenkatu, Skarppakullantie);
  - 17 steps of 4–30 cm;
  - 5 rough.
- **Lane data:** 135 lanes have a roads.json height more than 0.5 m off the driven surface, and 56 lanes run over
  kerb, sidewalk or verge for more than 20 % of their wheel tracks.
- Not fixed here, as briefed.

## Multiplayer test

- **Harness:** `tools/drivetest/mp.mjs`, with browser clients through a host lease (`win-chrome.sh` for the Windows
  host) plus Node bots (`game/tests/drive/mpbot.ts`, protocol v2, the same follower on real physics, others as
  kinematic bodies).
- **Results:** see the final report of the session; `~/turku3d/loops/autopilot/mp-*/mp-result.json`.

## Open

1. **Approach stops.** 140 lanes were not covered because the car stopped on the approach: junction connectors
   through kerbs, or lanes misaligned with the built road. Better connectors (from the kerb geometry), or start the
   car closer to the structure.
2. **Dedupe.** Underpasses of one road under several parallel tracks are separate structures. The report collapses
   them, `structures.ts` does not.
3. **Screenshots.** The browser pass needs a free lease; it ran for the v1 worst points only.
