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
outside the walking bounds. Orange solid perimeter barriers make the temporary
pilot limit visible. They are development geometry, not a city survey claim.

`collision` maps cell IDs to `{url,sha256,bytes,tris,h:[minY,maxY]}`. URLs are
relative to the scene JSON. `spawn` contains `position`, `headingDeg`, and
`vehicle:{position,yaw}` (yaw in radians). Product cells alone do not imply that
the entire cell is walkable; the scene bounds and visible perimeter define the
pilot extent.

## First office source adapter

The first adapter imports frozen trusted local prepared lidar/vector evidence,
including complete building footprints and roof planes, into a 75 m square that
covers about 50 m of frontage and its cross-section. It triangulates disjoint
surface polygons with shared vector boundaries, retaining lidar ground samples,
and emits collision in the same compilation. Cached `.pkl` data is local trusted
input only. It must not be accepted from network requests.

This first producer proves scene loading and contact, not completed recognisable
frontage: facade programs/textures, fixture inventories and separated lower-quay
profiles are subsequent bounded changes. The existing evidence remains in its
source library. No city-wide generation is performed.

```
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.scene \
  --cache "$TURKU3D_DATA/coarse/.inputs" --output "$TURKU3D_WORK/scenes"
bash pipeline/env/run.sh python -m unittest discover -s pipeline/tests -p test_scene.py
```
