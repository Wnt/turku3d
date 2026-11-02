# Procedural landmark appearance

`ranking.py` produces a citywide candidate queue from available OSM extracts, with
individual score contributions and missing-evidence fields. Buildings and castle
footprints qualify; ships, squares, and point monuments need separate generators.
`enrichment.py` joins Museovirasto RKY areas and protected-register points and
fetches the latest complete month of Wikimedia page views. The register is not a
complete inventory of municipal-plan protections. `measure.py` joins cached LOD2
shells (including their stored lidar residual evidence), assigns stable building
IDs, measures relative skyline height, and tests street viewpoints against nearby
roof heights. Visibility currently approximates street ground using landmark base.

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.landmarks.ranking --out "$TURKU3D_DATA/research/landmarks/ranked.json"
bash pipeline/env/run.sh python -m turku3d.landmarks.enrichment --ranked "$TURKU3D_DATA/research/landmarks/ranked.json" --pageviews
bash pipeline/env/run.sh python -m turku3d.landmarks.measure --ranked "$TURKU3D_DATA/research/landmarks/ranked.json" --cache-root "$TURKU3D_WORK"
bash pipeline/env/run.sh python -m turku3d.landmarks.references --ranked "$TURKU3D_DATA/research/landmarks/ranked.json" --limit 20 --describe
```

References and download errors stay under `data/research/landmarks/`. Wikimedia
Commons image metadata carries attribution/licence and content hashes; nothing
from downloaded photos or local panoramas is packaged. The installed Codex vision
runner writes only dimensional/material parameters and observations. Missing
images produce a retryable error, never a fabricated description. No paid API or
new key is used. Descriptions are stored in `data/landmark-descriptions/`, keyed by
stable building ID or OSM identity. Reviewed parameter-only descriptions can also
be committed under `descriptions/`. Reviewed repository parameters take precedence
over automatic estimates; explicit work-directory parameters take precedence over
both for development. Automatic estimates require verified reference identity.

`appearance.wall_meshes` returns `None` for an ordinary building, allowing the
facades module to handle it. Landmark materials bypass generic residential-window
programs. `appearance.roof_mesh` follows the same contract. A tile hashes only its
own buildings' descriptions; texture output is deterministic from parameters.
Physical-scale brick bonds, irregular fieldstone, metal seams and slate courses
produce albedo, tangent normal and ORM (occlusion/roughness/metalness). Wall
openings, trim bands and clocks have independent positions in metres. Descriptions
record uncertainty; only source-supported parameters should be promoted.

Landmark visual closure walls are trimmed where all three outward probes find a
higher enclosing roof. Collision shells are unchanged. Existing roof geometry and
stable IDs remain the authoritative shared-world structure.

Evidence and current limitations are tracked in `~/turku3d/loops/landmarks/STATUS.md`.

Sources: [Museovirasto services](https://www.museovirasto.fi/fi/palvelut-ja-ohjeet/tietojarjestelmat/kulttuuriympariston-tietojarjestelmat/kulttuuriympaeristoen-paikkatietoaineistot)
(CC BY 4.0); [Wikimedia page-view API](https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html).

## City inventory and reference validation

`ranking` retains every available OSM building footprint. It compares log-area,
compactness and corner-count bins with the city's own population to measure shape
rarity. `measure` consolidates OSM ways/relations sharing one surveyed building ID;
`aliases` retains their identities. Ordinary buildings can enter the candidate or
landmark tier after heritage, height, prominence and visibility enrichment.

Reference extraction verifies building identity, excludes document scans, selects
nearby local reference views by camera bearing, and rejects overlapping global
window rows. Tower/clerestory observations stay separate from the main-body rows.
A failed physical estimate gets one automatic correction; every response remains
in research data. Changed measurements invalidate cached estimates. Commons 429
responses defer the remaining queue with Retry-After metadata. The pipeline
resumes after that interval automatically, with three retries by default; cached
valid descriptions are reused. `--reference-retries` controls this limit. `pipeline --descriptions PATH` can write the parameter
bundle to a local generator snapshot instead of the default data store.

## One-building development assets

```sh
python -m turku3d.landmarks.asset --building-id 103371570S \
  --cache-root "$TURKU3D_WORK/coarse" --out "$TURKU3D_WORK/landmark-assets"
```

The building geometry, resolved description and transitive generator sources form
an immutable recipe hash. Repeated requests reuse the asset; other buildings are
untouched. The manifest reports the GLB content hash, triangles and material batches.
Serve the output from a local development endpoint, then replace the resident
landmark without reloading the world:

```js
await window.__dev.landmark('103371570S', '/coarse/dev-landmarks/103371570S/HASH.glb');
await window.__dev.landmark('103371570S', null); // restore streamed appearance
```

This preview retains the resident collision and surveyed shadow shell. Raw PNG
maps are convenient for iteration; final visual/performance acceptance uses the
normal packaged tile with compressed textures and regenerated shadows.


## Resume a complete measured queue

```sh
python -m turku3d.landmarks.pipeline --ranked data/research/landmarks/ranked-city.json \
  --cache-root "$TURKU3D_WORK/coarse" --assets "$TURKU3D_WORK/landmark-assets"
```

This resumes reference extraction with cached valid results, then builds every
available described landmark. Missing references or survey geometry remain explicit
per-building deferred outcomes; they do not abort later buildings. The asset command
also accepts `--ranked` to generate assets independently of reference collection.
Descriptions that newly fail identity verification retain their observations but
stop supplying appearance parameters; reviewed repository recipes remain usable.
