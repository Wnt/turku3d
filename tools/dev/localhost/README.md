# The localhost game (`tools/dev/localhost-game`)

The localhost game runs **main, hot**: a landing reaches the open tab within seconds, and the integrator's verdict
arrives later on a badge. `live` and GitHub Pages get every batch that builds (its advisory test results ride
along); only localhost shows builds the integrator has not built yet.

```bash
tools/dev/localhost-game                 # main: http://127.0.0.1:5171/
tools/dev/localhost-game --port 5211 --worktree "$TURKU3D_WORK/lh/repo" --no-build   # a sandbox instance
curl -s localhost:5171/__localhost/status          # {service, commit, verification, tiles, coarse}
tail -f "$TURKU3D_WORK/localhost-game/events.jsonl" # checkouts, verdicts, tile releases, coarse states, page loads, hot swaps
tail -f "$TURKU3D_WORK/localhost-game/coarse.log"   # the coarse tile service's output
node --test tools/dev/localhost/localhost.test.mjs  # verdicts, routes, the follow loop (synthetic git repo), the coarse supervisor (fake service)
cd game && ../scripts/dev/host_slots.py browser -- node_modules/.bin/playwright test -c playwright.hotswap.config.ts
```

It replaces the `serve-dist.mjs` invocation on port 5171 and keeps its routes (`POST /telemetry` into
`$TURKU3D_WORK/telemetry`, `/coarse/*` to the coarse tile service on port base + 5). It also runs that coarse tile
service itself (below), so do not start `tools/dev/coarse-tiles` beside it; `--no-coarse` leaves it out.

## How a landing reaches the tab

1. **Follow main.** `service.mjs` keeps a dedicated worktree (`~/turku3d/wt/localhost/repo`, detached) and moves it to
   `refs/heads/main` on every change: it watches the ref directory and the landing ledger and polls once a second.
   A change to `game/package*.json` runs `npm ci` there and restarts the dev server; a change to the files the dev
   server itself loads (`game/vite.config.ts`, `tools/dev/{environment,http}.mjs`, `vitePlugin.mjs`) restarts it.
2. **Vite dev server.** The game runs from that worktree through Vite in dev mode (`vitePlugin.mjs`, registered in
   `game/vite.config.ts` for `serve` only, so `make game-dev` gets the same routes). Production builds are unchanged.
3. **Code landings swap in place.** `main.ts` owns the world (renderer, tiles, physics worker with the character and
   every vehicle, collision streaming) and accepts hot updates of `play.ts`, the play session. A landing that changes
   only modules the session imports (UI, HUD, gameplay, missions, actors, input, audio) disposes the session and
   starts the new one from a `HotPlay` (`gameplay/hotState.ts`): missions, money, permit, clock, the driven vehicle and
   the camera; the character and the vehicles never left the physics worker. Measured: under 0.5 s from checkout
   to the new HUD text, about 2 s from the landing.
4. **Everything else reloads, with the game kept.** A change that reaches `main.ts` without passing `play.ts`
   (world, render, physics worker, `core/save.ts`, `main.ts` itself) is a Vite full reload; a dev-server restart is
   a socket disconnect. On either event the session writes the autosave slot and a `sessionStorage` marker
   (`turku3d.resume`), and the next page load continues from the autosave without the start menu.
5. **Data landings swap in place.** `/tiles/*` serves the tile release `state.json` names, with
   `Cache-Control: no-cache` (revalidation only). When the release changes, the open tab applies it without a page
   load (`game/src/world/README.md`): changed collision tiles are replaced in the worker, the visual tileset is
   swapped once the new one has settled, the player and the session stay. Measured on a one-tile landing: 193 s from
   the landing to the swapped tileset, of which 191 s is the quick `make tile-build` and 1.4 s the swap itself. A
   builder's per-tile entry skips the build: from `put-tiles` of a newly listed tile (t0_1_-1, headless tab on a
   sandbox service, host load 70-90) to the swapped tileset 2.2-4.8 s (put 0.9-1.1 s, composed and served 0.2-1.0 s
   later, manifest fetched within 0.1 s, the hidden tileset settled 0.5-2.5 s after the tile's model loaded). The car
   that drove into the tile's draft, reversed into the core and drove back met collision under it in every frame,
   never fell and kept its speed (23.7 m/s out, 23.0-23.3 m/s back); the first frame of the swapped tileset still
   hitches 0.4-0.6 s (its materials' node builds and uploads; a whole-tileset reload repeats them for every visible
   tile).

## The coarse tile service follows main too (`coarse.mjs`)

The draft tile service (`tools/dev/coarse-tiles`, `pipeline/turku3d/coarse/service.py`) runs as a second child from
the same worktree, on port base + 5 (main: 5175), output in `coarse.log` in the state directory. Its builds are child
processes in its process group, so stopping it stops them.

- **Restart on landing.** A checkout that changes a file the running service loaded restarts it: the repository
  files of its imported modules (`/coarse/status` `modules`, re-read at every checkout; before the first answer,
  anything under `pipeline/turku3d/`), its launcher `tools/dev/coarse-tiles` and the pipeline environment
  `pipeline/env/`. The follow loop does not wait for it.
- **Drain.** Before the restart the service gets `POST /coarse/drain` (it starts no further builds) and the running
  builds may finish: the supervisor polls `/coarse/status` until `building` is empty, at most 60 s, then stops the
  process group (SIGTERM, SIGKILL after 5 s) and starts the service from the worktree's newest checkout.
- **Crash and port.** An unexpected exit restarts it after 2 s, doubling up to 60 s while it keeps failing. When
  another process holds the port (a hand-started `coarse-tiles`), the state is `port in use` and the start is retried
  every 10 s, so stopping that process hands the port over.
- **Status.** `state.json` and `/__localhost/status` carry `coarse: {commit, state}`: the commit the worktree was at
  when the process started and `starting`, `running`, `draining`, `restarting`, `exited`, `port in use` or `off`
  (`--no-coarse`). Every change is an events row `{"type": "coarse", commit, state}`.
- **The game during a restart** (`game/src/world/coarseTiles.ts`): a connection error (the dev server's 503) pauses
  the drafts with one console line; the draft badge says `drafts: service restarting` and only `/coarse/status` is
  probed, every 2 s, until any process answers it (whatever its producer code). Then the 3x3 window and the path
  ahead are asked for at once; a new producer rebuilds them on request. A 404 from `/coarse/want` (an older process
  without the route) only skips the prefetch. A page loaded while the service is down starts paused when
  `/__localhost/status` shows it supervised, and so does a page whose first `/coarse/status` takes over 2 s (a busy
  service).

## Tile releases and per-tile entries (`scripts/dev/tile_store.py`)

`$TURKU3D_DATA/tile-store/<key>/` holds one complete tile dist (tileset, manifest, visual, collision, ground, roads,
vehicles, addresses, the tile quality ledger `quality.json`) plus `STORE.json`
`{key, commit, source, time, data_time, data_parts, tiles}`; `index.jsonl` lists the entries in the order they were
added. The newest 30 are kept, plus the newest entry that carries each tile of `playable-tiles.json` when none of
those 30 does. Entries are immutable APFS clones of build output. The key of a commit hashes the git blob ids of its
refresh and tile-build inputs (`live_cache.INPUTS`) and the data root's parts (`landing_class.data_parts`: facade
approvals, tile list, published layers). After the integrator publishes a commit, the layers it refreshed are the
data root's, so the commit's key is the live release's from then on. `data_time` and `data_parts` are when the build
seeded from the data root and what it read (the live build also writes `data_time` into `live-build.json`).

`$TURKU3D_DATA/tile-store/tiles/<tile>/<key>/` holds **per-tile entries**: one L0 tile a builder finished, with its
visual tile, its 500 m and 1 km parents and the coarse leaves beside it, its 16 collision tiles and the ring around
them, its ground textures, the parked cars on it and the road graph and address pool of the build, at their dist paths,
plus `TILE.json` (the manifest fragment and provenance: `task`, `commit`, `mainline`, `code`, `data_key`, `listed`,
`source`, `time`). The key hashes the assembler's input keys of those tiles; the newest three per tile are kept and
`tiles/index.jsonl` lists them. Builders put them:

```bash
make tile-add T=t0_2_0                       # ends with put-tiles of the new tile and its changed edge neighbours
make tile-build T='t0_0_0 t0_1_0' PUT=1      # puts the tiles whose own bytes differ from live
python3 scripts/dev/tile_store.py put-tiles --tiles-dir "$TURKU3D_WORK/dist/tiles" --tiles t0_2_0
python3 scripts/dev/tile_store.py key --release "$TURKU3D_DATA/live" # key of the release's own inputs
python3 scripts/dev/tile_store.py served     # what the service streams for HEAD: {key, delta, base, served}
```

- **What `/tiles` serves** (`tile_store.served`, called once per pass by `tiles.mjs`): the base release is the
  integrator's entry for main's key; the live release when its commit and its own recorded data have that key (a code-only landing); else
  the newest (by `data_time`) of the live release and the integrator's entries of main's ancestors. Over it,
  `compose` lays the newest eligible per-tile entry of every tile listed in `playable-tiles.json` and puts the result
  as a complete entry (source `composed`, `STORE.json` `base` and `entries`); a newer entry wins where two share a
  parent or a ring cell, and a coarse leaf never replaces a playable tile. The tileset is rebuilt from the composed
  visual entries with the assembler's own `build_tileset`, so a release with the same bytes has the same tileset.
- **An entry is left out** when the base has its tile's bytes already (*absorbed*: the integrator's release of the
  same inputs converged, so the tab sees no change and swaps nothing), when the base was seeded from the data root
  after the entry was put and carries the tile (*superseded*), when its tile or its builder's list is not listed in
  the data root yet, or when it was built with tile code that was never main's (`mainline` is the builder's merge-base
  with main when its tile code is exactly that commit's; otherwise its `code` must equal main's). A newly listed tile
  is therefore streamed as soon as its builder has put it and `list-tile` has listed it, in either order.
- **Quick builds become per-tile entries.** When main's tile code differs from the base's, or the data root's facade
  approvals or layers changed since the base was built (a facade publish), the service runs
  `tile_store.py quick-build`: `make tile-build AREA=playable-tiles T=<tiles>` on the published layers without
  refresh, from the base's dist (its assembler cache repacks only the tiles whose inputs changed), in a separate
  checkout (`$TURKU3D_WORK/localhost-game/build-repo`), in its own process group; every tile whose own bytes changed
  becomes a per-tile entry (source `localhost`). `<tiles>` is the union of the ledger rows' `tiles`
  (`scripts/dev/landing.py`) since the base, every listed tile when a row has none, minus listed tiles the published
  ground layer lacks (they arrive from their builder). A list change alone builds nothing. `--no-build` turns quick
  builds off.
- The service watches `tile-store/tiles/` and re-composes over the same base when only per-tile entries changed.

## Badge and status

`GET /__localhost/status` and the custom HMR event `turku3d:status` carry `{service, commit, verification, tiles}`.
`ui/buildBadge.ts` shows `main <commit> · unverified` (with `verifying: <stage>` while an integration covering the
commit runs), `verified` once the newest integration row covering it is green with no advisory failure (or no row
covers it and it is in the live release), `tests red: <stages>` when that row was published with failing advisory
checks (Chrome, audits, car report, facade baseline), and `verification failed: <stage>` when that row is red with
its culprit in the commit, data-red or an infrastructure failure (`status.mjs`). A quick tile build adds
`building tiles <ids>`.

## Files

| File | Role |
| --- | --- |
| `service.mjs` | follow loop, worktree, Vite child, `state.json`, `events.jsonl` |
| `coarse.mjs` | the coarse tile service child: restart on landing after a drain, crash backoff, port check |
| `status.mjs` | integration verdict for a commit; ledger helpers |
| `tiles.mjs` | which tile release `/tiles` serves (`tile_store.py served`); quick builds into per-tile entries |
| `vitePlugin.mjs` | dev-server routes: `/tiles`, `/tile-store`, `/telemetry`, `/__localhost/{status,event}`, `/facades/candidates/`; `/telemetry` on `vite preview` too |
| `../facadeCandidates.mjs` | the facade candidate viewer (`../README.md`), shared with `serve-dist.mjs` |
| `../http.mjs` | static file, telemetry and coarse proxy handlers shared with `serve-dist.mjs` |

State lives in `--state-dir` (default `$TURKU3D_WORK/localhost-game`): `state.json`, `events.jsonl`, `vite.log`, `coarse.log`,
`service.pid` (a second instance on the same state directory refuses to start).

Texture-only builders can preserve the served global road graph and address pool while
publishing their tile-owned geometry, collision, ground and facade maps:
`python3 scripts/dev/tile_store.py put-tiles --tiles-dir "$TURKU3D_WORK/dist/tiles" --tile-only --tiles t0_0_0 t0_0_1 t0_1_0 t0_1_1`.
The default includes world metadata for region builders.
