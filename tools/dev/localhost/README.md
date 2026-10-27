# The localhost game (`tools/dev/localhost-game`)

The localhost game runs **main, hot**: a landing reaches the open tab within seconds, and the integrator's verdict
arrives later on a badge. `live` and GitHub Pages stay verified-only; only localhost shows unverified builds.

```bash
tools/dev/localhost-game                 # main: http://127.0.0.1:5171/?tiles=/tiles/tileset.json
tools/dev/localhost-game --port 5211 --worktree "$TURKU3D_WORK/lh/repo" --no-build   # a sandbox instance
curl -s localhost:5171/__localhost/status          # {service, commit, verification, tiles}
tail -f "$TURKU3D_WORK/localhost-game/events.jsonl" # checkouts, verdicts, tile releases, page loads, hot swaps
node --test tools/dev/localhost/localhost.test.mjs  # verdicts, routes, the follow loop (synthetic git repo)
cd game && ../scripts/dev/host_slots.py browser -- node_modules/.bin/playwright test -c playwright.hotswap.config.ts
```

It replaces the `serve-dist.mjs` invocation on port 5171 and keeps its routes (`POST /telemetry` into
`$TURKU3D_WORK/telemetry`, `/coarse/*` to the coarse tile service on port base + 5).

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
   the landing to the swapped tileset, of which 191 s is the quick `make tile-build` and 1.4 s the swap itself.

## Tile releases (`scripts/dev/tile_store.py`)

`$TURKU3D_DATA/tile-store/<key>/` holds one complete tile dist (tileset, manifest, visual, collision, ground, roads,
vehicles, addresses) plus `STORE.json` `{key, commit, source, time, tiles}`; `index.jsonl` lists the entries in the
order they were added and the newest 30 are kept. Entries are immutable APFS clones of build output. The key of a
commit hashes the git blob ids of its refresh and tile-build inputs (`live_cache.INPUTS`) and the data root's parts
(`landing_class.data_parts`: facade approvals, tile list, published layers). After the integrator publishes a
commit, the layers it refreshed are the data root's, so the commit's key is the live release's from then on.

- The integrator's live build puts its tile-build output under the key right after the tile-build stage, before the
  checks run (source `integrator`).
- The service serves, in order: the integrator's entry for main's key; its own quick build of it; the live release
  when the live commit has the same key (a code-only landing); else the last release while a quick build runs.
- A quick build (`tile_store.py quick-build`) runs `make tile-build AREA=playable-tiles T=<tiles>` on the published
  layers, without refresh, in a separate checkout (`$TURKU3D_WORK/localhost-game/build-repo`) and puts it under
  `sha256(key + ":quick")` (source `localhost`), so it never shadows the integrator's build. `<tiles>` is the union
  of the ledger rows' `tiles` (`scripts/dev/landing.py`) since the served release; every listed tile when a row has
  none. `--no-build` turns quick builds off.

## Badge and status

`GET /__localhost/status` and the custom HMR event `turku3d:status` carry `{service, commit, verification, tiles}`.
`ui/buildBadge.ts` shows `main <commit> · unverified` (with `verifying: <stage>` while an integration covering the
commit runs), `verified` once the newest integration row covering it is green (or the commit is in the live
release), and `verification failed: <stage>` when that row is red with its culprit in the commit, data-red or an
infrastructure failure (`status.mjs`). A quick tile build adds `building tiles <ids>`.

## Files

| File | Role |
| --- | --- |
| `service.mjs` | follow loop, worktree, Vite child, `state.json`, `events.jsonl` |
| `status.mjs` | integration verdict for a commit; ledger helpers |
| `tiles.mjs` | which tile release `/tiles` serves; quick builds |
| `vitePlugin.mjs` | dev-server routes: `/tiles`, `/tile-store`, `/telemetry`, `/__localhost/{status,event}`, `/facades/candidates/`; `/telemetry` on `vite preview` too |
| `../facadeCandidates.mjs` | the facade candidate viewer (`../README.md`), shared with `serve-dist.mjs` |
| `../http.mjs` | static file, telemetry and coarse proxy handlers shared with `serve-dist.mjs` |

State lives in `--state-dir` (default `$TURKU3D_WORK/localhost-game`): `state.json`, `events.jsonl`, `vite.log`,
`service.pid` (a second instance on the same state directory refuses to start).
