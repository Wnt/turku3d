# Route guidance across streamed tile boundaries

The old router only knew the resident draft graphs. With a representative 1 km resident window around the office, 10 of the 12 landmark destinations had no route. It also replanned every five metres, and the expanded map used a fixed 650 m view.

Guidance now retains its planned corridor and trims the travelled prefix. It replans after a full second more than 25 m off that corridor, with at least two seconds between plans. New streamed graphs retry failed plans while stationary, but preserve valid routes. Forward progress is bounded by actual movement so a nearby return street cannot consume an untravelled loop. Seam endpoint matching searches neighbouring quantisation cells within 0.11 m. The expanded north-up map fits every remaining route vertex and the player, including markers and the collision-area overlay.

The companion long-deliveries implementation owns `/coarse/navigation.json`, the persistent city graph, and forwarding remaining routes to `CoarseTiles.setRouteHint`. Draft arrival/eviction must never replace this graph or redraw its static map layer.

## Localhost guidance replay

`game/scripts/probe-route-guidance.mjs` imports the production router, tracker and minimap through localhost Vite, using real cached OSM metadata. It loads the city graph through the production `createGameplay` endpoint consumer, confirms that draft arrival/eviction preserves the graph object, and samples routes from the office at no more than seven metre intervals and invalidates at every L0 crossing. This verifies guidance independently of vehicle physics. Every route kept exactly one plan; none disappeared or increased its remaining distance. The expanded Forum Marinum and Kuralan Kylämäki maps were visually inspected and include their complete routes.

| Destination | Route m | L0 crossings | Samples | Resident-only route |
|---|---:|---:|---:|---|
| Aboa Vetus Ars Nova | 608 | 5 | 116 | yes |
| Forum Marinum | 2564 | 12 | 427 | no |
| Kakola Spa | 2855 | 13 | 499 | no |
| Kupittaan urheiluhalli | 2627 | 16 | 454 | no |
| Kuralan Kylämäki | 4442 | 25 | 737 | no |
| Luostarinmäen käsityöläismuseo | 710 | 6 | 130 | yes |
| Sibelius-museo | 1263 | 10 | 234 | no |
| Turun biologinen museo | 1362 | 9 | 236 | no |
| Turun linna | 2572 | 13 | 425 | no |
| Turun tuomiokirkko | 1107 | 8 | 204 | no |
| Vanha rautatieasema, Ratapihankatu 37 | 1215 | 6 | 218 | no |
| Wäinö Aaltosen museo | 1233 | 6 | 215 | no |

The probe graph has 46,042 nodes and 61,044 edges. Raw metadata, screenshots and JSON evidence stay in the agent sandbox under `$TURKU3D_WORK/reports/route-guidance/`.

## Reproduce

```sh
source ../sandbox.env
npm --prefix game run dev -- --host 127.0.0.1
# In another shell, after preparing navigation.json from the endpoint:
python3 scripts/dev/host_slots.py browser -- node game/scripts/probe-route-guidance.mjs \
  "http://localhost:$TURKU3D_PORT_BASE" "$TURKU3D_WORK/reports/route-guidance/navigation.json"
npm --prefix game test -- tests/unit/router.test.ts tests/unit/minimap.test.ts
npm --prefix game run typecheck
```

The replay accepts a saved endpoint response as its second argument, or fetches `/coarse/navigation.json` when that argument is omitted. Unit cases cover seam points on opposite rounding bins, separate nearby streets, route progress without replanning, transient versus sustained deviation, mode/target changes, failed plans becoming available while stationary, and whole-route map fitting, and progress beside a nearby return leg. All 17 focused cases pass.

## Delivery

Initial guidance slice `5263e4e`: selected checks passed in 2.613 s; Pages package/upload took 11.157 s; all three curl smoke checks passed in 0.298 s. The frozen post-deploy mission/draft/router/minimap selection passed in 2.662 s.

## Integration and physical probes

The public navigation response was fetched successfully after a cold-cache request exceeded the five-second curl budget; the subsequent cached request completed in under one second. It contains the same 46,042 nodes, 61,044 edges and 12 landmarks used in the localhost evidence. The integrated endpoint consumer replay passed all 12 routes after `long-deliveries` landed.

`game/scripts/probe-route-drive.mjs` is an assisted physical diagnostic using the real hosted drafts through the sandbox localhost game. It sets an explicit map target, enters the car and drives with actual input/physics; it does not build an area or teleport after entering the car. Its simple centreline follower is not evidence of a clean player delivery.

The Forum Marinum run advanced about 1.1 km across real tile boundaries with continuous guidance and zero recovery resets, then stopped against a sign on a traffic island at engine x=-759.6, z=764.7, y=5.66 (`t0_-4_3`). The route remained present with 1,454 m left. Screenshot and samples are under `$TURKU3D_WORK/reports/route-drive/forum-marinum/`. The obstacle requires steering around the island; it is not a disconnected road seam.

An initial Kurala run was invalidated by source HMR during the loop-progress fix and is excluded. The replacement ran against frozen source on port base+4. It advanced about 1.2 km through seven sampled L0 cells, with guidance continuously present and zero resets/errors, before its centreline follower hit another traffic-island sign at x=1132.3, z=-222.8, y=14.93 (`t0_4_-1`), with 3,257 m remaining. Samples and screenshot are under `$TURKU3D_WORK/reports/route-drive/kuralan-kylamaki/`. Neither partial assisted drive is claimed as a completed delivery.

The companion long-deliveries agent completed a separate 2,688.6 m physical Kupittaan urheiluhalli delivery, walking the final 20 m, with reverse assistance but no teleports after car entry, recovery resets or game errors.

Physical diagnostic commands (use a frozen localhost source to avoid HMR invalidating its explicit target/input hooks):

```sh
ROUTE_DRIVE_PORT=$((TURKU3D_PORT_BASE + 4)) python3 scripts/dev/host_slots.py browser -- \
  node game/scripts/probe-route-drive.mjs "$TURKU3D_WORK/reports/route-guidance/navigation.json" forum-marinum 180
ROUTE_DRIVE_PORT=$((TURKU3D_PORT_BASE + 4)) python3 scripts/dev/host_slots.py browser -- \
  node game/scripts/probe-route-drive.mjs "$TURKU3D_WORK/reports/route-guidance/navigation.json" kuralan-kylamaki 360
```

The remaining route-specific follow-up is a human or obstacle-aware assisted full drive around the two traffic islands. The routing network and route line remain continuous at both recorded stops.
