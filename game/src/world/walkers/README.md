# Ambient walkers

Street-richness Phase 4: pedestrians on Turku's pavements, footpaths and crossings. Client only; the data is the
draft tiles' `roads.json` (FOOT-only ways) that every client already streams.

| File | Role |
|---|---|
| `network.ts` | FOOT network of the resident tiles: canonical nodes, chains, birth pieces per 25 m cell |
| `population.ts` | Deterministic walkers: conveyor slots, routes, positions at a UTC time |
| `ring.ts` | Which walkers this client draws: camera ring, caps, hysteresis and fades |
| `heights.ts` | Ground Y along chains from cached physics rays (`foot` filter, stair treads) |
| `asset.ts`, `visuals.ts` | `walkers.bin` decoder; 96 model recipes, shared wardrobe batches, textured VAT material |
| `reactions.ts` | Local reactions to players: sidestep, dodge or dive, knock-down |
| `network.worker.ts` | Builds the network off the main thread |
| `index.ts` | `Walkers` system for `play.ts`, tier caps, `liveWalkerCount()`, `walkersNear()` |

## Model

- **Network.** FOOT ways without the CAR bit (footways, sidewalks, crossings, pedestrian streets, park paths,
  steps). All coordinates sit on the pipeline's 0.1 m lattice. Endpoints within one lattice step are one node,
  represented by the smallest member, so the result does not depend on which tile arrived first. Edges join into
  chains through complete pass-through nodes; a chain id is a hash of its quantised polyline in canonical
  direction. A node is *complete* when every tile touching it is resident; walking past an incomplete node (the
  edge of the resident area) makes the rest of that route unknown on this client, and the walker is not drawn.
- **Births.** Each 25 m cell (a tenth of a tile, so no cell straddles a seam) lists the network pieces inside it,
  ordered by their own quantised geometry and weighted by the busiest street in the cell (none 0.7, service 0.85,
  street 1.0, major 1.4; pedestrianised streets 1.6). The cell owns `ceil(0.03 × weighted metres)` slots. Slot *k*
  is reborn every 70–110 s (its own hashed period and phase); life *n* exists when `k + u < expected × activity`,
  where `activity` follows the clock hour at the start of the birth minute (`Population`'s `BirthClock`): the
  shared cycle in multiplayer and for the scorecard; in single player this browser's view clock
  (`DayClock.hourAt`: `?time=`, `?cycle=`, the developer time panel). `hourAt` remembers each instant's mode for five
  minutes, so a time edit only changes births after it and no living walker pops; density follows within a life.
- **Routes.** A life starts at a hashed point of the cell's pieces, idles up to 2.5 s, then walks at 1.2–1.5 m/s
  with a 0.35–0.7 m keep-right offset that tapers to zero at junctions and dead ends. At a junction the exit is
  `hash(walker, node, step) % exits`, exits ordered by departure direction; 12% of junctions add a 2–6 s pause;
  dead ends turn round. Turn decisions use integers only, so float rounding cannot fork two clients.
- **Standing.** 16% of lives stand at their birth point all life, 0.5–1 m off the way's centre (never on a zebra),
  playing the idle clip: GTA's dense stand/phone/chat scenarios. 55% of those are a pair of different people
  0.85 m apart along the way, facing each other (`Walker.lifeId`/`member`; the first member keeps the life's id).
- **Signalised crossings.** A chain passing within 1.5 m of a signalised crossing on a car edge, across it, carries a
  gate: the controller and approach axis the drivers on that road use (nearest signalised node within 40 m, named as
  `traffic/tileNetwork.ts` names a lane's signal). A walker stops 0.5–1.7 m before the carriageway, keeping its
  offset, and waits until the drivers there have been red for 4 s (phase 25 s of their 60 s plan); it starts only
  if it can clear the road by 56 s. 6% cross against the light. The plan runs on the traffic clock: `trafficTime`
  supplies it, and a shifted clock (a single-player pause) plans routes again.
- **Animation.** Walk phase is metres walked divided by the variant's measured stride (1.42–1.47 m per loop:
  the median ground speed of baked shoe vertices in contact), so feet stay planted; idle cycles its 2.5 s loop.
- **Lifecycle fade.** 0.8 s at birth and at the end of life, part of the shared state.

## Drawing (local only)

The ring scores walkers by distance in view (camera cone plus 12°) and 2.5× distance out of view: drawn to 100 m
ahead and 40 m behind, 10 ring metres of hysteresis, incumbents win ties by 6 m. The cap picks the lowest scores.
A walker entering or leaving the drawn set closer than 55 m in view fades over 0.6 s (vertex scale, no discard);
elsewhere it switches instantly. A birth closer than 55 m in view is not drawn until that walker is out of view; a drawn walker whose life ends in
view walks on along its continued route (`Population.at(…, linger)`, up to 30 s) until it leaves the view or is 80 m
away, so nobody appears or vanishes in plain sight. Caps: mobile 0, low 10, medium 60, high 100; `?walkers=N` (0–200)
overrides.
Yaw is smoothed per drawn walker. None of this changes where a walker is.

## Reactions to players (local)

`reactions.ts` displaces a drawn walker from its deterministic position, and the displacement decays back to zero:
- a player on foot whose closest approach over 1.6 s comes within 0.85 m: the walker sidesteps relative to its own
  heading (1.5 m/s);
- a player's vehicle whose footprint, swept along its motion relative to the walker for 1.8 s, comes within 1.1 m
  of it: the walker hurries aside (3 m/s) or dives (5.5 m/s within 0.7 s of arrival); a parked or slow car on the
  path is walked round;
- a vehicle at 2.5 m/s or more that still reaches the walker knocks it down: thrown along the car's motion, it
  falls (0.45 s), lies (4 s), gets up (0.9 s) and walks back to its route.
- a moving walker keeps 0.6 m from other drawn walkers (standing people, someone coming the other way or slower
  ahead); standing and waiting walkers hold still.
The walk cycle advances by the reaction's own metres, facing follows the combined motion, and the knock-down leans
the instance about its feet. Threats are every player the client knows of (`play.ts walkerThreats`: the local
character or driven vehicle, `RemotePlayers.threats`), so multiplayer clients see nearly the same reactions. Births,
routes and what traffic yields to are unchanged; `snapshot().reacting` counts walkers with an active reaction.

## Rendering budget

One `WalkerMaterial` serves twelve wardrobe families and their near/far meshes. Ninety-six anatomical/wardrobe
recipes share these batches; all use one RGBA16F animation texture and one padded albedo atlas. Interleaved
vertex and instance attributes fit WebGPU's guaranteed eight vertex-buffer slots including velocity history.
LOD uses a 22–26 m hysteresis band. There is one instanced contact pass and no per-person shadow map or CPU rig.
The detailed budget, sources and deterministic rebuild are in `public/assets/walkers/README.md`.

## Multiplayer

Existence, route and position are pure functions of the resident road graphs and the shared UTC clock
(`DayClock.sharedNow()`, synchronised to the multiplayer server's welcome). Clients agree without traffic; nothing
depends on tile load order or `mergeRoadGraphs` numbering. Limits: a client draws only walkers whose routes are known
within its resident tiles (others are simply not drawn there); two clients holding different builds of a tile's
`roads.json` disagree about walkers in that tile (cosmetic, gameplay is unaffected; a road-graph hash agreement as
planned for Phase 3 traffic would gate it). Walkers have no physics; they react to players locally (see Reactions).

## Verify

```sh
cd game
npx vitest run tests/unit/walkers.test.ts tests/unit/walker-reactions.test.ts tests/unit/walker-signals.test.ts tests/unit/walker-sight.test.ts tests/unit/audio.test.ts
npx tsc --noEmit -p tsconfig.json
```

In the browser: `?walkers=30` (or `0` to compare), `scripts/probe-walkers.mjs` (headless tour, close-ups, draw counts per pass, on/off timings), `window.__game.walkers.snapshot()` for drawn/candidate counts,
network build time, per-frame update and scan cost; `liveWalkerCount()` from this module for the Phase 0 scorecard,
or `walkersNear(buildWalkNetwork(roadGraphs), x, z, 50, utcMs)` to count them offline from tile data.

The body asset and its rebuild: [`public/assets/walkers/README.md`](../../../public/assets/walkers/README.md).
