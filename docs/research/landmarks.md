# Landmarks: churches, ships and other special objects

Research date: 2026-10-27. Question (Jonni): *churches, ships and other special objects must be modelled
with different logic than houses. How does Google Maps' 3D mode get them to look so good, and is there an
open-source implementation that works with our source material?*
Tags: **[V]** measured here, **[U]** from documentation or judgement.

## Short answer

Google does not model landmarks at all: its 3D mode is a **photogrammetric mesh** of everything the aircraft
saw, so a cathedral costs the same as a shed. We have no oblique multi-view imagery of Turku and may not reuse
Google's mesh, so that path is closed. What we do have is enough for a cheaper version of the same idea for a
short allowlist of landmarks: a **dense mesh straight from the full-density city lidar** (no roof-shape
fitting), the **true ortho on its roofs**, and **our own generated wall materials**. The prototype in
`pipeline/turku3d/landmarks/` does this for Turku Cathedral; its measured numbers follow in
`docs/reports/tool-landmark-meshes.md`.

## 1. How Google's 3D mode works, and why we cannot copy it

- Google's photorealistic city meshes are made by **aerial photogrammetry**: aircraft fly overlapping
  nadir *and oblique* camera passes, bundle adjustment solves the camera poses, dense multi-view matching gives a
  point cloud, and a surface mesh is extracted and textured from the same photos ([KIT talk "Photogrammetry
  at Google scale"](https://www.gik.kit.edu/english/2589.php); oblique capture explained in
  [GPS World](https://www.gpsworld.com/a-straightforward-explanation-of-oblique/) and
  [GIM International](https://www.gim-international.com/content/article/enhancing-reality-mapping-with-different-perspectives)) [U].
- The oblique views are what make church walls, towers and ship hulls look right: they photograph the vertical
  surfaces. Nothing in the pipeline knows a church is a church; the spire is just dense surface.
- We cannot use it: the Map Tiles API forbids pre-fetching, storing, caching and offline use, and
  "geodata extraction" from the content ([Map Tiles API policies](https://developers.google.com/maps/documentation/tile/policies)) [U].
  ARCHITECTURE.md D2 already limits the Photorealistic 3D Tiles to an optional runtime backdrop needing a key;
  the no-paid-services / no-new-keys rule rules even that out today.
- We do not have the input: the city publishes nadir orthos (5 cm, 2022 true ortho without lean) and lidar,
  not oblique imagery (`turku-data.md` §2.3). Street View / Look Around / Mapillary may only be **local
  reference** here (AGENTS.md rule 10), never a texture or geometry source that ships.

## 2. What our data supports

**Airborne lidar is 2.5D, and that is enough for a landmark's massing.** On the cathedral footprint
(`103371570S`, 3,085 m²) the 2021 lidar has [V]:

| Measure | Value |
| --- | --- |
| All returns inside the footprint (+0.5 m) | 265,450 (86 pts/m²) |
| Class 6 (building) | 143,242, heights 13.5–102.5 m N2000 |
| Class 12 (overlap) | 117,741 |
| Class 6 above 60 m (tower top and spire) | 14,675 |
| Class 6 within 0.5 m of the footprint edge (wall returns) | 23,177 |

The spire is sampled at metre density from above, the walls only sparsely from the side. So:

- **TIN / DSM meshing is the right tool, Poisson is not** (judgement from the numbers above, [U] for Poisson's
  behaviour): screened Poisson ([Open3D](https://www.open3d.org/docs/release/tutorial/geometry/surface_reconstruction.html),
  [MeshLab](https://www.meshlab.net/), [CGAL](https://doc.cgal.org/latest/Poisson_surface_reconstruction_3/))
  needs oriented normals all around the object; with almost no wall returns it balloons the walls and
  rounds the spire. A **footprint-bounded heightfield** (max class-6 height per 0.5 m cell, gaps filled, the
  footprint polygon as a constrained boundary) keeps every roof break, the tower and the spire, and closes with
  vertical walls on the surveyed footprint. That is what the prototype builds, then decimates to a budget.
- **What the roofer path loses** is exactly this: roofer and the CityGML LoD2 fit planar roof faces per
  footprint. The city's own LoD2/LoD3 model of the cathedral (`Building_1156587`, 402 / 427 surfaces up to
  102.5 m [V]) is a usable alternative shape, but the general buildings selector caps shells at 2,000
  triangles and 10 cm residual medians, which a spire is unlikely to meet [U]; its fallbacks are extrusions.
- **Roofs:** the 2022 true ortho (5.5 cm, no lean) is projected straight down onto up-facing triangles.
- **Walls:** neither the ortho nor the lidar sees them well. Use our generated facade path (D7/D8:
  references stay local, the shipped pixels are generated) per planar footprint wall, with a procedural
  material on non-planar or unbound wall parts.
- **Lidar RGB** (every return carries RGB from the survey's own nadir camera) is a free colour hint for
  steep surfaces the ortho smears, such as the spire. Prototype step 2, not used yet.

## 3. Choosing what gets this treatment

The allowlist is explicit (`pipeline/turku3d/landmarks/landmarks.toml`); OSM and the register only propose
candidates for the orchestrator. In the `work-3x3` OSM extract [V]:

| Tag | Hits (ways/nodes) | Examples |
| --- | ---: | --- |
| `building=cathedral` | 1 | Turun tuomiokirkko (also `heritage=yes`, `historic=church`) |
| `building=church` | 5 | Martinkirkko, Aleksandran kirkko, Hovigym |
| `man_made=tower` | 6 | cathedral tower, Martinkirkko tower, four nodes |
| `building=ship` | 7 | six moored boats on the Aura near the office, Majland |
| `amenity=place_of_worship` | 17 | many are nodes inside ordinary buildings |
| `tourism=attraction` with a building | 7 | Kauppahalli, observatory, concert hall, Samppalinna windmill |
| `historic=*` | 108 | 35 with a building tag; the rest memorials (props) |

`seamark:type` only marks harbour quays here, and OSM has no `ship=*` objects. The city's building
register attributes (completion year, `GIS:Rakennukset` 1300 for the cathedral) and its LoD2 surface counts are a
second signal: a footprint whose LoD2 model has hundreds of surfaces is a candidate.

## 4. Open-source tools that fit

| Need | Tool | Fit with our data |
| --- | --- | --- |
| Point filtering, rasterising, classes | [PDAL](https://pdal.io/) (in our env) | Direct: LAZ class filters, `writers.gdal` DSMs |
| Heightfield / TIN mesh | `triangle` (in our env), scipy | Direct; what the prototype uses |
| Simplification to a budget | [meshoptimizer](https://github.com/zeux/meshoptimizer) (in `tools/npm`), gltfpack | Direct; the assembler already uses it |
| Poisson / ball pivoting | Open3D, MeshLab, CGAL | Poor for airborne lidar (no walls); useful only with terrestrial or drone scans |
| LoD2 from airborne lidar | [roofer](https://github.com/3DBAG/roofer) (ours), [City3D](https://github.com/tudelft3d/City3D) (PolyFit-based, GPL-3.0, wants a MIP solver) | Planar roofs; neither models spires or hulls better than a DSM |
| Photogrammetry | [COLMAP](https://colmap.github.io/), [OpenMVS](https://github.com/cdcseacave/openMVS) | Only with **our own** photos (drone or handheld), never street imagery |
| LoD3 / digital-twin authoring | Blender + [CityJSON](https://www.cityjson.org/) tools, [3D City Database](https://www.3dcitydb.org/) | Manual; the city's own LoD3 has finer roofs but no facade detail |

## 5. Ships on the Aura

The moored restaurant boats are OSM `building=ship` polygons and their superstructures are in the lidar
(class 6/1 above the water, class 9). The hull below the waterline is not, and does not need to be. Two
options: (a) the same footprint heightfield as the cathedral, with a hull flare and the waterline at the measured
Aura level, generated paint/wood materials; (b) a CC0 low-poly ship scaled to the lidar footprint and height.
Suomen Joutsen lies at Forum Marinum, outside `work-3x3`; it needs its own input area.

## Recommendation

1. Keep roofer for houses. Give allowlisted landmarks the lidar heightfield mesh (visual ≤ 40k triangles,
   collision ≤ 2,000) with ortho roofs, replacing the selected building shell for the same footprint.
2. Order: cathedral (prototype), then the other `building=church|cathedral` and `man_made=tower` footprints,
   then the moored boats (`building=ship`), then Kauppahalli, the observatory and the windmill.
3. For wall pixels, run the generated facade path on each landmark's planar footprint walls; the procedural
   material stays as the fallback.
4. Revisit photogrammetry only if we fly or shoot our own images.
