# Connected Linnankatu / Market Hall source slice

This extends the existing A1/A2 scene to local bounds `[62.5,62.5,375,312.5]`.
It includes the complete Linnankatu-facing Market Hall entrance and a street loop
through Kristiinankatu, Linnankatu and Aurakatu. The complete approved Hall asset
remains visible beyond the playable rectangle. This is one bounded centre pilot,
not a city-wide generation run or a claim that every surrounding feature is known.

The frozen source manifests contribute 27 additional approved buildings, retaining
all earlier office/Qwensel assets and their authored programs and textures. Market
Hall `103304670P` retains its existing landmark description, arches, brick/trim
materials and clerestory. Reviewed Linnankatu programs retain their explicit
opening scope and register/storey qualifications.

The city street register distinguishes asphalt carriageways and footways,
concrete-paver footways, granite setts, gravel and fieldstone regions. These finish
boundaries are separate from the support's walking/driving classification. Exact
existing library maps and metric scales are reused. A concrete-paver finish does
not turn an asphalt walking strip into a generic slab plaza.

## Measured sections

The adapter reuses `coarse.street_survey` and `coarse.street_profiles`. Only shared
registered carriageway/footway edges qualify; a building-side outline is not
invented as a curb. The bounded fit uses 170,574 nearby raw ground returns and
produces 31 qualified chains, 399 stations and 731.97 m of sections. Heights range
from 0.0376 to 0.1943 m across the measured edges. Unsupported intervals remain
unfitted. The existing accepted A1/A2 rectangle is excluded from new profile fits.

At independent positions between the fitting stations, 189 of 190 cross-section
checks qualified. Median side-height difference is 0.00427 m, p90 0.01452 m;
median rise difference is 0.00696 m, p90 0.01926 m. These positions reuse the same
raw sensor cloud: this checks spatial interpolation, not independent survey accuracy.
The source confidence remains 0.03 m.

One shared planar network contains the source finish boundaries, section rails,
retaining ownership and bridge approaches. Ground, bevel, curb cap and collision
come from those same coordinates. The 0.0001 m planar grid avoids micro-triangles
that cannot survive the collision format's minimum area. Retaining rail matching
uses the same declared coordinate precision. The contact tolerance stays 0.002 m.

Freeze the source in an owned private evidence cache:

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.scene.ground_survey \
  --output "$TURKU3D_WORK/a3-evidence/ground-survey.npz" \
  --bounds 62.5 62.5 375 312.5
bash pipeline/env/run.sh python -m turku3d.scene.street_sections \
  --output "$TURKU3D_WORK/a3-evidence/street-survey.pkl" \
  --bounds 62.5 62.5 375 312.5 --exclude-bounds 125 187.5 375 312.5
```

The dense source has 52,569 representative samples from 2,389,351 accepted raw
returns. Raw data and frozen source products remain outside Git; the scene recipe
pins their hashes. Imported GLB/KTX objects use the canonical external asset store.

## Source and physics proof

The private support candidate `118fdfe0` passes 36,583 contact samples with maximum
error 0.000288 m. Its dry support gap is 0.008775 m² under the unchanged 0.01 m²
limit. There are 20 collision cells; the largest is 377,648 bytes. This candidate
intentionally precedes the final tree re-anchor and is not a publication artifact.

Four mapped curb crossings pass production tile-local Rapier capsule traversal
both ways: eight routes, no stalls, all grounded, at least 2.03 m tested headroom.
The normal 0.20 m autostep suffices. Steep normals on the curb faces are expected;
they are not mistaken for a broken horizontal path.

The established A2 deck and both lower-passage routes still pass. Both lower
routes also pass at offsets of ±0.4 m, preserving the central 0.8 m path band plus
the capsule radius. A changed ground triangle under the right tunnel lining is
inaccessible in both releases: the capsule stops at the identical lining contact.
It is recorded separately from reachable support differences.

The final tree preparation retains 1,154 pinned anchors, including 19 newly
playable context plants; nine of these are trees requiring trunk collision.
The re-anchor changes 128 heights. Only two lie inside the earlier playable
rectangle, by at most 0.000107 m. Larger new-area changes follow the dense ground
and measured curb source; the maximum is 0.164 m for a shrub. Source placement
parents stay distinct so same-species instances from different cells cannot be
mistaken for duplicate bark/crown parts.

This evidence proves source consistency and bounded gameplay routes. Matched
normal-game views, final tree/hedge contact checks and native M1 performance still
belong to the final integrated release; these source results alone are not a
claimed visible iteration.

## Final prepared release

Release `4bbf3a31687945b0ab5538a39ac2c08c1a10e203d19cd010cc721f275a9b9650`
incorporates the accepted measured hedge producer and metric timber bench finish.
It retains every preceding building asset unchanged, including the office and
corrected Qwensel frontage. Its 57 assets comprise 48 buildings, four vegetation
products and five context products. The 6,369 meshes include 5,411 explicit
supports; 231 fixtures include 143 supported solid tree trunks. The eight mapped
office lamps and their photometry remain intact.

The final canonical audit repeats all 36,583 support samples and the same
0.000288 m maximum contact error and 0.008775 m² dry support gap. All 302 external
image bindings are hash checked. Twenty collision cells contain 213,911 triangles;
the largest cell is 387,264 bytes. The scene is 123,302,008 bytes before transfer
compression. The strict version-two recipe pins 24 source files, every asset,
material recipe, tree anchor and the explicit supported office spawn. The initial
region catalog contains only this complete office/City Hall/Market Hall scene.
Native M1 performance and matched normal-game views remain deployment evidence.
