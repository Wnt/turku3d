# Play telemetry

Where the player goes and which building facades they see, how close and how large on screen. The pipeline turns the
sessions into a content priority list (`pipeline/turku3d/telemetry/README.md`): generated facades first for the
buildings seen most and closest, and ground, buildings and roads for the areas the player walks into beyond the built
tiles.

## When it runs

`telemetryEnabled` (`guard.ts`, over `core/localOnly.ts` `localFeature`, which the developer tools share) is an
environment check, not a feature flag:

- on in play mode when the page is served from `localhost`, `127.0.0.1` or `[::1]`, or with `?telemetry=1`;
- always off on `github.io` (the published Pages build), even with `?telemetry=1`, so that build makes no
  telemetry request;
- off with `?telemetry=0` (an ad-hoc probe against the user's localhost service that should not count as play).
  QA render mode never records.

Browser automation does not turn it off. The player may be playing in a Chrome window that a tool launched
(`chrome-devtools-mcp` starts Chrome with `--enable-automation`, so `navigator.webdriver` is true): an earlier
webdriver rule silently dropped every session played there (2026-10-27, from 15:01 on). Browser checks record too,
into the telemetry directory of their own sandbox's server (`vite preview`, the dev server or `serve-dist`), never
into the data root that the content loop reads.

## What it records

`Telemetry.tick` runs after each rendered frame (not on the start screen or while paused). It fits the walls of at
most one newly visible tile per frame and samples once a second. Measured in the sandbox play session (M1 Max, WebGPU,
4 resident L0 tiles, 6 384 walls): fitting takes at most 2 ms once per tile, a sample 0.2–0.6 ms once a second (the
very first sample, before the JIT warms up, took 6 ms), i.e. under 0.01 ms per frame on average.
`__game.telemetry.stats` reports `lastSampleMs`, `maxSampleMs`, `maxFitMs`, `walls`, `samples`, `sent` and `failed`.
One JSON line per sample:

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
`~/turku3d/data/telemetry/`. The Vite dev server (the localhost game, `tools/dev/localhost-game`) and `vite preview`
(the browser checks) do the same through `tools/dev/localhost/vitePlugin.mjs`, into `$TURKU3D_TELEMETRY_DIR` or
`$TURKU3D_WORK/telemetry`. Failed sends are dropped and counted (`stats.failed`).

```sh
cd game && npx vitest run tests/unit/telemetry.test.ts     # guard, grid, wall fit and projection, batching
node --test tools/dev/serve-dist.test.mjs tools/dev/localhost/localhost.test.mjs   # endpoints (repository root)
# A new game on the dev server under automation posts its first batch within 10 s (Chrome, WebGPU):
cd game && ../scripts/dev/host_slots.py browser -- node_modules/.bin/playwright test -c playwright.hotswap.config.ts tests/e2e-hotswap/telemetry.spec.ts
```
