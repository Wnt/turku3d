# Multiplayer client

`config.ts` chooses the local server at `ws://127.0.0.1:7700/` for a `localhost` page and the public server for other hosts. `?mp=ws://localhost:<port>/` overrides the endpoint for isolated tests. The server only accepts pages with the allowed origins in [PROTOCOL.md](../../../server/PROTOCOL.md).

`MultiplayerClient` owns the WebSocket, session token in `sessionStorage`, reconnect backoff, roster and score snapshots. `sendPose` limits traffic to less than 20 messages per second and emits a one-second stationary heartbeat. A delivery takes the place of the next pose in the same rate budget. Play sessions construct it when Multiplayer is selected and dispose it when the session ends.

From the repository root, with the sandbox environment sourced:

```sh
npm test --prefix game -- --run tests/unit/net-client.test.ts
node server/index.js
```
