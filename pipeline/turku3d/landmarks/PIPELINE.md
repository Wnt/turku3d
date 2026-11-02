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
be committed under `descriptions/`.

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
