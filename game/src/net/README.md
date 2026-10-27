# Multiplayer client

`config.ts` chooses the local server at `ws://127.0.0.1:7700/` for a `localhost` page and the public server for other hosts. `?mp=ws://localhost:<port>/` overrides the endpoint for isolated tests. The server only accepts pages with the allowed origins in [PROTOCOL.md](../../../server/PROTOCOL.md).

`MultiplayerClient` owns the WebSocket, session token in `sessionStorage`, reconnect backoff, roster and score snapshots. `sendPose` limits traffic to less than 20 messages per second and emits a one-second stationary heartbeat. A delivery takes the place of the next pose in the same rate budget. Play sessions construct it when Multiplayer is selected and dispose it when the session ends.

`RemotePlayers` keeps about 100 ms of pose history and extrapolates at most 200 ms. Nearby people use the full skinned character model; beyond 6 m they use a single-draw static character silhouette. Cars and scooters use their game models. Nicknames remain visible above actors. Kinematic collision proxies are placed only in loaded collision tiles; all remote positions remain on the minimap. A local car's Rapier contact impulses drive its existing impact sound, plus a screen flash and controller rumble. `Scoreboard` renders the live podium and self, a final-ten-second countdown, and a full start/pause menu page with Finnish clock-hour archives. Only delivery drop-offs produce score claims.

From the repository root, with the sandbox environment sourced:

```sh
npm test --prefix game -- --run tests/unit/net-client.test.ts
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --base main --specs mp.spec.ts --project webgpu
```

The Chrome spec starts its own local server on sandbox port `+3`, uses two pages, and writes `reports/mp-client/page-a.png`, `page-b.png` and `metrics.json` under `$TURKU3D_WORK`.
