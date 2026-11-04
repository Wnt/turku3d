# Drive tests: bridges, tunnels, underpasses and multiplayer

The autopilot (`game/src/gameplay/autopilot/`) drives lanes the way a player would: throttle, brake and steer into
the normal vehicle physics. These tools use it to drive every bridge, tunnel and underpass lane in the city, and to
drive several multiplayer clients at once.

## Sweep (Node, real physics, no browser)

```sh
D=~/turku3d/loops/autopilot/sweep              # any folder; tile-service.json there pins the tile version
cp ~/turku3d/loops/integrator/tile-service.json $D/
bash pipeline/env/run.sh python tools/drivetest/osm_structures.py $D/osm-structures.json   # OSM names, rail bridges
node tools/drivetest/fetch.mjs $D $D/tile-service.json      # finished tiles' roads (ready.json probes, never a build)
cd game && DRIVE_DIR=$D DRIVE_SHARD=0/4 npx vitest run -c vitest.drive.config.ts   # one shard; run k=0..3 in parallel
DRIVE_ONLY=Auransilta,Myllysilta ...           # only these structures (id or OSM name); DRIVE_LIMIT=N lanes
node tools/drivetest/report.mjs $D             # $D/report.md, $D/report.json
```

- **Structures:** `game/src/qa/drivetest/structures.ts`.
  - Bridges and tunnels are each tile's roads.json car rows with the v2 `BRIDGE`/`TUNNEL` flags, grouped by OSM way.
  - Underpasses are car rows that cross, in plan, a deck at least 2.5 m above them. The deck can be a road or path
    bridge from roads.json, or an OSM rail or cable bridge.
- **Lanes:** the autopilot router's lanes that run at least 12 m on the structure, at its level, in their own
  direction. Each lane is driven from 90 m before it to 50 m after it.
- **Physics:** each lane is driven by `physics/world.ts` (the worker's own code) over the pinned version's
  collision cells, at the posted limit (capped at 80 km/h).
- **Resuming:** results append to `$D/results/*.jsonl`, and a rerun skips the lanes already there. Cell, roads and
  manifest downloads are kept in `$D/tiles`.

## Pass/fail (`game/src/qa/drivetest/trace.ts LIMITS`)

| Check | Warn | Fail | Why |
|---|---|---|---|
| Wheel off the ground | – | 50 ms | A real deck never unloads a wheel; one 17 ms step is solver noise |
| Vertical chassis acceleration (83 ms mean) | 1.5 m/s² | 3.0 m/s² | 0.3 g is a speed hump at 30 km/h. A sag or crest on a good deck stays near 0.1 g. Flat ground reads < 0.001 |
| Vertical jerk (over 50 ms) | 50 m/s³ | 100 m/s³ | 0.5 g within 50 ms is a hit, not a slope |
| Pitch or roll rate | 0.2 rad/s | 0.35 rad/s | A 6 % grade change over 10 m at 50 km/h is 0.08 rad/s. 20°/s is a lurch the chase camera shows |
| Ground step under a wheel track (ray profile every 10 cm) | 2.5 cm | 4 cm | Expansion joints stand < 2 cm proud. The bump detector and kerb rumble start at 5 cm |
| Suspension jolt | 0.5 m/s | 1.0 m/s | The haptics jolt floor |
| Collision speed change | – | 0.5 m/s | The haptics collision floor: the car touched a railing, kerb or wall |
| Kerb strike, fall-through (> 1.5 m below the lane), stuck, left the lane | – | any | – |

The window covers the structure plus 15 m on each side, so its joints and approach slabs count.

## Browser pass (screenshots, cross-check)

```sh
node tools/drivetest/browser.mjs $D --base http://127.0.0.1:5480/ --failures 20   # or --keys k1,k2 / --sample N
```

The browser pass runs in the real game: headless Chrome with WebGPU, one browser through chrome-guard
(`autopilot`), `traffic=0`.

- **Driving:** each lane is driven with `autopilot.followLane` through the input path, and its trace is taken from
  the worker's snapshots.
- **Screenshots:** the car is put 18 m before the worst point, and the pass saves `$D/shots/<key>.png`.
- **Comparison:** `$D/browser.jsonl` puts the Node and browser results side by side.

## Multiplayer

```sh
node tools/drivetest/mp.mjs OUT --clients 6 --base http://localhost:5480/ --port 5482 --seconds 150
```

The test starts its own multiplayer server (temporary scores, no producer pings) and runs N headless clients in one
Chrome. The base URL must be `localhost`, because the server accepts only localhost and Pages origins.

1. Each client drives out to one of four points around the spawn, then across to the opposite one, so the routes
   cross.
2. A quarter of the way in, one client leaves. Past halfway, a new one joins.
3. The test reports:
   - divergence: each remote view against that player's own pose at the same wall time;
   - the best-fit display delay;
   - leave and join propagation;
   - car contacts near another player;
   - trip outcomes and frame times.
