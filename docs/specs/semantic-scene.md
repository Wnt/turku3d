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
whole-base height checks. Fixture family associations retain their evidence
confidence; mapped position alone does not establish a family.

```
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.scene \
  --cache "$TURKU3D_DATA/coarse/.inputs" --output "$TURKU3D_WORK/scenes"
bash pipeline/env/run.sh python -m unittest discover -s pipeline/tests -p test_scene.py
```

## Preserved visual context and contact audit

Prepared assets also accept `vegetation` and `context` recipes. The vegetation
adapter retains explicit source instance identities, crown data, rotation and
scale, resolves base elevation against final supports, and writes prepared
translations plus `instance_near_water` from source water polygons. Trunk
collision uses the existing generator's explicit allometric radius/height rule;
small shrub records do not gain tree-trunk colliders. Vegetation outside gameplay
coverage remains visual context. The pilot retains 165 vegetation instances,
including shrubs, with nine solid tree trunks inside its supported area.

Five frozen distant-scenery cells preserve surrounding silhouettes and vegetation.
Their prepared geometry and baked colors are clipped outside playable bounds in
the compiler. Context assets never add collision or promise gameplay coverage.
The river's visible extent follows the source water polygon beyond those bounds.

Office window crowns are added only when the imported office asset matches the
anchor source hash. The ten openings use the retained authored grid; dimensions
of the new crowns remain explicit reference-informed priors.

The bounded real-source audit verifies every dependent asset/image hash, checks
2,192 support samples against decoded collision, checks tree contact and collider
policy, and compares dry coverage to source polygons. This is geometric evidence,
not a substitute for the deployed walking and driving playtest.

```
bash pipeline/env/run.sh python -m turku3d.scene.audit \
  game/public/scenes/office.scene.json --cache "$TURKU3D_WORK/evidence"
```

## Measured upper retaining walls

Optional mesh `boundaryIds` names explicit edges used by the mesh. A vertical
retaining face has `supportId:null`, solid collision, and the same two boundary
IDs referenced by its upper and lower support polygons. Boundary identities
include elevation: the two levels at one plan edge remain distinct.

The small office survey reads WFS `kanta_Rakenne` code 66634 and raw last-return
lidar classes 2/12. Three measured runs cover the western wall, its adjoining
ramped wall and the next eastern ledge. The sustained western section has lower
levels around 1.23 m and upper levels around 3.61 m N2000. Its two-metre aprons
meet the unchanged terrain at shared rails. The final station of each supported
run closes to existing terrain; this terminal transition is inferred, rather than
a surveyed wall endpoint. No wall, barrier or height is added outside the source
run. Near the eastern ledge, two missing lower-walk knots use the independent
wall's measured lower level. Unsupported knots elsewhere remain unsupported.

The continuous riverside strip owns its paving where an access connector's wider
survey captured an upper landing. This prevents isolated upper-height triangles
from punching through the lower walkway. Actual ramps along the riverside strip
retain their measured longitudinal slope. Three of the four collision cells
change; the fourth remains byte-identical. The bounded audit additionally checks
all 115 retaining faces against both shared support edges.

The source survey can be reproduced with owned work/cache directories and
read-only shared raw data (use the host admission wrapper for lidar decoding):

```
python -m turku3d.scene.survey_retaining \
  --bounds 140 240 215 285 --output "$TURKU3D_WORK/evidence/retaining.pkl"
```

## Explicit lamps, signs and light blockers

`lightOccluders` is a required array, including when empty. Each record contains
`{id,positions:[[x,z],...],minY,maxY}` from a source building footprint and height
range. Lighting consumes these records without recovering buildings from render
triangles. `--asset-sources` preserves separate context-asset provenance when
recompiling a pilot with retained assets.

Emitter intensity is in candela in the renderer, range is a spherical cutoff in
metres, and color is linear RGB. Optional `category` is `street`, `path`, or
`wall`; every mapped office pole emits `street`. Fixture position is its supported
base, not its centre. The producer transforms each declared emitter offset once.
The first canopy and crook settings (45 cd / 18 m and 90 cd / 28 m) are spherical
visual priors for the existing exposure, not measured photometry or proof of a
particular lamp technology. Night comparisons must assess those priors.

`office_fixture_bindings.json` records exact registry associations and confidence.
The compiler installs only those associated poles. The actual contact disc is
checked against the final support; its bottom ring follows that plane while the
shaft, head and emitter remain rigid. Posts are solid and elevated fixture detail
is passable. See the calibrated [fixture evidence](../design/office-fixture-evidence.md).

The pilot's two registered signs retain E2 and C1. The old source adapter's bare
post fallback for C1 does not erase its code in the semantic producer. Their
heading agrees with the mapped carriageway's right-hand approach; it remains an
orientation inference, not a photographic match. C1's original procedural face
uses the published 640 mm normal diameter, red ring and yellow field from
[Traficom's C-sign drawings](https://www.finlex.fi/api/media/authority-regulation/535793/media/LIITE%2004%20Tieliikennelain%20merkkiryhm%C3%A4n%20C%20liikennemerkkien%20mitoitus.pdf).
No official drawing pixels or street-reference images are included.

## Prepared context vegetation

Context imports retain mapped tree sidecars as explicit instancing transforms,
stable IDs, species and near-water labels. Their CC0 source prototypes feed the
same vegetation LOD library as playable trees. The compiler excludes trees
inside the prepared gameplay bounds and identities already owned by the retained
vegetation asset; it does not assign distant trees unsupported collision. Five
frozen sidecars contribute 2,242 unique context trees to the office release.
Their content hashes are recorded in `sources`. All five corresponding skyline
cone layers are removed from the compiled context geometry.

Context uses its own scenery material recipe before graphics preparation. It
retains the distant facade shader and avoids adding distant geometry to the near
shadow batches. Playable-boundary guidance appears only near the player and
requires an actual dry support sample; physical boundary collision remains.
These changes repair regressions found in matched normal-gameplay captures of
`b5dc631c`; they require a fresh visual comparison before acceptance.

Placement groups preserve their original source parents. Multiple primitives of
one species share a parent, while another cell's same species has a different
parent; flattening those groups incorrectly removed 1,384 context instances in
the first import. The corrected GLB retains all 2,242 unique placements under the
actual consumer grouping rule. The office release also imports the complete
`103295899S` facade from its adjacent frozen source tile: entities crossing the
pilot boundary must not fall back to a plain collision proxy.
