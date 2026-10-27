# Play telemetry

Where the player goes and which building facades they see, how close and how large on screen. The pipeline turns the
sessions into a content priority list (`pipeline/turku3d/telemetry/README.md`): generated facades first for the
buildings seen most and closest, and ground, buildings and roads for the areas the player walks into beyond the built
tiles.

## When it runs

`telemetryEnabled` (`guard.ts`) is an environment check, not a feature flag:

- on in play mode when the page is served from `localhost`, `127.0.0.1` or `[::1]`, or with `?telemetry=1`;
- always off on `github.io` (the published Pages build), even with `?telemetry=1`, so that build makes no
  telemetry request;
- off under browser automation (`navigator.webdriver`) unless the URL has `?telemetry=1`, so test suites do not
  write play sessions. QA render mode never records.

## What it records

`Telemetry.tick` runs after each rendered frame but samples only once a second; it fits the walls of a tile the first
time the tile is on screen. A sample over 4 resident tiles (~10 000 walls) costs well under a millisecond once a
second (`__game.telemetry.stats.lastSampleMs` / `maxSampleMs`), far below the 0.3 ms per frame budget. One JSON
line per sample:

```json
{"session": "k3j9x0a1", "t": 1793098800.123, "E": 23459668.1, "N": 6704254.2, "H": 2.31, "yaw": 87.5, "pitch": -6.1,
 "vehicle": null, "speed": 1.42, "tile": "t0_0_0",
 "facades": [{"facade_id": "103295893K_36", "screen_fraction": 0.0812, "distance_m": 14.2, "angle_deg": 21.3}],
 "outside_tiles": {"stand": "t0_2_0", "ahead": ["t0_3_0", "t0_3_-1"]}}
```

| Field | Meaning |
| --- | --- |
| `session` | random id per page load |
| `t` | unix time, seconds |
| `E`, `N`, `H` | player feet (car body when driving), EPSG:3877 + N2000 |
| `yaw`, `pitch` | camera compass heading and pitch, degrees |
| `vehicle` | driven vehicle id (`player` for the own car), `null` on foot |
| `speed` | horizontal speed, m/s |
| `tile` | level-0 visual tile id at the player (`t0_<i>_<j>`, `pipeline/turku3d/config.py` grid; negative indices allowed) |
| `facades` | at most 40 walls, largest first, with `screen_fraction > 0.0005` and `distance_m < 150` |
| `outside_tiles` | only when the player's tile is not a built level-0 tile: that tile and the next two along the camera heading |

A wall is every glTF object range with `building_id` and `facade_index` in a resident level-0 tile (bound to a
facade map or not; `facade_id` is `<building_id>_<facade_index>`, the approval manifest key). `walls.ts` fits it as
a vertical rectangle from its vertices and the producer's outward `normal`. `screen_fraction` is the rectangle
projected to the screen, clipped at the near plane and to the viewport, as a fraction of the viewport area; occlusion
is ignored. Walls whose back faces the camera are skipped. `distance_m` is from the camera to the nearest point of the
wall; `angle_deg` is between the wall normal and the direction from the wall centre to the camera (0 = head-on).

## Transport

Samples are POSTed every 5 s as JSON lines (`Content-Type: application/x-ndjson`) to `/telemetry` on the serving
origin, and sent with `navigator.sendBeacon` when the page is hidden. `tools/dev/serve-dist.mjs` (`make play`,
`make game-preview`) appends them to `$TURKU3D_WORK/telemetry/session-<YYYYMMDD>.jsonl`; the main checkout writes
`~/turku3d/data/telemetry/`. Failed sends are dropped and counted (`stats.failed`); the Vite dev server has no
endpoint.

```sh
cd game && npx vitest run tests/unit/telemetry.test.ts     # guard, grid, wall fit and projection, batching
node --test tools/dev/serve-dist.test.mjs                  # endpoint (from the repository root)
```
