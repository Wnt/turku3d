# Ambient walkers

Street-richness Phase 4: pedestrians on Turku's pavements, footpaths and crossings. Client only; the data is the
draft tiles' `roads.json` (FOOT-only ways) that every client already streams.

| File | Role |
|---|---|
| `network.ts` | FOOT network of the resident tiles: canonical nodes, chains, birth pieces per 25 m cell |
| `population.ts` | Deterministic walkers: conveyor slots, routes, positions at a UTC time |
| `ring.ts` | Which walkers this client draws: camera ring, caps, hysteresis and fades |
| `heights.ts` | Ground Y along chains from cached physics rays (`foot` filter, stair treads) |
| `asset.ts`, `visuals.ts` | `walkers.bin` decoder; one InstancedMesh per body variant, VAT material |
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
  where `activity` follows the day cycle's hour at the birth minute.
- **Routes.** A life starts at a hashed point of the cell's pieces, idles up to 2.5 s, then walks at 1.2–1.5 m/s
  with a 0.35–0.7 m keep-right offset that tapers to zero at junctions and dead ends. At a junction the exit is
  `hash(walker, node, step) % exits`, exits ordered by departure direction; 12% of junctions add a 2–6 s pause;
  dead ends turn round. Turn decisions use integers only, so float rounding cannot fork two clients.
- **Animation.** Walk phase is metres walked divided by the variant's measured stride (1.42–1.47 m per loop:
  the median ground speed of baked shoe vertices in contact), so feet stay planted; idle cycles its 2.5 s loop.
- **Lifecycle fade.** 0.8 s at birth and at the end of life, part of the shared state.

## Drawing (local only)

The ring scores walkers by distance in view (camera cone plus 12°) and 2.5× distance out of view: drawn to 100 m
ahead and 40 m behind, 10 ring metres of hysteresis, incumbents win ties by 6 m. The cap picks the lowest scores.
A walker entering or leaving the drawn set closer than 55 m in view fades over 0.6 s (vertex scale, no discard);
elsewhere it switches instantly. Caps: mobile 0, low 10, medium 30, high 50; `?walkers=N` (0–200) overrides.
Yaw is smoothed per drawn walker. None of this changes where a walker is.

## Rendering budget

One `WalkerMaterial` (MeshStandardNodeMaterial) for all three body meshes. `setupPosition` reads the pose from the
RGBA16F vertex animation texture (two frames, linear blend, spherical-Fibonacci normal index) **before** r186's
instancing, and the previous phase for the velocity pass, so TRAA sees the limbs move. Per-instance attributes
carry clip rows, phases, fade and packed sRGB tints for seven body regions. The program does not depend on the
instance count (`tests/unit/walkers.test.ts` builds it with r186's CPU builder for 0–50 walkers, all variants,
with and without velocity). `userData.vertexProgram` keeps it in the depth/normal prepass
(`render/normalPrepass.ts`). Walkers cast no shadows on any tier (a VAT shadow program is a follow-up) and stay out
of the river reflection pass. Submitted draws: prepass + beauty per visible variant, at most 6 on medium.

## Multiplayer

Existence, route and position are pure functions of the resident road graphs and the shared UTC clock
(`DayClock.sharedNow()`, synchronised to the multiplayer server's welcome). Clients agree without traffic; nothing
depends on tile load order or `mergeRoadGraphs` numbering. Limits: a client draws only walkers whose routes are known
within its resident tiles (others are simply not drawn there); two clients holding different builds of a tile's
`roads.json` disagree about walkers in that tile (cosmetic, gameplay is unaffected; a road-graph hash agreement as
planned for Phase 3 traffic would gate it). Walkers have no physics; a player walking into one passes through.

## Verify

```sh
cd game
npx vitest run tests/unit/walkers.test.ts tests/unit/audio.test.ts
npx tsc --noEmit -p tsconfig.json
```

In the browser: `?walkers=30` (or `0` to compare), `scripts/probe-walkers.mjs` (headless tour, close-ups, draw counts per pass, on/off timings), `window.__game.walkers.snapshot()` for drawn/candidate counts,
network build time, per-frame update and scan cost; `liveWalkerCount()` from this module for the Phase 0 scorecard,
or `walkersNear(buildWalkNetwork(roadGraphs), x, z, 50, utcMs)` to count them offline from tile data.

The body asset and its rebuild: [`public/assets/walkers/README.md`](../../../public/assets/walkers/README.md).
