# World regions: one continuous world built in units, streamed by distance

2026-11-13, `w2-region`. A **region** is one drivable world made of many square **build units**, grown ring by ring
from a centre ("Centre, then rings": the first ring is about 1 km around Kauppatori). It extends the location
contracts of [world-pipeline.md](world-pipeline.md); entities, records and the store are unchanged. Code:
`pipeline/turku3d/world/region.py`, client `game/src/world/location/` (region mode of the loader).

## Grids (all on the engine origin, so they nest)

| Square | Side | What it is |
| --- | --- | --- |
| cell | 62.5 m | ground, trees, planting entities; collision cells `c_i_j` |
| chunk | 250 m | what the client fetches and draws as one model per layer; road graph tiles `t0_i_j` |
| unit | 500 m | what one build process discovers and compiles; equal to one lidar tile and one WFS block |

## Build

- **Unit query:** the unit's square grown by a **margin** (62.5 m, one cell) on every side. `Area` has a square
  shape for this (`touches` and `bbox` are the square's), so nothing is discovered in the corners of a circle.
  No background ring: every row is `scene` and carries its other levels (`levels`), so the client chooses by distance.
- **Ownership:** a row belongs to the unit whose square holds its `origin` (x, z), the same rule the client uses to
  put a row in a chunk. Rows anchored in the margin are kept in the unit's manifest for the seam check and left out
  of the region; the neighbour unit owns them. An entity whose box crosses a border (a building, a fence run) is
  therefore drawn once, by one unit, and was discovered with all of its sources by that unit.
- **Seams:** a margin row's hash is compared with the owning neighbour's row of the same id. Equal hashes mean the
  two builds agree on it (entities do not depend on the query except near its edge, w2-core 12:27Z); the assemble
  step reports equal / differing per border and names the differing ids. A kind whose rows differ there needs more
  margin or a query-independent discover; it is fixed in the kind, never by blending.
- **Units in parallel:** each unit is its own `build` process (memory of one location), several at once on a host,
  all writing into one store (content-addressed, so equal entities compile once and are shared). Source caches are
  per lidar tile and WFS block and shared by every unit on the host. Unit manifests are written to
  `units/<region>/<unit>.json` in the store, not to `locations/`, so the lab's location list does not show them.
- **Assemble** (`world region assemble NAME`): reads the unit manifests, keeps owned rows, writes one **chunk
  object** per 250 m chunk (`turku-chunk/1`: `{chunk: [i, j], entities: [rows]}`, a content-addressed store object)
  and the **region index** `locations/<NAME>.json` (`turku-region/1`): `chunks` {`i_j`: url, bytes, entities,
  triangles, y range}, `collision` (cells of the owned ground), `roads` (graphs of owned chunks), `start`, `focus`,
  `units` (id, square, revision, built), `seams`, `stats`. Small: about 100 bytes a chunk.
- **Adding a ring** builds only the new ring's units and assembles again: the inner units are not rebuilt, their
  chunk objects come out byte-identical (same url, still cached by browsers), only the outer border's seam rows
  are checked anew. Rebuilding one unit after a generator change is the same command for that unit.
- **Commands:** `python -m turku3d.world region plan|build|assemble NAME --at E,N --radius R [--with-area AREA]
  [--units i_j,...] [--parallel K]`. A ring is the units whose square meets the circle, plus the units of named
  pilot areas' queries. The ring is an argument; nothing about Turku is in code.

## Client

- `?world=NAME` loads `locations/NAME.json`; a `turku-region/1` index starts the loader in **region mode**: the
  same chunked drawing, styling, tree levels and refresh as a location, with the set of chunks following the camera.
- **Streaming by distance:** a 250 m chunk is fetched (nearest first, a few at a time) and drawn while the nearest
  edge of its square is within its layer's range, and released beyond range + 150 m (meshes disposed, shared assets
  kept). Ranges (`REGION_RANGE`, region.ts): ground and buildings 350 m, trees and planting 250 m (instances near,
  their far mesh beyond, as in a location), street objects 150 m; a chunk's far corner lies up to ~350 m beyond its
  range. Measured on the M1 Max (headed, Kauppatori): 700/700/600/300 m drew 3,600 draws and 11 M triangles at
  24 fps, CPU-bound.
- **Collision near the player only:** every cell of the index is listed in the game's collision manifest by url,
  nothing is fetched up front; the existing collision streamer loads the cells within its radius and along the
  travel ahead and puts edge walls where no cell exists (the region's outer border).
- **Stills:** the chunks within range of the requested camera are loaded before the page says ready, so a still is
  the same as in play.
- **Multiplayer:** the index revision and chunk hashes pin the content: clients in one room load the same chunks.

## Later, not now

Distant chunks as merged far meshes per chunk (one draw each), a city-wide index of regions, and a background ring
of cheap shells beyond the outermost ring. None of it changes the formats above.
