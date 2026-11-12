# World pipeline contracts (W2)

2026-11-12. The contracts of the new world pipeline: what an entity is, where its data lives, what the client loads,
what a generator implements, and how one changed entity reaches the running client. Goal and tracks are in the
[visual brief](visual-brief.md). Code: `pipeline/turku3d/world/` (Python) and `game/src/world/location/` (client).
Owner: `w2-core`. Change a contract here, in place, and say so on the campaign board.

The old tile generator (`pipeline/turku3d/coarse`), its caches and the prepared-scene compiler
(`pipeline/turku3d/scene`) are not part of this pipeline and are not built upon.

## 1. The model in one paragraph

A **source reader** reads city data for an area. A **kind** (building, terrain, ground, trees, …) turns source
records into **entities**: each has a stable id, a **description** (measured facts, stored as data) and a
**compiled output** (meshes and material references, content-addressed). A **location** is a query, `--at E,N
--radius R`: the driver finds the entities that intersect it, compiles those whose inputs changed and writes a
**manifest** that the client loads. Nothing in an entity depends on the query that found it, so two locations that
overlap share entities and a city-wide index can later serve the same store.

## 2. Coordinates

[docs/specs/coordinates.md](../specs/coordinates.md) stays the only frame definition.

- **Descriptions and source references** use the survey frame: EPSG:3877 `E`, `N` in metres and N2000 `H`, absolute.
  A description may define local frames from such points (a wall frame: origin, along-wall axis, up).
- **Compiled output** uses engine axes (x = E − 23459500, y = H, z = −(N − 6704500); right-handed, Y up) in metres,
  **relative to the entity's `origin`** (engine metres, double precision in JSON) so float32 vertices keep
  sub-millimetre precision. Front faces are counter-clockwise; normals point out of solids and up from ground.
- **Areal kinds** (terrain, ground) cut the world on the origin-aligned grid of `config.py`: cell `(i, j)` covers
  x ∈ [i·S, (i+1)·S), z ∈ [j·S, (j+1)·S), `i` east, `j` south. S is 62.5 m unless the kind's owner documents another
  power-of-two multiple. Never cut on the query circle.
- Conversions come from `turku3d.world.frame` (which re-exports `turku3d.config`); do not write the origin elsewhere.

## 3. Entity

### 3.1 Identifier

`<kind>/<key>`, for example `building/103456789A`, `terrain/c_-6_-4`, `trees/c_-6_-4`. `kind` is `[a-z][a-z0-9-]*`;
`key` is `[A-Za-z0-9._-]+`. The id is a relative file path and a URL path, unchanged.

- A discrete object uses the permanent identifier of its authoritative source. A building is one LoD2 building
  record (`bldg:Building_LOD2`): its key is the permanent building id (`pysyvarakennustunnus`, VTJ-PRT), or
  `gml-<gml_id>` when the record has none. Never an array index or a hash of geometry.
- An areal kind uses the grid cell, `c_<i>_<j>` at 62.5 m (the collision-cell id of `config.py`).
- The id never contains the location name, the query or a generator version.

### 3.2 Description

The description is everything measured or authored about one entity, as one JSON document. Compiling it needs no
other input: **`compile` is a pure function of the description**. Ground heights under a building, the neighbours
that matter, a wall's reading from a panorama: if the output depends on it, it is in the description.

Every description has this envelope; the rest belongs to the kind's schema:

```json
{
  "schema": "turku-description/<kind>/<n>",
  "id": "building/103456789A",
  "tier": "auto",
  "provenance": [{ "source": "turku-lod2", "key": "…", "at": "2026-11-12T21:00:00Z", "by": "w2-core" }]
}
```

- `tier`: `auto` (fitted from registers, LoD2, lidar, orthophoto priors, no human or model looked at the building),
  `observed` (read from imagery by the automatic observation chain, with confidence), `reviewed` (checked against
  several panoramas). A higher tier is never overwritten by a lower one.
- `provenance`: where each part came from. A kind's schema may add per-field provenance and confidence.
- The **building** description is defined by `w2-buildings` in
  [docs/specs/building-description.md](../specs/building-description.md); this document only fixes the envelope.
- No building ids or coordinates in code: anything specific to one entity is in its description.

A description is produced in two steps, both owned by the kind:

1. `describe(stub, sources, stored)` builds the **effective description** from source records plus the **stored
   description** when one exists. With no stored description this is the automatic fit.
2. The stored description is what observation and review wrote for that entity (section 4.2). How stored facts
   override automatic ones (per wall, per field) is the kind's rule, written in its schema.

### 3.3 Compiled output

One entity compiles to GLB meshes and instance lists.

- **Mesh:** glTF 2.0 binary, one file per entity per level of detail, self-contained, viewable in any glTF viewer.
  - `POSITION` (float32, relative to `origin`), `NORMAL`, indices uint32; `TEXCOORD_0` in **metres** (walls: u along
    the wall, v up from the wall base; ground and roofs: engine x, z in absolute metres) so procedural materials
    tile at world scale and continue across entities; optional `COLOR_0` and custom `_NAME` attributes.
  - One glTF node per **part** (`walls`, `roof`, `plinth`, …), `node.extras = {"entity": id, "part": name}`.
  - One primitive per material. `material.extras.turku = {"family": "plaster", "params": {…}}` is the **material
    reference**; `pbrMetallicRoughness` (`baseColorFactor` linear, `roughnessFactor`, `metallicFactor`) is the
    fallback every material carries, so an unknown family still renders.
  - No embedded textures. A family that needs maps names them in `params` (library ids or store URLs).
- **Material families** are resolved in the client (`game/src/world/location/materials.ts`, one registered
  function per family: `params → three material`). `flat` (`color`, `roughness`, `metalness`) always exists.
  `w2-buildings`, `w2-street` and `w2-look` add families there; a generator may use a family name before the client
  knows it and gets the fallback.
- **Instances:** `{ "asset": <url>, "transforms": [[x, y, z, yaw, sx, sy, sz], …] }`, positions relative to
  `origin`, yaw in radians about +Y (0 = asset's +Z pointing south, positive turns towards east). `asset` is a GLB:
  a store object or a path under `game/public/assets/`. Use instances for trees, furniture and repeated kit parts.
- **Levels of detail:** `lod` 0 is street-level detail. `lod` 1 is the cheap far representation (a building: its
  shell, at most a few hundred triangles). A kind must emit `lod` 0; if it emits no `lod` 1 the driver uses the
  `lod` 0 file. The manifest lists one chosen level per entity; a streaming client can later choose by distance.

### 3.4 Entity record

Written by the driver per compiled entity (`turku-entity/1`), immutable, stored under its build key:

```json
{
  "schema": "turku-entity/1",
  "id": "building/103456789A",
  "kind": "building",
  "sources": [{ "source": "turku-lod2", "key": "…", "sha256": "…" }],
  "description": { "sha256": "…", "url": "objects/7c/7c…e1.json", "tier": "auto" },
  "generator": { "kind": "building", "code": "<sha256 of the kind's source files>" },
  "key": "<sha256 of description.sha256 + generator.code + format version>",
  "origin": [-346.5, 12.1, -242.8],
  "bounds": [-12.0, -0.4, -9.5, 12.0, 21.3, 9.5],
  "output": {
    "lods": [{ "lod": 0, "url": "objects/3f/3f…9a.glb", "sha256": "3f…9a", "bytes": 48211, "triangles": 1532 }],
    "instances": [],
    "families": ["plaster", "roof-metal", "flat"]
  },
  "hash": "<sha256 of the canonical JSON of origin, bounds and output>",
  "timings": { "describeMs": 4, "compileMs": 310, "packMs": 6 }
}
```

- `key` decides whether to rebuild (same key: reuse the record). The code hash covers the kind's package and the
  shared packer, so editing a generator rebuilds its entities and nobody maintains version strings.
- `hash` is the **content hash**: it changes exactly when what the client draws changes. The client swaps an entity
  when its `hash` changes and at no other time.
- `bounds` are `[minX, minY, minZ, maxX, maxY, maxZ]` relative to `origin`.

## 4. Store

Two roots, both plain directories.

### 4.1 Output store: `$TURKU3D_WORLD_STORE` (default `$TURKU3D_WORK/world`)

```
objects/<aa>/<sha256>.<ext>   immutable blobs: .glb meshes, .json descriptions and records, .bin, .png/.ktx2
builds/<key>.json             entity record by build key (immutable)
locations/<name>.json         location manifests (the only mutable files)
```

- Every blob is named by the SHA-256 of its bytes; `<aa>` is its first two hex digits. Writes are
  write-to-temp-then-rename. Nothing in `objects/` or `builds/` is ever modified, so two stores merge with
  `rsync -a --ignore-existing`, and a stage can run on any host and ship its results back.
- It is per sandbox by default because generator code differs between sandboxes. It is scratch: delete it freely;
  `python -m turku3d.world gc` drops objects that no manifest in `locations/` references.
- The dev server serves it at `/world/…` (`/world/objects/…` immutable, `/world/locations/…` no-cache).

### 4.2 Shared data: `$TURKU3D_WORLD_DATA` (default `$TURKU3D_DATA/world`)

```
descriptions/<kind>/<key>.json         the stored description of one entity (section 3.2)
descriptions/<kind>/<key>.log.jsonl    one line per write: time, writer, tier, sha256, note
descriptions/.history/<sha256>.json    every version ever stored
sources/<source>/…                     source data fetched on demand, by the source's own tiling
```

- **Stored descriptions live here**, outside Git, shared by all sandboxes and hosts: they are the campaign's
  accumulating measurements. Write only through `turku3d.world.store.write_description`, which validates the
  envelope, refuses to lower a tier, keeps the old version and appends the log line.
- Trying a description without publishing it: put it in a private root and set
  `TURKU3D_WORLD_DESCRIPTIONS=/private/root:$TURKU3D_WORLD_DATA/descriptions`; the first root that has the file
  wins for reads, and writes go to the first root.
- `sources/` is a cache of public source data (WFS responses, lidar tiles, orthophoto tiles, panorama coverage
  indexes). `$TURKU3D_DATA/raw` stays read-only; readers look there first and fetch into `sources/` only what is
  missing. Reference imagery itself stays where `qa/README.md` puts it.

## 5. Location manifest

`locations/<name>.json`, `turku-location/1`. `name` is a pilot area (`humalistonkatu`, `portsa`, …: the `area` of
its reference views) or `at-<E>-<N>-r<R>` for a free query.

```json
{
  "schema": "turku-location/1",
  "name": "humalistonkatu",
  "revision": 12,
  "built": "2026-11-12T21:40:03Z",
  "query": { "crs": "EPSG:3877", "at": [23459140.0, 6704730.0], "radius": 150.0, "background": 600.0 },
  "origin": [23459500, 6704500, 0],
  "focus": [-360.0, 13.2, -230.0],
  "entities": [
    {
      "id": "building/103456789A", "kind": "building", "tier": "auto", "role": "scene",
      "hash": "…", "origin": [-346.5, 12.1, -242.8], "bounds": [-12.0, -0.4, -9.5, 12.0, 21.3, 9.5],
      "lod": 0, "url": "objects/3f/3f…9a.glb", "bytes": 48211, "triangles": 1532,
      "instances": []
    }
  ],
  "stats": { "entities": 214, "triangles": 412000, "bytes": 9100000, "byKind": { "building": 61 } },
  "timings": { "sourcesMs": 800, "discoverMs": 120, "describeMs": 900, "compileMs": 5200, "manifestMs": 15 }
}
```

- URLs are relative to the store root (`/world/` on the dev server).
- `role` is `scene` (inside `radius`, `lod` 0) or `background` (between `radius` and `background`, cheap level of
  detail). The client treats them alike.
- `focus` is the query centre on the ground in engine metres: where an interactive session starts.
- The manifest holds everything the client needs to draw the location: no second request per entity besides its GLB
  and instance assets. Time of day, weather, quality and camera belong to the session or the capture, not here.
- City scale later: the same entity rows served by a spatial index instead of one file. Do not put anything in an
  entity row that only makes sense relative to this manifest's query.

## 6. Generator interface

A track implements one **kind** as a module or package under `pipeline/turku3d/world/kinds/<kind>/` and registers
it in `pipeline/turku3d/world/kinds/__init__.py`. Types are in `pipeline/turku3d/world/api.py`.

```python
class Kind(Protocol):
    name: str                                    # "building"

    def discover(self, area: Area, sources: Sources) -> list[Stub]:
        """The entities of this kind that intersect the area: id, bounds, source references.
        Must not depend on the area beyond selection: the same entity from any query."""

    def describe(self, stub: Stub, sources: Sources, stored: dict | None) -> dict:
        """The effective description (section 3.2): source facts merged with the stored description."""

    def compile(self, description: dict) -> Output:
        """Description in; mesh parts, material references and instances out. Pure and deterministic:
        no source access, no clock, no randomness that is not seeded from the entity id."""
```

```python
@dataclass
class Material:
    family: str                       # "plaster"; resolved by the client, section 3.3
    params: dict                      # JSON; the family's parameters
    color: tuple[float, float, float] = (0.6, 0.6, 0.6)   # fallback base colour, linear RGB
    roughness: float = 0.9
    metalness: float = 0.0

@dataclass
class Part:
    name: str                         # "walls", "roof", …
    positions: np.ndarray             # (n, 3) float, engine axes, relative to Output.origin
    triangles: np.ndarray             # (m, 3) int, counter-clockwise front faces
    material: Material
    normals: np.ndarray | None = None # (n, 3); flat normals are computed when missing
    uvs: np.ndarray | None = None     # (n, 2) metres
    colors: np.ndarray | None = None  # (n, 3|4) linear
    attributes: dict[str, np.ndarray] = field(default_factory=dict)   # custom, written as _NAME
    lod: int = 0

@dataclass
class Instances:
    asset: str                        # store URL or /assets/… path of a GLB
    transforms: np.ndarray            # (n, 7): x, y, z, yaw, sx, sy, sz relative to Output.origin
    lod: int = 0

@dataclass
class Output:
    origin: tuple[float, float, float]            # engine metres, absolute
    parts: list[Part]
    instances: list[Instances] = field(default_factory=list)
```

- The driver packs `parts` into GLB (`turku3d.world.glb`), hashes, stores and writes the record. A generator never
  writes files or touches the store.
- `Sources` gives area reads of the source readers under `pipeline/turku3d/world/sources/` (LoD2 buildings and
  register attributes, terrain heights, surface polygons, OSM, tree register, and panorama coverage), each record
  with its source reference. A reader fetches what is missing for the area on demand (section 4.2).
- Budget: `compile` of one entity in about a second or less; `describe` of one entity in tens of milliseconds once
  the area's sources are loaded. Slow one-off measurement (vision reads, panorama rectification) is not a
  generator step: it runs in `w2-observe`'s chain and writes stored descriptions.
- A kind owns its description schema version. Bump `<n>` in `turku-description/<kind>/<n>` on an incompatible
  change and keep `describe` able to read the stored descriptions already written, or migrate them.
- The stand-in kinds of the walking skeleton (`building`, `terrain`, `ground`, `trees`) are replaced in place by
  their owners (`w2-buildings`: `building`; `w2-street`: the others and any new kinds). Delete what you replace.

## 7. Commands

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.world build --area humalistonkatu        # a pilot area, from its views
bash pipeline/env/run.sh python -m turku3d.world build --at 23459140,6704730 --radius 150
bash pipeline/env/run.sh python -m turku3d.world entity building/103456789A --location humalistonkatu
bash pipeline/env/run.sh python -m turku3d.world watch --location humalistonkatu    # rebuild on description change
scripts/dev/scene-capture.mjs --mode render --world humalistonkatu --views scripts/dev/views/pilot-locations.json \
  --area humalistonkatu --out "$TURKU3D_WORK/captures/humalistonkatu"
```

- `build` prints per-stage timings and entity counts per kind and writes `locations/<name>.json`. `--area NAME`
  derives the query from the cameras of that area's reference views (`qa/views/views.jsonl`); `--at` works anywhere
  in built-up Turku and fetches missing source data.
- The game opens a location at `/?world=<name>` (with `solo=1` for play, `cam=…` for a still): the same client and
  renderer, with the location loader in place of the prepared scene. `scene-capture.mjs --world NAME` adds the
  parameter; `--area NAME` keeps only the views of that area.
- In-process, for a workbench: `turku3d.world.driver.compile_entity(kind, description) -> EntityRecord`.

## 8. Live update of one entity

1. A stored description changes (observation, review, the workbench), or a kind's code changes.
2. `world entity <id> --location <name>` (or the running `world watch`) re-describes and recompiles that entity
   only, writes its new objects and record, and replaces `locations/<name>.json` atomically with `revision + 1`.
   Target: about one second.
3. The dev server watches `locations/` and sends the Vite custom event `turku:world` `{ name, revision }`.
4. The client's location loader re-fetches the manifest and diffs it against what is drawn, by entity `id` and
   `hash`: it loads the new files of changed entities first, then swaps them in one frame, adds new entities and
   removes missing ones. Camera, lighting, time and every unchanged entity stay as they are. No page reload.
5. The same refresh is callable: `await __dev.world.refresh()` resolves `{ revision, changed, added, removed, ms }`
   once the swapped entities are drawn. `scene-capture.mjs` calls it before a capture, so a still never races an
   update.

`__dev.world` also has `manifest()`, `entity(id)` (its record and three.js object), `stats()` (entities,
triangles, geometry and texture bytes) and `focus(id)`.

## 9. Not in these contracts yet

Stills only for now: collision, physics, streaming and multiplayer are not built, and nothing here prevents them
(collision can be a further output per entity; streaming is a spatial index over entity rows; a multiplayer room
pins a set of content hashes). Frame time, triangles and texture memory are recorded per location by the capture,
without a limit.
