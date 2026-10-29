# Multiplayer client

`config.ts` chooses the local server at `ws://127.0.0.1:7700/` for a `localhost` page and the public server for other hosts. `?mp=ws://localhost:<port>/` overrides the endpoint for isolated tests. The server only accepts pages with the allowed origins in [PROTOCOL.md](../../../server/PROTOCOL.md).

`MultiplayerClient` owns the WebSocket, session token in `sessionStorage`, reconnect backoff, roster and score snapshots. `sendPose` limits traffic to less than 20 messages per second and emits a one-second stationary heartbeat. Vehicle seat commands, deliveries and pole/fine events all replace poses in one shared 55 ms send queue. Play sessions construct it when Multiplayer is selected and dispose it when the session ends.

`RemotePlayers` keeps about 100 ms of pose history and extrapolates at most 200 ms. Network H is at character feet in foot mode and 0.6 m below the chassis centre in car, scooter and truck modes; `height.ts` maps it to visual roots, while the worker places the foot capsule above H and vehicle chassis at the visual root. `identity.ts` sends the exact driven model and colour key from the manifest, so a remote parked car shares the local car's geometry and paint. Nearby people use the full skinned character model; beyond 6 m they use a single-draw static character silhouette. Vehicles relay the physics chassis quaternion and wheel state; remotes slerp heading, pitch and roll and interpolate the wheels in chassis space. Parked meshes retain their authored local-axis correction. Legacy yaw-only peers remain supported. Nicknames remain visible above actors. Walking-only kinematic proxies are placed in loaded collision tiles; vehicle contact uses bounded server impulses, never kinematic vehicle pushes; all remote positions remain on the minimap. A local car's collision impulse drives its existing impact sound and, in multiplayer, a screen flash; the controller rumble is input/haptics.ts's in every mode. `Scoreboard` renders the live podium and self, a final-ten-second countdown, and a full start/pause menu page with Finnish clock-hour archives. Only delivery drop-offs produce score claims.

From the repository root, with the sandbox environment sourced:

```sh
npm test --prefix game -- --run tests/unit/net-client.test.ts
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --base main --specs mp.spec.ts --project webgpu
cd game && TILE_DIST="$TURKU3D_DATA/dist/tiles" python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test -c playwright.mp-real.config.ts
```

The Chrome spec starts its own local server on an ephemeral port, uses two pages, and writes `reports/fix-mp-remote-height/page-a.png`, `page-b.png` and `metrics.json` under `$TURKU3D_WORK`. The real-tile config mounts `$TURKU3D_DATA/dist/tiles` read-only and writes `real-tile-heights.json` and `real-tile-remote-car.png` in that report directory.

`sharedVehicles.ts` maps registry IDs to local physics actors and transfers full poses
under server-issued epochs. A starter is registered when moved/occupied; purchased
scooters/trucks and moved draft cars also join the registry. `ParkedVehicles.setShared`
suppresses claimed slots globally. Registry rendering outlives the driver's roster entry.
A foreign acquired vehicle uses the shared renderer with its local physics pose; its
source slot stays suppressed. Late join/reconnect restores owned quaternion/velocity.
The server sends nearby changed poses only, with metadata upserts and shallow motion
patches. Resting snapshots are dirty-filtered; the server does not echo owner's motion.
`vehicle_impulse` deduplication precedes a clamped worker impulse at the chassis centre.
Empty target ownership transfers to the hitter, so paused/distant owners cannot freeze it.

Driving is retained through registry refreshes and reconnects. `SharedVehicles`
reclaims the seat after welcome and preserves the local body until an explicit
`seat_taken` acquisition denial survives three seconds of grace. A forced exit
uses the current registry chassis pose, including when the local body is missing.
Connected pose stalls never revoke seats; disconnected holders have a five-minute
reservation. A restart that loses server sessions re-registers the driven vehicle
from its current local pose. Late register/reclaim replies never release that seat.
On a full page reload, the retained seat determines the collision preload position.
Multiplayer placement then restores owned registry bodies before unpausing and
re-enters that seat, independently of the local solo save. Borrowed cars whose
source tile is absent use the shared renderer rather than an absent native visual.

Carried pizzas are now visible on remote walking characters. Welcome's server time
aligns the day cycle (explicit developer/QA time overrides stay explicit).

Focused headless checks (no browser required for vehicle-sync delivery):

```sh
npm --prefix game test -- tests/unit/net-client.test.ts tests/unit/shared-vehicles.test.ts tests/unit/shared-remotes.test.ts tests/unit/parked-streaming.test.ts
npm --prefix server test
node server/tools/probe-vehicles.mjs
```

The hourly leaderboard displays **Earned** cash from validated delivery pay, tips and
bonuses. Wallet fines and purchases do not reduce it. Ties use deliveries, then the
earliest result. Legacy archives retain their recorded net totals and ranks. Reload
open multiplayer tabs after this score schema deployment.
