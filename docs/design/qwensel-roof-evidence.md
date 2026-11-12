# Qwensel roof detail, 2026-11-12

Only the roof of the second hero building is refined. The accepted six windows,
four shutter pairs, doorway, landing/stairs and every source roof plane remain
unchanged. `pipeline/turku3d/scene/qwensel_roof.json` is the qualified observation
record; no photographic or lidar pixels/point clouds are committed.

Two independently viewed building photographs, [Commons152575891](https://commons.wikimedia.org/?curid=152575891)
(photographed2024-07-10) and [Commons62757339](https://commons.wikimedia.org/?curid=62757339),
agree on two brick chimneys and profiled red clay tiles. These are appearance and
count observations, not calibrated colour or component measurements.

A bounded private scan of raw last returns from `23459500_6704500.laz` identifies
22/19-point compact peaks around world XZ[198.691,231.5075] and[192.768,234.738].
Their tops are13.976/13.972m, with an estimated0.08m top and0.15m plan uncertainty.
The two positions agree with the photograph arrangement along the retained main
ridge. Two other small elevated clusters over the rear wing remain unlabelled and
are not added. Cap widths have0.12m uncertainty; shaft insets, cap courses, openings
and flashing are construction priors. Flashing corners sample the actual retained
roof planes; chimney shafts extend0.06m into the roof. These appended details are
passable and do not alter building collision.

The approved asset's roof UVs use a different frame on adjacent triangles. The
adapter changes only that primitive's UV/material: contour-aligned U and uphill V
are shared per plane, with a0.92×1.32m repeat containing4×4 tiles. Individual tile
width0.23m, exposed row0.33m,14mm profile and8mm lap are explicit construction
priors. Original procedural albedo/normal/ORM maps retain approximately the old
roof's sRGB mean156.6/102.6/78.3; this is appearance preservation, not photometric
truth. Chimney brick dimensions and colours are likewise priors. Every map is
1024×1024, encoded as immutable KTX2 outside Git with block-aligned base extents.

The adapter checks the exact source SHA634429187e16cc204689ce3f21496bde5618d74d5b873fb7053202e0401a742c.
It preserves the original binary prefix and all original POSITION/NORMAL/indices,
other materials/UVs, image dependencies and frontage metadata. Its output contains
25 unchanged roof triangles and68 appended chimney/flashing triangles. A private
matched native gameplay preview must precede any visual acceptance; a subsequent
live pair is required before this counts as a published iteration.

Selected check:

```sh
source ../sandbox.env
node --test pipeline/tests/test_qwensel_roof.mjs
```

Private production:

```sh
PYTHONPATH=pipeline ~/turku3d/envs/pipeline/bin/python -m turku3d.scene.qwensel_roof_surface "$TURKU3D_WORK/qwensel-roof/maps"
node pipeline/turku3d/scene/qwensel_roof.mjs 634429187e16cc204689ce3f21496bde5618d74d5b873fb7053202e0401a742c.glb "$TURKU3D_WORK/qwensel-roof/maps/maps.json" "$TURKU3D_WORK/qwensel-roof/qwensel-roof.glb"
```

The `qwensel_roof_release` appearance stage updates an exact pinned scene and
its recipe without rebuilding unaffected terrain or lighting. It checks the
approved binary prefix and roof evidence hashes, preserves every native mesh,
support, fixture, emitter and collision file, and changes only the single asset,
its qualified bounds and source records. The original authored frontage pin and
registered-frontage role remain intact, so ordinary recipe compilation still
emits the accepted windows, shutters and supported stairs.

```sh
python -m turku3d.scene.qwensel_roof_release \
  --scene PINNED/office.scene.json --recipe PINNED/office.recipe.json \
  --asset CANONICAL/shared/616208546c072f3339ab907babb2cdc5c7ddb7e253c36b755d819075ea2f5ab2.glb \
  --output OWN_OUTPUT --output-recipe OWN_OUTPUT.recipe.json
```
