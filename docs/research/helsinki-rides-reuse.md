# Helsinki-rides facade reuse study

Date: 2026-10-26. Source: local shallow clone of `MattiTynka/Helsinki-rides` at `~/turku3d/ref/helsinki-rides`. This is a code and workflow study; no model, texture, photograph or map asset is copied. Its root `LICENSE` grants MIT rights to original code and documentation, but explicitly excludes those assets.

## What the project does

- `tools/world/author_delivery_observed_facade_finish.ts` takes a reviewed list of source wall faces, checks source city-model and world hashes, proves the selected wall plane complete, builds metric charts, and authors a plain or brick material finish. It records observations, limitations and appearance-only references. It deliberately does not infer openings from a plain finish.
- `tools/world/lib/facade-charts.ts` identifies triangles by float32 vertex bits, requires an orthonormal metric wall chart with at most 2 mm plane deviation, maps physical bounds to atlas pixels with a gutter, and proves a coarse target lies inside the selected detailed wall union. `apply_facade_charts.ts` checks source hashes and ownership, validates the resulting GLBs, preserves unrelated file hashes, and records draw calls and texture cost. Distant LODs receive sampled vertex colours.
- `repair_facades.ts` and the restoration modules are targeted, source-bound repairs rather than a universal facade generator. The native atlas scripts pack per-wall sources, preserve physical scale, generate mips, and verify that RDO changes only allowed texture payloads. The observed-finish atlas shares a plain tile but gives brick returns separate metric charts so the brick size stays constant.
- The build-helsinki-game skill separates visual, collision, pedestrian navigation and vehicle graph outputs. It calls for source ledgers, per-cell hashes, stable ownership, independently measured frame/draw/texture budgets and live Chrome captures. Its streaming guidance uses tile generation tokens, a collision-ready envelope, bounded per-frame uploads and actor-safe retirement. The texture-mapping-3d skill emphasizes calibrated references, UV preservation, before/after renders and explicit visual review.
- Ground junction logic is explicit: surveyed floor profiles qualify plinth and storefront placement on slopes; normal windows remain level. Bridge/junction code keeps collision, visual surfaces and attachment checks separate. That is more constrained than extrapolating a facade image down to the terrain.

## Compared with Turku3D

Turku3D already has a stronger geographic source for this pilot: authoritative `facades.geojson` baselines, metre UVs, individual KTX2 maps and a separate collision shell. Helsinki-rides is stronger at proving that an authored appearance applies only to reviewed source faces, documenting limitations, guarding unchanged cells by hashes, and accounting for atlas mips and GPU residency rather than compressed bytes alone. Its original procedural observed finishes also avoid preserving street clutter, but plain/brick finishes alone cannot reproduce the office block's real openings and ornaments.

## Concrete reuse

1. Reimplement the source-bound chart validation around Turku3D's existing facade ID and UV contract: record baseline geometry hash, physical size, outward normal, reference IDs and opening grid; reject maps whose grid, aspect or visible content contradict the wall. A direct port of the pure triangle-key and physical-atlas accounting helpers is possible under MIT, retaining the copyright notice if copied.
2. Add a per-facade provenance ledger with reference-only sources, generator prompt/version, quota/call count, generated image hash, KTX2 hashes and visual QA result. This makes a regenerated map auditable without shipping reference pixels.
3. For later scale-up, pack reviewed facade images by visual tile with gutters and metric texel density, count full mip residency and draw calls, and sample accepted LOD0 maps into distant vertex colours. Preserve the current per-facade interface until measured build/runtime cost justifies changing the assembler.
4. Apply measured ground profiles to plinth and ground-floor openings only after the structures/ground owners publish stable measured junction data. Keep the authored upper window rows level.
5. Borrow the review discipline: open actual WebGPU and WebGL2 captures from reachable street cameras, inspect texture seams and ground joins, and bind acceptance to exact build and source hashes.

## Do not take

- No Helsinki city mesh, atlases, texture pixels, photographs, maps, models or owner-supplied assets: the root MIT license does not cover them.
- No Helsinki EPSG:3879 coordinate constants, source triangle IDs or cell schema. Turku uses EPSG:3877, its own origin, 250 m visual tiles and facade sidecars.
- No blanket procedural window/door placement or image projection from a mismatched street view. The target is reference-faithful generated elevation art with explicit grid and street-level QA.
- No claimed performance numbers from the Helsinki project as Turku measurements. Its 350/600 draw and 256/512 MiB targets are provisional there and do not replace Turku's 8 MB tile and 300 MB initial texture budgets.
