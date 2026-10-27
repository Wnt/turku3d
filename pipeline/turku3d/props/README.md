# Props layer v1

Build from the immutable `work-3x3` WFS/OSM snapshot and published ground/structures collision:

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.props  # office core (development)
bash pipeline/env/run.sh python -m turku3d.props --area playable-tiles  # final AOI acceptance
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_props.py
bash pipeline/env/run.sh scripts/dev/gate
# npm ci --prefix game is needed once for the browser preview.
bash pipeline/env/run.sh python -m turku3d.props.preview
bash pipeline/env/run.sh python -m turku3d.props.orientation_report
```

The default is the office core, 250×250 m (16 collision tiles), without a margin.
`--area playable-tiles` selects the listed playable L0 tiles (`config.playable_tile_ids()`) plus 50 m; for the four core tiles that is engine x/z −50…550 m.
`--tiles t0_0_0 t0_0_1 --margin 50 --source-area work-3x3` selects another envelope.
A build replaces the **complete** props manifest, not an incremental subset.
Only `$TURKU3D_WORK/build/layers/props` is published; the assembler owns shipped files.
Run the assembler with the other completed layers to integrate this layer into the game.

Outputs follow `docs/specs/layers.md`: `c_i_j.col.npz`, `t0_i_j.vis.glb`, and
`MANIFEST.json`. `instances.json` records source identity, engine position, yaw, sign
code, nearest ground distance and orientation method. `metrics.json` reports counts,
geometry/file budgets, missing sources and omitted objects. The manifest embeds those
metrics, hashes every input/output and identifies producer code. Stale unlisted files
are intentionally ignored by layer readers. Do not build concurrently with assembly.

## Models, placement and collision

- Original procedural benches, bins, bollards, inverted-U bike racks, yellow bike
  station terminals, parking meters, lamps, stops, signs and generic posts.
- A glTF mesh is stored once per type/sign face per tile, referenced by separate object
  nodes with transforms and required extras. This is **standard glTF mesh instancing**;
  it does not request GPU draw batching. Budget raw draws as one per visible object.
- Each model uses one embedded 256×128 RGB palette/sign atlas. `props_<type>` material
  keys preserve these source colors/images when no library replacement exists.
- Finnish E1, E2, E6, C37, C38, B5, D3.1 and H20 have small procedural faces. These are
  stylised approximations, not regulatory artwork. Unsupported codes remain attached
  to generic posts and are counted separately. No invented speed limits or text.
- Placement uses [the shared validator](../placement/README.md): collision-surface heights,
  whole-base support and semantic/access clearance, deterministic repair within 2 m,
  and explicit missing-bridge-deck omissions. JSON/HTML reports retain every violation.
- Model +Z is the visual front, and yaw is a +Y rotation in engine x/z radians.
  Signs and bus stops face approaching right-hand traffic, inferred from their side
  of the nearest carriageway centreline. This is independent of the centreline's
  digitization direction; explicit lane direction is unavailable in the city input.
  Benches face away from the kerb toward the walkway, falling back to walkway
  interior where the road is not nearby. Lamp arms point toward the road. Bins,
  bollards, posts and racks align with the kerb tangent. Shared placement recomputes
  these headings after any position repair.
- Dimensions are representative, not surveyed. A bike station symbol represents a
  register location, not its reported capacity; rack locations use three hoops.
- Whole objects belong to their placement-centre collision tile; no clipping or
  duplication at tile edges (`seams: objects`). Cylinders cover posts, lamps and bins;
  benches use seat/base and backrest boxes. Racks use individual upright cylinders
  and top bars, retaining gaps. Material IDs are 25/27/28 from the shared table.
- Exact duplicate type/code/position records are removed. A `kanta_Pysakki` symbol
  within 2 m of an E6 sign is omitted to avoid a duplicate stop pole.

## Sources and licensing

Models and procedural atlas artwork in `models.py` are original project assets,
released under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
No downloaded model pack or API key is needed. The Python code follows repository
licensing; the CC0 dedication applies to generated geometry and artwork.

Placement: City of Turku cached WFS `GIS_Varusteet`, `GIS_Liikennemerkit`,
`GIS_Follariasemat`, `GIS_Polkupyoraparkki`, `GIS_Pysakointiautomaatit`,
`kanta_Pysakki`, plus `GIS_Keskilinjat` for orientation; OSM contributors for
`highway=street_lamp` and `barrier=bollard` (ODbL; https://www.openstreetmap.org/copyright).
Keep the project's original city data provenance/attribution with distributed outputs.
`kanta_Sahkoverkonosa` is optional and currently unavailable (HTTP 401 in the source
inventory); no credentials or guessed pole positions are used. Unclassified electrical
points, if supplied later, remain generic posts until their code list is verified.

Sign identifiers/colors are informed by the Finnish authority's
[traffic-sign guide](https://aineistot.vayla.fi/api/file/ava/Julkaisut/Vaylavirasto/vo_2023-29_liikennemerkkien_kaytto_web.pdf)
and the city's descriptive sign-code fields. Drawings are newly generated, not extracted.

## Browser evidence

`python -m turku3d.props.preview` reads the completed manifest and loads its GLBs in
Chrome through local Three.js. It uses sandbox port base +3 and the sandbox browser
profile, then closes its own browser/server. Outputs in `$TURKU3D_WORK/reports/props`:
`aoi.png`, `detail.png`, `catalog.png`, `street.png`, `sign.png`, `browser.json`.
The overview, office bench close view and street views retain actual coordinates;
the catalogue rearranges prototypes for inspection. The two street views include
published ground and buildings; other views show props alone. These are not evidence
of integrated game FPS or placement accuracy against photographs.
`orientation_report` draws +Z arrows on the cached 2025 true ortho for the four L0
tiles in `orientation-ortho.png`; `orientation.json` records counts and the color legend.
