# Multiplayer client

`config.ts` chooses the local server at `ws://127.0.0.1:7700/` for a `localhost` page and the public server for other hosts. `?mp=ws://localhost:<port>/` overrides the endpoint for isolated tests. The server only accepts pages with the allowed origins in [PROTOCOL.md](../../../server/PROTOCOL.md).

`MultiplayerClient` owns the WebSocket, session token in `sessionStorage`, reconnect backoff, roster and score snapshots. `sendPose` limits traffic to less than 20 messages per second and emits a one-second stationary heartbeat. A delivery takes the place of the next pose in the same rate budget. Play sessions construct it when Multiplayer is selected and dispose it when the session ends.

`RemotePlayers` keeps about 100 ms of pose history and extrapolates at most 200 ms. Network H is at character feet in foot mode and 0.6 m below the chassis centre in car, scooter and truck modes; `height.ts` maps it to visual roots, while the worker places the foot capsule above H and vehicle chassis at the visual root. `identity.ts` sends the exact driven model and colour key from the manifest, so a remote parked car shares the local car's geometry and paint. Nearby people use the full skinned character model; beyond 6 m they use a single-draw static character silhouette. Cars, scooters and trophy trucks use their game models (the truck's wheels at rest). Nicknames remain visible above actors. Kinematic collision proxies are placed only in loaded collision tiles; all remote positions remain on the minimap. A local car's collision impulse drives its existing impact sound and, in multiplayer, a screen flash; the controller rumble is input/haptics.ts's in every mode. `Scoreboard` renders the live podium and self, a final-ten-second countdown, and a full start/pause menu page with Finnish clock-hour archives. Only delivery drop-offs produce score claims.

From the repository root, with the sandbox environment sourced:

```sh
npm test --prefix game -- --run tests/unit/net-client.test.ts
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --base main --specs mp.spec.ts --project webgpu
cd game && TILE_DIST="$TURKU3D_DATA/dist/tiles" python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test -c playwright.mp-real.config.ts
```

The Chrome spec starts its own local server on an ephemeral port, uses two pages, and writes `reports/fix-mp-remote-height/page-a.png`, `page-b.png` and `metrics.json` under `$TURKU3D_WORK`. The real-tile config mounts `$TURKU3D_DATA/dist/tiles` read-only and writes `real-tile-heights.json` and `real-tile-remote-car.png` in that report directory.
