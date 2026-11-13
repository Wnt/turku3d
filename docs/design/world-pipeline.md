# World pipeline contracts (W2)

2026-11-12. The contracts of the new world pipeline: what an entity is, where its data lives, what the client loads,
what a generator implements, and how one changed entity reaches the running client. Goal and tracks are in the
[visual brief](visual-brief.md). Code: `pipeline/turku3d/world/` (Python) and `game/src/world/location/` (client).
Owner: `w2-core`. Change a contract here, in place, and say so on the campaign board.

The old tile generator (`pipeline/turku3d/coarse`), its caches and the prepared-scene compiler
(`pipeline/turku3d/scene`) are not part of this pipeline and are not built upon.

## 1. The model in one paragraph

A **source reader** reads city data for an area. A **kind** (building, ground, trees, signpost, fence, …) turns
source records into **entities**: each has a stable id, a **description** (measured facts, stored as data) and a
**compiled output** (meshes, instance lists and material references, content-addressed). A **location** is a query, `--at E,N
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
- **Areal kinds** (ground, trees, planting) cut the world on the origin-aligned grid of `config.py`: cell `(i, j)` covers
  x ∈ [i·S, (i+1)·S), z ∈ [j·S, (j+1)·S), `i` east, `j` south. S is 62.5 m unless the kind's owner documents another
  power-of-two multiple. Never cut on the query circle.
- Conversions come from `turku3d.world.frame` (which re-exports `turku3d.config`); do not write the origin elsewhere.

## 3. Entity

### 3.1 Identifier

`<kind>/<key>`, for example `building/103456789A`, `ground/c_-6_-4`, `signpost/<register id>`. `kind` is `[a-z][a-z0-9-]*`;
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

1. `discover(area, sources)` reads the sources once per area and returns each entity as a **stub** that carries its
   **source facts** as JSON (a building: its LoD2 rings, register row, terrain line and touching neighbours; a
   ground cell: its height grid and surface polygons). The driver saves them (`facts/`, section 4.1).
2. `describe(stub, stored)` is pure: it builds the **effective description** from the stub's facts plus the
   **stored description** when one exists (what observation and review wrote for that entity, section 4.2). With
   no stored description this is the automatic fit. How stored facts override automatic ones (per wall, per field)
   is the kind's rule, written in its schema.

So rebuilding one entity after its stored description or its generator changed reads no source data: saved facts →
`describe` → `compile`.

### 3.3 Compiled output

One entity compiles to GLB meshes and instance lists.

- **Mesh:** glTF 2.0 binary, one file per entity per level of detail, self-contained, viewable in any glTF viewer.
  - `POSITION` (float32, relative to `origin`), `NORMAL`, indices uint32; `TEXCOORD_0` in **metres** (walls: u along
    the wall, v up from the wall base; ground and roofs: engine x, z in absolute metres) so procedural materials
    tile at world scale and continue across entities; optional `COLOR_0` and custom `_NAME` attributes.
  - One glTF node per **part** (`walls`, `roof`, `plinth`, …), `node.extras = {"entity": id, "part": name}`.
  - One primitive per material. `material.extras.turku = {"family": "plaster", "params": {…}, "surface":
    "opaque"}` is the **material reference**; `pbrMetallicRoughness` (`baseColorFactor` linear, `roughnessFactor`,
    `metallicFactor`) is the fallback every material carries, so an unknown family still renders. The fallback is
    the family's true mean response for those parameters. `surface` says how it is drawn: `opaque`, `masked`,
    `blend` or `decal` (glTF `alphaMode` follows); a surface that emits light is its own primitive with
    `emissiveFactor`.
  - Positions are snapped to 0.1 mm, so equal points are bit-identical. Nothing is quantised, compressed or merged.
  - No embedded textures. A family that needs maps names them in `params` (library ids or store URLs).
- **Material families** are resolved in the client (`game/src/world/location/materials.ts`, one registered
  function per family: `params → three material`). `flat` (`color`, `roughness`, `metalness`) always exists.
  `w2-buildings`, `w2-street` and `w2-look` add families there; a generator may use a family name before the client
  knows it and gets the fallback.
- **Instances:** `{ "asset": <url>, "transforms": [[x, y, z, yaw, sx, sy, sz], …] }`, positions relative to
  `origin`, yaw in radians about +Y (0 = asset's +Z pointing south, positive turns towards east). `asset` is a GLB:
  a store object (a generator hands over parts and the driver packs them; identical assets hash to the same
  object, so the client batches by url) or a path under `game/public/assets/`. The asset's origin is the object's
  **base** (y = 0 is where it meets the ground). Use instances for trees, furniture and repeated kit parts.
- **Objects a player can hit** (fence panels, sign and signal posts, lamps, bollards, benches, trees) are never
  mesh parts of a ground or building entity. Each is one instance row (or its own entity with one row), and the
  list carries what the game needs later, unchanged from generator to manifest:
  - `ids`: one stable id per transform (the register key);
  - `behaviour`: `fixed`, `knock-over` or `breakable` (absent: scenery nothing collides with);
  - `collision`: `{ "shape": "box" | "capsule" | "cylinder", "size": […] }` in metres, asset-local, base at y = 0;
  - `mass`: kilograms of one object, optional;
  - `bounds`: the asset's own box, written by the driver for packed assets.

  Stills only draw them; nothing else reads these fields yet.
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
    "lods": [{ "lod": 0, "url": "objects/3f/3f…9a.glb", "sha256": "3f…9a", "bytes": 48211, "triangles": 1532,
               "error_m": 0.01 }],
    "instances": [],
    "families": ["plaster", "roof-metal", "flat"],
    "instanceTriangles": 0,
    "derived": {}
  },
  "hash": "<sha256 of the canonical JSON of origin, bounds and output>",
  "timings": { "describeMs": 4, "compileMs": 310, "packMs": 6 }
}
```

- `key` decides whether to rebuild (same key: reuse the record). The code hash covers the kind's package and the
  shared packer, so editing a generator rebuilds its entities and nobody maintains version strings.
- `hash` is the **content hash**: it changes exactly when what the client draws changes. The client swaps an entity
  when its `hash` changes and at no other time.
- `bounds` are `[minX, minY, minZ, maxX, maxY, maxZ]` relative to `origin`, instances included.
- **The record and its `lod` 0 mesh are the source of truth of an entity's geometry**: indexed, one primitive per
  material, normals, metric UVs and the material table, as the generator emitted them (a part without normals gets
  flat ones, which splits its vertices: supply normals to keep a welded mesh). Whatever a renderer needs beyond
  that (batched buffers, cluster pages, distance fields, a surface cache) is derived from the store and can be
  regenerated; nothing is authored in a derived form. Such products are listed under `output.derived` (name →
  object) by the tool that builds them and never replace `output.lods`. The rules are in
  [render-readiness.md](render-readiness.md) (`w2-render`).
- `output.lods[].error_m` is the largest deviation of that level from the description (0.01 for `lod` 0; a kind
  states it for a proxy level in `Output.error_m`, 0.5 when it does not).
- `output.instances[]` rows carry the asset's `bounds` and `triangles` when the driver packed it.
- A ground entity also has `output.heightfield`: `{ "url": "objects/…​.bin", "format": "f32le", "size": [columns,
  rows], "origin": [x, z], "spacing": 2.5 }`, the raster its mesh was triangulated from (`Output.heightfield`;
  row 0 is the north edge, engine metres absolute, heights in N2000). The manifest row repeats it.

## 4. Store

Two roots, both plain directories.

### 4.1 Output store: `$TURKU3D_WORLD_STORE` (default `$TURKU3D_WORK/world`)

```
objects/<aa>/<sha256>.<ext>   immutable blobs: .glb meshes, .json descriptions and records, .bin, .png/.ktx2
builds/<key>.json             entity record by build key (immutable)
facts/<kind>/<key>.json       source facts of an entity from its last discovery (replaced when the sources change)
locations/<name>.json         location manifests (mutable)
```

- Every blob is named by the SHA-256 of its bytes; `<aa>` is its first two hex digits. Writes are
  write-to-temp-then-rename. Nothing in `objects/` or `builds/` is ever modified, so two stores merge with
  `rsync -a --ignore-existing`, and a stage can run on any host and ship its results back.
- It is per sandbox by default because generator code differs between sandboxes. It is scratch: delete it freely;
  `python -m turku3d.world gc` drops objects that no manifest in `locations/` references.
- The dev server serves it at `/world/…` (`/world/objects/…` immutable, `/world/locations/…` no-store,
  `/world/events/<name>` the update notices of section 8).

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
- `sources/` is a cache of public source data (`wfs/`, `laz/` and `dem/`, `osm/`, `ortho/`; panorama coverage
  stays in the shared panorama cache). `$TURKU3D_DATA/raw` stays read-only; readers look there first and fetch into `sources/` only what is
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
      "lod": 0, "url": "objects/3f/3f…9a.glb", "bytes": 48211, "triangles": 1532, "error_m": 0.01,
      "instances": []
    },
    {
      "id": "signpost/LM-20417", "kind": "signpost", "tier": "auto", "role": "scene",
      "hash": "…", "origin": [-352.1, 12.4, -238.0], "bounds": [-0.4, 0.0, -0.4, 0.4, 3.2, 0.4],
      "instances": [{
        "asset": "objects/a1/a1…07.glb", "bounds": [-0.35, 0.0, -0.05, 0.35, 3.2, 0.05], "triangles": 148,
        "transforms": [[0, 0, 0, 1.571, 1, 1, 1]], "ids": ["LM-20417"],
        "behaviour": "knock-over", "collision": { "shape": "cylinder", "size": [0.03, 3.2] }, "mass": 18
      }]
    }
  ],
  "stats": { "entities": 214, "triangles": 412000, "bytes": 9100000, "instances": 96, "byKind": { "building": 61 } },
  "sources": { "osm": { "features": 154 }, "ortho": { "layer": "ortho2022to", "px_m": 0.2, "valid": 1.0 },
               "panoramas": { "lookaround": 298, "google": 89 } },
  "timings": { "sourcesMs": 800, "discoverMs": 920, "describeMs": 300, "compileMs": 5200, "totalMs": 6500 }
}
```

- URLs are relative to the store root (`/world/` on the dev server).
- `role` is `scene` (inside `radius`, `lod` 0) or `background` (between `radius` and `background`, cheap level of
  detail). The client treats them alike.
- `focus` is the query centre on the ground in engine metres: where an interactive session starts.
- `sources` says what exists around the query in the sources that kinds and the observation chain read besides
  the registers: `build` fetches them for the area even when no registered kind uses them yet, and a source that
  fails is reported there (`{"error": …}`) without stopping the build.
- `failures` (`[{ "id", "error" }]`) names the entities whose generator raised: they are left out of `entities`,
  `build` prints `FAILED <id>: <error>` for each, and the rest of the location is built. An empty list is the
  normal case; a kind fixes its failures, the client never draws a half entity.
- Every entity row has `bounds` and a stable `id`; every instance list has the asset's `bounds` and, for objects
  the game addresses, one id per row. A city-wide hierarchy can cull and stream both without opening a mesh.
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
        """The entities of this kind that touch the area: id, survey box, source references and the source
        facts their description needs, as JSON values. All source reading happens here. An entity's stub must
        not depend on the area beyond its selection: the same entity from any query."""

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        """The effective description (section 3.2): the stub's facts merged with the stored description. Pure."""

    def compile(self, description: dict) -> Output:
        """Description in; mesh parts, material references and instances out. Pure and deterministic:
        no source access, no clock, no randomness that is not seeded from the entity id."""
```

```python
@dataclass
class Stub:
    id: str                           # "building/103456789A"
    box: tuple[float, float, float, float]   # survey bounds e0, n0, e1, n1
    sources: list[dict]               # [{"source": "turku-lod2", "key": "…"}]
    facts: dict                       # JSON: everything describe() needs

@dataclass
class Material:
    family: str                       # "plaster"; resolved by the client, section 3.3
    params: dict                      # JSON; the family's parameters
    color: tuple[float, float, float] = (0.6, 0.6, 0.6)   # fallback base colour, linear RGB
    roughness: float = 0.9
    metalness: float = 0.0
    double_sided: bool = False
    surface: str = "opaque"           # "opaque" | "masked" | "blend" | "decal"
    emissive: tuple[float, float, float] | None = None    # linear RGB of a surface that emits

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
    asset: str | list[Part]           # a GLB url (/assets/… or store), or parts the driver packs into one
    transforms: np.ndarray            # (n, 7): x, y, z, yaw, sx, sy, sz relative to Output.origin
    lod: int = 0
    ids: list[str] | None = None      # one stable id per transform
    behaviour: str | None = None      # "fixed" | "knock-over" | "breakable"
    collision: dict | None = None     # {"shape": "box" | "capsule" | "cylinder", "size": […]}, base at y = 0
    mass: float | None = None         # kilograms of one object

@dataclass
class Heightfield:                    # ground only: the raster the mesh was triangulated from
    origin: tuple[float, float]       # engine x, z of heights[0, 0], absolute
    spacing: float                    # metres between lattice points
    heights: np.ndarray               # (rows, columns), N2000 metres; row 0 is the north edge

@dataclass
class Output:
    origin: tuple[float, float, float]            # engine metres, absolute
    parts: list[Part]
    instances: list[Instances] = field(default_factory=list)
    heightfield: Heightfield | None = None
    error_m: dict[int, float] = field(default_factory=dict)   # largest deviation from the description, by lod
```

- The driver packs `parts` into GLB (`turku3d.world.glb`), hashes, stores and writes the record. A generator never
  writes files or touches the store.
- `Sources` (`pipeline/turku3d/world/sources/`) gives area reads anywhere in the city. A reader looks in
  `$TURKU3D_DATA/raw` first and fetches what is missing for the area into `$TURKU3D_WORLD_DATA/sources/`
  (section 4.2):

  | Read | Source | Missing data |
  | --- | --- | --- |
  | `sources.wfs.features(layer, box)` | any city WFS layer: LoD2 buildings, registers, surface polygons | 500 m blocks, fetched concurrently |
  | `sources.terrain.heights(e, n)` | lidar ground key points as a 1 m grid | 500 m LAZ tiles |
  | `sources.osm.features(box)` | OpenStreetMap points, lines and areas with tags, in the survey frame | one city-wide table, cut once from the national extract (about three minutes) |
  | `sources.ortho.image(box, px_m)` | orthophoto pixels at 0.05 to 0.8 m | 1024-pixel chunks from the city's WMS |
  | `sources.panoramas.spots(box)` | Street View and Look Around panoramas (`turku3d.observe.panoramas`) | coverage tiles |

  A track may keep further readers in its own package; they follow the same rule and time themselves with
  `sources.timed(name)` so `build` prints them.
- A building is a LoD2 building, or a register footprint (`kanta:Rakennus`, building and outbuilding classes) that
  no LoD2 building covers, raised to a flat-roofed box by its storeys: `shell.source` says which
  (`turku-lod2`, `turku-kanta-footprint`). New buildings and most outbuildings come this way.
- Budget: `compile` of one entity in about a second or less; `describe` of one entity in milliseconds. Slow one-off measurement (vision reads, panorama rectification) is not a
  generator step: it runs in `w2-observe`'s chain and writes stored descriptions.
- A kind owns its description schema version. Bump `<n>` in `turku-description/<kind>/<n>` on an incompatible
  change and keep `describe` able to read the stored descriptions already written, or migrate them.
- The stand-in kinds of the walking skeleton are replaced in place by their owners, who delete what they replace:
  `building` (`kinds/building_shell.py`: LoD2 shell, register colour) by `w2-buildings`, whose kind keeps using
  `building_shell.discover` for the shell and register facts; `ground` (`kinds/ground.py`: lidar relief with the
  city's surface polygons) by `w2-street`; `trees` (`kinds/trees.py`) by `w2-green`. New kinds (`w2-objects`:
  `signpost`, `signal`, `lamp`, `pole`, `fence`, `furniture`; `w2-green`: `planting`) add a row to `KINDS`.
- The build key hashes the kind's module or package plus the shared mesh code. A kind that compiles with code
  outside its package lists those paths in a `code` attribute.

## 7. Commands

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.world build --area humalistonkatu        # a pilot area, from its views
bash pipeline/env/run.sh python -m turku3d.world build --at 23459140,6704730 --radius 150
bash pipeline/env/run.sh python -m turku3d.world entity building/103456789A --location humalistonkatu
bash pipeline/env/run.sh python -m turku3d.world refresh --location humalistonkatu  # after a generator change
bash pipeline/env/run.sh python -m turku3d.world watch --location humalistonkatu    # rebuild on description change
bash pipeline/env/run.sh python -m turku3d.world describe building/103456789A       # its effective description
bash pipeline/env/run.sh python -m turku3d.world gc                                 # drop unreferenced meshes
node scripts/dev/scene-capture.mjs --mode render --world humalistonkatu --area humalistonkatu \
  --views scripts/dev/views/pilot-locations.json --out "$TURKU3D_WORK/captures/humalistonkatu"
```

- `build` prints per-source and per-kind timings and entity counts and writes `locations/<name>.json`. `--area
  NAME` derives the query from the cameras of that area's reference views (`qa/views/views.jsonl`) and what they
  look at; `--at` works anywhere in built-up Turku and fetches missing source data. `--background M` (default 400)
  is how far the cheap surroundings reach.
- `build` discovers and describes in one process and compiles the entities that have no stored build in
  `TURKU3D_JOBS` processes (the sandbox sets it; one process below eight entities per process). `compile` must
  therefore depend on its description alone: no module state filled by `discover`.
- `entity`, `refresh` and `watch` rebuild from the saved facts (section 3.2) and rewrite the manifest only when
  something a client draws changed.
- The game draws a location at `/?world=<name>&cam=E,N,H,yaw,pitch,fov` (render mode): the same client and
  renderer, with the location loader (`game/src/world/location/`) in place of the prepared scene. Play in a
  location waits for collision. `scene-capture.mjs --world NAME` adds the parameter and records the manifest
  revision, the build timings and the drawn entities per kind in the capture manifest; `--area NAME` keeps only
  the views of that area; the comparison sheet shows the reference photo beside the still, and with `--baseline
  DIR` (an earlier capture of the same views, for example the frozen game's) that render between them.
  `--ready-timeout SECONDS` (default 240) is how long the capture waits for the location to finish loading.
  A page follows its location's manifest, so a revision that arrives while a capture loads or shoots redraws the
  scene under the camera: `build`, `refresh` and `entity` keep `building/<name>.json` (host, pid, start) in the
  store while they run, and `scene-capture.mjs --world NAME` refuses to start while that marker's process lives.
- `gc` deletes the meshes that no location manifest in the store references, and the stored builds that pointed
  at them, so a build key that comes back compiles again. Delete the manifests of locations you no longer draw
  first (`locations/<name>.json`).
- Client materials: a kind's families are registered with `registerFamily(name, fn, kind?)`
  (`game/src/world/location/materials.ts`). Give the kind when the family name is not yours alone (`metal`,
  `stone`): a family registered for a kind draws only that kind's entities. A family nobody registered goes to
  the game's own material pass under its name (`asphalt`, `granite`, `grass`, `water`, …).
- The loader draws entities in chunks: the entities of one renderer layer (ground, buildings, everything else)
  whose origins fall in the same 250 m square are styled, batched and compiled as one model, as a tile is in the
  game. One model per entity did not finish loading 1,952 entities in ten minutes on the M1 Max.
- In-process, for a workbench: `turku3d.world.driver.compile_entity(store, kind, description)` returns the
  record's origin, bounds, output and hash.

### Where a build runs

Measured 2026-11-12 with the same code at two suburban points (radius 150 m, background 400 m, nothing cached):

| Stage | Mac (M1 Max) | osgallery-dev | Runs on |
| --- | --- | --- | --- |
| Registers over WFS, fetch and parse | 7 s (51 blocks, 7 layers) | 35 s (81 blocks, 11 layers) | the build's host |
| Lidar ground: fetch the tiles, 1 m grids | 8 s | 31 s | the build's host; grids are cached in shared data |
| Orthophoto 0.2 m, panorama coverage | 3 s, 5 s | 3 s, 6 s | the build's host |
| OpenStreetMap table of the city (once) | 181 s | not measured | any host once; it travels with `--data world/sources/osm` |
| Compile ground, 157 to 159 cells | 5.0 s | 24.2 s | Mac or Air |
| Compile buildings, 285 to 319 shells | 0.8 s | 2.6 s | Mac or Air |
| Copy to the host and merge the store back (`scripts/dev/on-host`) | none | 5 s | |

- **One location, or one entity while you work: the Mac** (or the Air, whose cores are as fast and which is idle:
  `scripts/dev/on-host air -- python -m turku3d.world build …`). Every processor-bound stage is three to five
  times slower per core on osgallery-dev, and a location is seconds of work.
- **Kinds that read whole lidar point clouds** (objects, vegetation) fetch hundreds of megabytes per 500 m tile:
  build new places on the Air or osgallery-dev so those caches stay off the Mac, and let the store merge back.
- **Many locations at once** (a random set, a district): osgallery-dev or a cloud lease, one `build` process per
  location; the store is content-addressed, so results from any host merge with `rsync -a --ignore-existing`,
  which `on-host` does.
- **Stills:** a browser slot on the Mac or the Air (`~/turku3d/loops/w2/hosts.md`); osgallery-dev has no GPU.

## 8. Live update of one entity

1. A stored description changes (observation, review, the workbench), or a kind's code changes.
2. `world entity <id> --location <name>` (or the running `world watch`, which polls the stored descriptions of the
   location's entities and restarts itself when pipeline code changes) re-describes and recompiles that entity
   only, writes its new objects and record, and replaces `locations/<name>.json` atomically with `revision + 1`.
   Target: about one second.
3. The dev server watches `locations/` and sends `{ name, revision }` to the pages that listen on the
   server-sent event stream `/world/events/<name>` (the repository runs Vite without its hot-update socket).
4. The client's location loader re-fetches the manifest and diffs it against what is drawn, by entity `id` and
   `hash`: it loads the files of every chunk that holds a changed, added or removed entity, then takes the old
   chunk out and draws the new one. Camera, lighting, time and every other chunk stay as they are. No page reload.
5. The same refresh is callable: `await __dev.world.refresh()` resolves `{ revision, changed, added, removed, ms }`
   once the swapped entities are drawn. `scene-capture.mjs` calls it before a capture, so a still never races an
   update.

`__dev.world` also has `manifest()`, `entity(id)` (its manifest row and three.js object), `stats()` (drawn
entities, triangles, instances and mesh bytes by kind) and `last` (the report of the last automatic refresh).

## 9. Not in these contracts yet

Stills only for now: collision, physics, streaming and multiplayer are not built, and nothing here prevents them
(collision can be a further output per entity, and hittable objects already carry their behaviour and shape;
streaming is a spatial index over entity rows; a multiplayer room pins a set of content hashes). Frame time,
triangles and texture memory are recorded per location by the capture, without a limit.

The client draws every entity as its own object for now: simple to swap, about a thousand draws per location.
Batching by material family, a hierarchy of levels of detail and virtualised geometry are renderer products
derived from the same records ([render-readiness.md](render-readiness.md)); the only runtime target is Chrome on
an Apple-silicon Mac with WebGPU.
