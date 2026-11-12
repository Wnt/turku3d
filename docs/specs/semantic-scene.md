# Semantic scene contract

`turku-scene/1` is the single current next-generation runtime schema. The producer
validates it before publication; unsupported versions, extra fields, non-finite
coordinates, invalid triangles and dangling IDs fail closed. Required collections
are present even when empty. `pipeline/turku3d/scene/schema.py` defines the fields.

Coordinates are world-local metres: x = E − 23459500, y = N2000 elevation,
z = 6704500 − N. `origin` records [23459500, 6704500, 0]. Material colors are
linear RGB. Mesh positions, fixture bases and emitter positions are world-local;
fixture metadata never applies a second transform. Normals and metric UVs are
prepared by the compiler. Geometry owns collision policy: solid, passable,
dynamic or sensor. The compiler emits `.col` collision products from these same
triangles; runtime code does not recover semantics from triangles.

A support has one authoritative mesh and references canonical boundary IDs.
Adjacent regions reuse each boundary's exact coordinates. Fixtures reference the
final support, owned mesh IDs and emitter IDs. Emitters contain their position,
linear color, intensity and range. Source records pin a SHA-256 and record an
explicit uncertainty in metres. A scene release identifies one frozen compilation.

`coverage: {mode: "replace", bounds, entityIds}` replaces the complete bounded
scene. `bounds` are [minX,minZ,maxX,maxZ]. The pilot is a standalone world, not a
patch applied over old rendering/physics. Complete building entities may extend
outside the walking bounds. The terrain covers complete owned physics cells; there are no invented perimeter
barriers. Evidence routes remain inside the declared prepared coverage.

`collision` maps cell IDs to `{url,sha256,bytes,tris,h:[minY,maxY]}`. URLs are
relative to the scene JSON. `spawn` contains `position`, `headingDeg`, and
`vehicle:{position,yaw}` (yaw in radians). Product cells alone do not imply that
the entire cell is walkable; the scene bounds and visible perimeter define the
pilot extent.

## First office source adapter

The adapter imports frozen trusted local prepared lidar/vector evidence, including
complete building footprints and roof planes, into the four-cell envelope
[125,187.5,250,312.5]. The central comparison remains about 50 m of frontage.
It triangulates disjoint surface polygons with shared vector boundaries, retains
lidar ground samples, fits mapped lower-quay paving and emits the shoreline wall
and collision together. Overlay coordinates snap to a 10-micrometre grid to
remove numerical returning edges before constrained triangulation; this is far
below source uncertainty. Empty or unsupported observations do not become height
measurements. Cached `.pkl` data is local trusted input only.

`assets` is a required collection of `{id,entityId,url,sha256,position,recipe,bounds}`.
The first recipe is `building`; bounds contain world-local `min` and `max` vectors.
The source adapter freezes existing approved content-addressed GLBs and dependencies,
preserving facade-kit attributes, authored geometry, atlas bindings and roof maps.
A semantic collision proxy has `render:false` when its visual product is an asset.
This is a compiler decision; the runtime does not infer building identity.

Materials may reference `baseColorTexture`, `normalTexture`, and `ormTexture` as
`{url,sha256,wrap}` with wrap `repeat` or `clamp`. The compiler bakes all metric UV
transforms into the mesh; no runtime transform guessing is needed. Base color maps
are sRGB, the others linear. Optional `water:{point,normal,band}` declares the
reflection plane explicitly. Required fixture `sourceIds` and `uncertaintyM`
record mapped placement provenance; family dimensions remain inferred priors.

The current pilot retains 17 published building assets and the published asphalt,
slab and quay PBR maps. Furniture is emitted only with a resolved support and
whole-base height checks. The upper retaining wall, fixture family association,
vegetation and distant context are tracked separately from these completed inputs.

```
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.scene \
  --cache "$TURKU3D_DATA/coarse/.inputs" --output "$TURKU3D_WORK/scenes"
bash pipeline/env/run.sh python -m unittest discover -s pipeline/tests -p test_scene.py
```
