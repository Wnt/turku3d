# Render readiness: virtualised geometry and software global illumination

2026-11-12. Owner: `w2-render`. These rules keep the W2 world ready for techniques like Unreal's Nanite and
software Lumen ([plan](world-pipeline.md), design constraint 6). Nothing of the kind is built in this campaign:
stills still come from the three.js WebGPU renderer. The rules exist so that no description, generator or asset has
to be re-authored when those techniques come. Change a rule here, in place, and say so on the campaign board.

`python -m turku3d.world check-readiness` (section 9) reports how a compiled location obeys them. It reports; it
never blocks a merge.

**Platform (Jonni, 2026-11-12):** the only runtime target is Chrome on an Apple-silicon Mac (the M1 Max). Rules and
formats may rely on WebGPU as Chrome offers it there (storage buffers, compute, indirect draws, uint32 indices,
float textures) and need no WebGL2, mobile or other-browser fallback. Stock three.js is not a constraint either:
section 11 will recommend the renderer path.

## 1. What the two techniques need from an asset

- **Virtualised geometry** cuts each mesh into clusters of about a hundred triangles, groups neighbouring clusters,
  simplifies each group with its outer boundary locked and repeats, which gives a graph of levels of detail. At run
  time the clusters whose error is under a pixel are drawn into a visibility buffer (triangle and instance id per
  pixel), and materials are evaluated once per pixel from that id. It needs dense real geometry, connected
  topology, instances, opaque surfaces and materials that are data, not code per object.
- **Software global illumination** traces rays against signed distance fields (one per mesh asset, merged into a
  coarse global field) and looks up lighting in a surface cache: a few flat "cards" per object that hold albedo,
  normal, emission and lit radiance. It needs closed solids with thickness, objects simple enough for a handful of
  cards, terrain as a height field, and explicit albedo, roughness and emission.

## 2. What changes this week, per track

| Track | Do this now |
| --- | --- |
| `w2-buildings` | A building is one closed solid: every wall including party and yard walls, roof, and a bottom (G2, F1). Each opening ends in an opaque `glass` surface (F2). Repeated opening assemblies, balconies, dormers and chimneys are `Instances` (I1). The fallback colour is the family's true mean (M2). |
| `w2-street` | The ground cell is a height field whose raster is stored and whose border vertices equal the neighbour's (F3). No floating or stacked layers: markings partition the mesh or are tagged `decal` (F4). Kerbs, steps, walls and quays are closed geometry; walls and quays are their own entities (G1, G2). |
| `w2-green` | A tree asset is generated skeleton first; trunk and boughs are closed geometry, leaves are twig-cluster cards in thinning order (T1, T2). Trees are library instances scaled per tree, not unique meshes (I1). Hedges have a solid core (T3). |
| `w2-objects` | Posts, rails, pickets, plates and housings are closed solids at true thickness, not double-sided quads (G2, F5). Alpha masks only for fine meshes, with the pattern in the description (F5). One shared asset per family and size (I1). Lenses and lit faces are their own primitive with `emissive` (M2). |
| `w2-core` | The lod 0 GLB stays the lossless source; runtime encodings are extra derived objects (S1). Packer: snap positions to 0.1 mm, write `surface` and `emissive` (G3, M2, M3). Record: asset bounds and triangles per instance row, `error_m` per level, `heightfield` (I2). |
| `w2-observe` | Read real depths (`recess_m`, `projection_m`, `depth_m`) where a view shows them; they become geometry (G1). |

## 3. Source of truth and derived products

- **S1. Three layers, only the first is authored.** (1) Stored descriptions and source references are the source of
  truth. (2) What `compile` returns (`Output.parts`, `Output.instances`) is the **source mesh**; the lod 0 GLB in
  `objects/` is its lossless encoding: float32 positions relative to `origin`, uint32 indices, normals, metric UVs,
  one node per part, one primitive per material, no quantisation, no mesh compression, nothing merged across
  entities. (3) Everything a renderer wants beyond that (compressed or merged GLB, flattened instances, baked
  textures, cluster pages, distance fields, cards) is a **derived object**, listed in the record under
  `output.derived` and rebuilt from layer 2 by a tool, never written by a kind and never replacing `output.lods`.
  *Why: cluster graphs, distance fields and cards are built offline from the unmodified mesh; an optimised runtime
  mesh cannot be turned back into one.*
- **S2. `lod` 1 is a proxy, not a decimation.** A kind's `lod` 1 is a separate closed stand-in for the far ring (a
  building's shell) built from the same description. The level-of-detail graph is always derived from lod 0, so
  never thin lod 0 to save triangles. *Why: simplification cannot remove window holes or merge small solids, so far
  proxies must come from the description; near levels must come from the full mesh.*
- **S3. One source, every fidelity.** The source is authored once, at the fidelity wanted on strong hardware (G4).
  Every cheaper form is derived from it by a parameter of the renderer: the level drawn at a distance, how many
  leaf cards of the prefix (T2), texture pixels per metre, the lighting tier. No description, kind or GLB names a
  quality tier or is tuned to one machine's budget, and nothing a frame needs is known only after loading a whole
  location: bounds, error and triangle counts are in the rows (I2). *Why: the long-run target is 40 frames per
  second on the M1 Max with fidelity rising by itself on stronger hardware; that works only if cost sits behind
  parameters of derived products, never inside authored data.*

## 4. Geometry for cluster hierarchies

- **G1. Real geometry for anything 3 cm deep.** Whatever stands 3 cm or more in front of or behind its surface is
  modelled: reveals, frames, sashes, sills, surrounds, cornices, plinths, pilasters, balconies, kerbs, steps,
  posts. No normal-mapped or painted depth for these, no card where a solid stands. Periodic relief that a family's
  metric parameters describe completely (board laps, brick joints, standing seams, tile profiles, setts) stays in
  the material, with the true dimensions in `params` (`scale_m`, `profile`, joint depth) so displacement can
  generate it later. *Why: a cluster hierarchy can only refine what the source mesh contains, and a baked illusion
  has no distance field and no card.*
- **G2. Build from closed solids.** Every connected piece is a closed two-manifold: each edge is shared by exactly
  two triangles, faces wound counter-clockwise seen from outside. Solids may overlap and penetrate one another (a
  sill pushed into a wall); they are never open sheets or single faces "at a depth". A wall face, its reveals and
  the surface behind them are one dented skin joined along shared edges. *Why: an open border cannot be simplified
  and stays locked through every level; a distance field needs an inside; a card that sees a back face stores
  black.* Thin things: F5.
- **G3. One point, one position; no T-junctions.** Vertices at the same point have bit-identical positions (reuse
  the array, or round to 0.1 mm before emitting; the packer will snap). Normal and UV seams may split a vertex.
  Never leave a vertex in the middle of another triangle's edge: cut both sides at the same points. No zero-area
  or duplicate triangles. *Why: cluster builders weld by position to find adjacency; a T-junction is a crack once
  one side is simplified.*
- **G4. Density follows what a street camera resolves.** At 1080p a pixel is 5 mm at 5 m and 2 cm at 20 m. Model
  edges down to 1 cm, keep curve chord error under 5 mm (an arch 12 to 16 segments, a 10 cm post 8 sides, a pipe
  6), and do not subdivide flat faces: a flat sign face is two triangles and its picture is a texture. Expected at
  lod 0, per m² of mesh surface: a building 2 to 60 triangles with its openings, ground 0.3 to 10; in all, a
  street object 100 to 3 000 and a tree 3 000 to 30 000. The check flags more than 1 000 triangles per m² and
  meshes with a fifth of their triangles under 0.25 cm². *Why: sub-pixel triangles cost most in every rasteriser,
  and the hierarchy removes detail but cannot add it.*

  | View distance | One pixel (1080p, 60° vertical) | Smallest feature worth geometry | Representation |
  | --- | --- | --- | --- |
  | 5 m | 5 mm | 1 to 2 cm | lod 0, full |
  | 20 m | 2 cm | 5 cm | derived from lod 0 |
  | 80 m | 8 cm | 20 cm | derived from lod 0 |
  | 300 m | 30 cm | 1 m | proxy (`lod` 1) |
  | over 1 km | over 1 m | block massing | merged proxies per cell (later) |
- **G5. Do not merge, hide or thin.** Do not merge entities, or parts with different names, into one mesh. Do not
  drop walls, roofs or undersides because a street camera does not see them. Do not delete triangles hidden inside
  other solids. Keep `Part.name` semantic and stable (`walls`, `roof`, `plinth`, `frames`, `glass`, `trim`).
  *Why: parts are the pieces that get cards and fields; merging and hidden-surface removal are runtime decisions
  made from the source, per renderer.*

## 5. Geometry for distance fields and surface cards

- **F1. One entity is one real object, closed on its own.** A building keeps its party walls and has a bottom face
  at least 0.3 m below the lowest terrain point of its `shell`; row houses stay separate solids. Nothing relies on
  a neighbour or the ground to close it. *Why: a field is built per object and instanced; light leaks through any
  side an object leaves open.*
- **F2. Every opening ends in an opaque surface.** The glazing of a building is an **opaque** primitive of family
  `glass` that closes the recess; what shows behind it (`glass.interior`, curtains, blinds) is a per-pixel effect
  of the family, or real geometry in front of an opaque back. A gateway with `passage: through` is a tunnel through
  the solid; `recess` ends in a back wall. Truly clear glass (shelters, glazed balconies, balustrades) is a closed
  slab at least 8 mm thick in its own part with `surface: "blend"`. *Why: blended surfaces are in neither the
  visibility buffer, the distance field nor the surface cache; a wall closed only by them is a wall full of holes.*
- **F3. Ground is a height field.** A ground cell is a single-valued surface over (x, z): no overhangs, no
  underside (the buried bottoms of kerb stones are tolerated up to 1 % of the surface). Its height raster (lattice spacing, origin, heights) is stored beside the mesh and named in the record
  (`output.heightfield`); the mesh is any triangulation within 2 cm of it with kerb lines as edges. Vertices on a
  cell border are bit-identical in both cells: sample the same raster at the same lattice points and cut surface
  polygons at the same border points. Bridges, quay walls, retaining walls and steps are closed solids of their own
  entities, not ground. *Why: terrain is traced and cached as a height field, far cheaper and leak-free; a cluster
  graph across cells needs matching borders.*
- **F4. No stacked or floating layers.** A point of the ground or of a wall belongs to exactly one surface.
  Markings, patches and manhole covers either partition the mesh (their own triangles, coplanar, sharing edges) or
  are a separate part whose material has `surface: "decal"`, at most 5 mm above its host. *Why: coincident layers
  fight in depth as soon as either is simplified, and double up in a field or a card; a tagged decal can be
  rasterised into its host's surface instead of drawn.*
- **F5. Thin things are still solids.** Anything under 10 cm in its smallest dimension (boards, pickets, rails,
  posts, sign plates, brackets, wires) is a closed solid at its true thickness (a sign plate is a 3 mm slab, a wire
  a tube of at least 1.5 cm), not a double-sided quad. Alpha-masked surfaces are only for patterns with gaps under
  3 cm (chain-link, welded mesh, expanded metal, leaf cards): `surface: "masked"`, with the pattern's dimensions
  and its `coverage` (opaque share, 0 to 1) in the description and in `params`. Each such object is its own
  instance row. *Why: under the voxel size (10 to 20 cm near the camera) an object cannot be in a distance field
  and is handled per instance by screen traces and coverage; opaque geometry needs no texture fetch in the
  rasteriser, and a later geometry-only path needs the pattern, not the mask.*
- **F6. Keep objects card-sized.** A part or instanced asset should be describable by about a dozen flat cards
  along its axes: walls, roof, a balcony, a dormer, a chimney, a fence panel. Keep one entity under about 60 m;
  long runs (fences, walls, kerb-side railings) are rows of panels, not one mesh. *Why: card count and field
  resolution are capped per object; a huge or folded object is lit from too few, too coarse samples.*

## 6. Instancing and entity granularity

- **I1. Repeats are instances.** An assembly that occurs more than twice with the same geometry is an `Instances`
  row with `asset=[parts…]`: the opening assembly of one opening type at one size (frame, sashes, bars, sill,
  surround, pediment, leaf), a balcony unit, a dormer, a chimney, a bracket, a fence panel and post, a lamp head, a
  sign plate, a bollard, a tree of the library. The driver content-addresses assets, so identical ones from any
  entity or kind become one store object. The host keeps what is unique to it (the wall with its holes and the
  opaque surface behind each). Whether today's renderer draws instances or a flattened copy is a derived choice
  (S1). *Why: cluster graphs, distance fields and cards are built and stored once per asset; a city of unique
  window meshes is roughly ten times the data and build time.*
- **I2. Bounds and ids everywhere.** Every entity has `id`, `hash`, `origin`, `bounds` (exists). Every instance row
  of a real-world object has `ids` (exists). For a city-wide hierarchy and streaming the record and manifest also
  carry, per instance row, the asset's local `bounds` and `triangles`, and per level `error_m` (the largest
  deviation of that level from the description: 0.01 for lod 0, about 0.5 for a shell). Nothing in a row depends on
  the query. *Why: culling and level selection run on bounds and error before any geometry is fetched.*

## 7. Materials

- **M1. A family is one function; a material is one row.** `params` is a flat record of numbers, booleans,
  `#rrggbb` colours, enum strings and texture-library ids: no nested objects, no free text, no per-entity texture.
  Parameter names are spelled as in the building description (`colour`, `colour2`, `scale_m`); a colour that
  varies per object (a fence's paint) is quantised to a palette, so that equal geometry stays one asset.
  Each family declares its parameters (name, type, default, enum values) in
  `pipeline/turku3d/world/families.json`; the check lists unknown families and parameters. In the client a family
  is one TSL function of a parameter block, surface position, metric UV and normal; numbers and colours enter as
  uniforms, never as constants baked into the node graph, and enums select among at most eight variants. Aim for
  under 40 families in the world. *Why: a visibility buffer shades every pixel from a material id: one shader per
  family, one table row per material.*
- **M2. Explicit, truthful fallback values.** `Material.color`, `roughness` and `metalness` are the family's mean
  response for those parameters (what a square metre averages to), not a placeholder; `Material.emissive` (linear
  RGB) is set on every surface that emits, and such a surface (luminaire lens, signal lens, lit sign) is its own
  primitive. *Why: the surface cache, far proxies and any low-resolution lighting read these four values instead of
  running the family.*
- **M3. Say how a surface is drawn.** `Material.surface` is `opaque` (default), `masked`, `blend` or `decal`.
  Opaque wherever the real thing is opaque. `double_sided` only with `masked`. Masked area stays under 5 % of an
  entity's surface outside foliage. *Why: opaque is the cheap path of a visibility buffer, masked costs a texture
  fetch while rasterising, blend needs a separate pass and is invisible to global illumination.*
- **M4. Metres, at one density.** `TEXCOORD_0` is metres along the surface with under 5 % stretch on every
  triangle; returns and reveals continue the UV around the corner instead of projecting. Where UVs address an atlas
  (leaf cards, sign faces) the material says `uv: "atlas"` and the atlas is drawn at a stated density. The reference
  density for anything rasterised is 512 px/m (sign faces may use 1024). *Why: equal texel density is what lets one
  virtual texture, or one procedural evaluation scale, serve the whole city; a metric UV per flat part is already a
  unique unwrap for a future texture cache.*

## 8. Foliage

- **T1. Skeleton first.** A tree asset is generated from species, parameters and a seed: an explicit branch
  skeleton, then closed tube geometry for the trunk and every bough of 3 cm or more (`bark`), then leaves. The
  family table stores what a geometry-only path needs: branching parameters, and per species the leaf's shape,
  length, width and leaves per twig cluster. No crossed billboards, no cards scattered on a crown ellipsoid, no
  photographed tree planes. *Why: both today's cards and later leaf geometry or voxel crowns are generated from the
  same skeleton and leaf parameters.*
- **T2. Leaf cards are twig clusters in thinning order.** Each card is one twig cluster placed on the skeleton, at
  most 60 cm across, `surface: "masked"`, `double_sided`, `uv: "atlas"`; its atlas cell is drawn from the species'
  leaf parameters. Cards are emitted in a seeded shuffle, so that any prefix of the list is an even thinning of the
  crown. *Why: loose cards cannot be simplified by edge collapse; a prefix scaled up by area is the level-of-detail
  chain, and each card marks where a geometry cluster goes.*
- **T3. Hedges and shrubs have a solid core.** A hedge is a closed solid at the measured profile with an opaque
  `hedge` surface, plus a fringe of cards (T2). *Why: the core occludes and takes cards; cards alone leak light and
  vanish in a distance field.*
- Trees are library instances (I1): species × size class × a few seeds, fitted to each measured tree by the row's
  scale. A unique mesh only for a tree that data marks as notable.

## 9. The readiness check

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.world check-readiness --location humalistonkatu     # scene entities
bash pipeline/env/run.sh python -m turku3d.world check-readiness --location portsa --all --top 5
bash pipeline/env/run.sh python -m turku3d.world check-readiness --glb building.glb --kind building   # any GLB
```

Code: `pipeline/turku3d/world/readiness/`. It reads what the store holds (the manifest, the lod 0 GLBs and the
instanced assets), prints a summary per kind with the worst entities of each kind and the rule each misses, and
writes everything to `$TURKU3D_WORK/readiness/<location>.json`. `--store DIR` reads another sandbox's store. It
takes about 12 s for the 470 scene entities of Humalistonkatu and exits 0 whatever it finds.

| Measured | How | Flag |
| --- | --- | --- |
| Closed or open (G2) | Positions welded on the packer's 0.1 mm grid; border edges split into true openings, T-junction seams and near misses (closed by a 2 mm weld); pieces joined through edges, each closed or not; winding; per part | open border over 5 cm, inside-out solids |
| T-junctions (G3) | A border edge that carries another border edge's midpoint; also zero-area and duplicate triangles | seams or near misses over 2 cm, any bad triangle |
| Thickness (F5) | Per closed solid 2 × volume / area (a slab's thickness, half a rod's diameter); 256 rays along the inward normal | none: thin objects are listed, not faulted; double-sided opaque surface is |
| Distance field (F1, F2) | Opaque triangles voxelised at the object's field resolution (extent / 96, 2 to 25 cm) and at 2× and 4×, flooded from outside; from just inside the surface, the share of axis directions that escape | a mesh with borders whose flood finds no inside, or that lets over 2 % of directions out |
| Ground (F3, F4) | Share of surface facing down, share of plan under more than one layer, vertices on shared cell borders compared between neighbours | underside over 1 %, stacked over 2 %, any unmatched border |
| Density (G4) | Triangles per m², share under 0.25 cm² | over 1 000 per m², or a fifth small |
| Instances (I1) | Instanced share of drawn triangles, unique assets, reuse factor; inside a mesh, loose solids of equal shape and the share of triangles with two or more congruent parallel copies | over half the triangles of a mesh of 2 000 or more are repeats |
| Materials (M1 to M3) | Rows and families per entity and location, against `pipeline/turku3d/world/families.json`; nested parameters; placeholder fallback; surface share opaque, masked, blend, decal | unknown family or parameter, placeholder, masked over 5 % outside foliage |
| UVs (M4) | UV edge length against surface edge length, unless the material says `uv: "atlas"` | over 5 % of the surface off by more than 5 % |
| Cluster hierarchy | meshoptimizer (`clusterlod.mjs`): meshlets of 128 triangles, groups of 8 simplified to half with shared vertices locked, repeated; the share of triangles still needed at an error of 2 cm, 8 cm and 30 cm (one pixel at 20, 80 and 300 m), with open borders locked and, at 30 cm, free | over 25 % still needed at 300 m in a mesh of 1 000 or more |

First findings (2026-11-12, Humalistonkatu) are on the campaign board and in `~/turku3d/loops/w2/status/w2-render.md`.

## 10. Requests to the contract (`w2-core`)

1. `Material.surface` and `Material.emissive`; the packer writes glTF `alphaMode` and `emissiveFactor` and
   `extras.turku.surface`.
2. The packer snaps positions to 0.1 mm and changes nothing else (unwelding for flat normals keeps positions equal,
   which is enough).
3. Record: `output.instances[].bounds` and `.triangles` for each asset, `output.lods[].error_m`,
   `output.heightfield` for ground, and an `output.derived` map for S1.
4. The lod 0 GLB stays uncompressed and unmerged; the client may merge or flatten at load.

## 11. Renderer path and feasibility

[renderer-path.md](renderer-path.md) holds the recommendation (our own GPU-driven world passes in WGSL on the same
device and frame as an unforked three.js, which keeps dynamic objects, sky, water and the post chain), the
measurements on the M1 Max behind it, how cluster culling, a visibility buffer and distance-field tracing map onto
WebGPU in Chrome on Apple silicon, sizes for a city of Turku's extent, how one source scales to every fidelity
(rule S3), and the decisions that are expensive to reverse. Three of them bear on the rules above:

- WebGPU offers the M1 family no efficient compute rasteriser, and measurements show it does not need one: triangles
  are drawn by the hardware rasteriser and their number on screen is set by the cluster hierarchy alone. Rules G3 and G5 (shared borders, nothing merged or
  thinned by hand) are what lets the hierarchy do that.
- WebGPU has no bindless textures: materials are evaluated per pixel from flat rows, so a family is procedural or
  samples a few shared array textures. No entity brings its own image (rules M1 and M4).
- Every visible instance costs at least one cluster. Repeats stay instances (rule I1), and the far field comes from
  merged proxies of parent cells, a derived product of the partition, not from the assets.
