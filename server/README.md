# Multiplayer server

Node 24, ES modules, one pinned dependency (`ws`). Presence and delivery scores are
server-owned; physics stays in the later game client. The exact client contract is
[PROTOCOL.md](PROTOCOL.md). World coordinates are defined by
[the architecture](../docs/ARCHITECTURE.md#d3-coordinate-system-and-origin).

From the repository root:

```sh
npm ci --prefix server --ignore-scripts
npm test --prefix server
node server/index.js
```

The server binds only `127.0.0.1:7700`. For an isolated test, set `TURKU_MP_PORT=0`
(ephemeral port) and `TURKU_MP_SCORES=/path/to/test/scores`. Production defaults to
`server/data/scores/`, ignored by Git. Completed hour files survive restart;
unfinished scores and sessions are in memory and do not. Files older than seven
days are pruned daily. The server checks delivery amounts and duplicate IDs, but
has no authoritative order lifecycle to prove an actual trip was completed.

```sh
curl -fsS http://127.0.0.1:7700/healthz
curl -fsS http://127.0.0.1:7700/live.json
curl -fsS http://127.0.0.1:7700/scores/index.json
```

Tests use small synthetic data, temporary directories and ephemeral loopback ports;
they never read city data or touch a running service. The economy test executes the
existing game's TypeScript through Node's built-in type stripping and checks its
mission payout expression. Spawn derivation and coordinate conversion are recorded
in `spawns.json`; points have 3 m spacing and were checked against the published
building footprints. Client ground snapping and obstacle handling are still needed.
