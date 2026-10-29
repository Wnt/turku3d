# Facade programs and the Linnankatu pilot

2026-10-29. The draft generator consumes the shared
`textures/turkutex/facade_program.py` contract. The pilot was deployed in
`54161374`; the second delivery enables the same compiler's rule fallback for every
generated building and removes the old draft wall assembly path. No image model
or image generation runs at request time.

## Authority and painting

Directly reviewed programs own the observed fields on their matching wall, ahead
of older generated maps, descriptions and sampled rules. Other walls keep their
stored maps or description/rule treatment. A wall must match the building ID,
outward normal, line and a meaningful overlap; a roof fragment can extend beyond
the observed span. The painter clips observations to that overlap. Unknown measured
fields do not erase supported priors. Unreviewed programs do not override rules.
The rejected scaffold-covered pilot (1032959071) retains its existing treatment.

Register storeys remain authoritative, including conflicting geometry. OSM storeys
are not promoted to register measurements. Missing counts stay null and the rule
painter uses a blank material rather than inventing floor rows. Explicit measured
opening rectangles can still be painted. Ground-only observations affect only
their measured strip; full-wall observations do not create another row above the
measured wall. Occluded rectangles retain prior treatment. A photographic count
conflict is not a reason to change the building's height or register count.

The compiler uses real wall widths and the extrusion's eave. Collinear roof
fragments retain the full footprint-edge bay origin. Program attributes encode
bay pitch, ground/upper floor heights, opening size/sill, trim, corners, plinth,
cornice, courses and balconies. Reviewed openings and balcony rails use small
atlas patches. The existing material atlas supplies albedo/normal/ORM; no new
per-building texture image or encode is needed. Runtime attributes are interleaved
to fit WebGPU buffer limits, and per-wall variation survives tile batching.

Each building's reviewed JSON contributes to `facade_kit.parameter_hash`; editing
one program invalidates only tiles containing that building. Compiler source and
archetype definitions contribute to the building-visual producer identity. These
appearance-only modules are excluded from structural freshness. Cached geometry
and collisions are reused. Local builds export `facade-programs.json`
beside the tile as diagnostic evidence; the GLB carries compact painter attributes.

## Rule table and evidence

`textures/turkutex/facade_archetypes.py` is the executable ordered rule table.
Explicit use and cladding precede construction period. Office, retail, civic and
industrial OSM tags are supported. Numeric use codes follow
[CityGML 1.0 Annex C.1](https://www.sig3d.org/files/media/downloads/GDI-NRW.Archiv/09_08-007r1_City_Geography_Markup_Language_CityGML_Encoding_Standard.pdf);
unknown/local codes abstain. Low metal sheds use footprint area and storeys.
Long irregular frontages can use paired bays; district evidence refines ground
entrances. Neither year nor a district alone invents a shop POI.

Each family contains bounded distributions for material, pigment, trim, bay width,
window width/height/sill, ground treatment, plinth, cornice, courses, corners,
surrounds, rhythm and balcony probability. A separate seeded stream per field
uses the stable register ID, else OSM type/ID. Regeneration and input ordering do
not change a building's samples. Thirty neighbouring-ID fixtures produce thirty
different window dimensions and more than twenty pigments.

The OCI store has 320 descriptions, but only 132 report a visible wall: plaster
56, brick 12, wood 27, concrete 21, stone 4, glass 4 and metal 8. The other 188
register-derived rows are not independent photo measurements. Supported plaster
descriptions have window-width fractions around 0.36–0.65 and height fractions
around 0.49–0.68 (10th–90th percentiles); these constrain broad priors rather than
justify fitted period-specific distributions. Rare families remain explicit
architectural priors. References and downloaded descriptions stay outside git.

The compiler preserves the separately calibrated register colour table from
[wall-palette-calibration.md](wall-palette-calibration.md), which uses capture
round 3. Raw measured photographic RGB includes exposure and shadow, so it does
not replace that table unless a program explicitly declares calibrated albedo.
On reviewed walls the calibrated register palette remains unless the program explicitly
declares calibrated albedo. Other walls retain existing AI colours and stored maps.

All 21,774 unique IDs in 339 cached LOD2 and 20 cached GIS/Kanta files receive an
archetype. This covers the available register cache, not uncached city records;
the inventory excludes live OSM and footprint enrichment. There are 5,622 missing
construction years and 6,114 missing register storey counts.

| Family | Main rule | Cached IDs |
| --- | --- | ---: |
| Empire | Before 1870 | 87 |
| Neo-Renaissance | 1870–1899 | 154 |
| Jugend | 1900–1919 | 394 |
| Nordic classicism | 1920–1929 | 1,241 |
| Functionalism | 1930–1944 | 825 |
| Postwar plaster | 1945–1959 | 2,938 |
| Postwar brick | 1945–1959, brick; undated brick | 14 |
| Sixties slab | 1960–1969 | 1,299 |
| Concrete panel | 1970–1979; undated concrete | 1,210 |
| Postmodern brick | 1980–1999, brick | 20 |
| Postmodern plaster | 1980–1999 | 2,023 |
| Contemporary housing | 2000 onward; unknown fallback | 7,022 |
| Glass office | Glass; modern office | 0 |
| Metal office | Modern office, metal | 0 |
| Traditional timber | Wood before 1980/undated | 167 |
| Modern timber | Wood since 1980 | 19 |
| Industrial brick | Historic/brick industry | 39 |
| Warehouse | Warehouse/shed/garage, modern industry | 3,940 |
| Shopping | Retail/supermarket/mall | 0 |
| Civic | Civic uses; undated monumental | 382 |

Zero office/shopping counts reflect sparse use/cladding fields in this cache;
live OSM can supply those classifications. They are not estimates of city totals.

## Undated-building fallback (2026-10-29)

The fallback now draws a stable period from explicitly dated footprints within
80 m, weighted by `1 / (5 + distance_m)^2`. Distance is between footprints, so
party walls and adjacent frontages dominate distant buildings. Complete source
features in the existing 100 m input margin supply donors across tile ownership
edges. Repeated roof surfaces and overlapping OSM representations do not multiply
one building's vote. Inferred dates never become donor dates or overwrite the
construction-year/storey fields.

Explicit use, cladding and construction dates retain precedence. Register years,
materials and storeys also resist replacement by conflicting OSM values. OSM
`start_date`, building/use/amenity/office, heritage/historic, roof shape/material
and building material/cladding add evidence. Shop and amenity/office entrance
points within 1 m affect ground treatment; they do not turn an apartment block
into a shopping centre or invent a POI. Heritage supports an older period rather
than automatically labelling a residential building civic.

Without dated neighbours, central cadastral districts I–IX or a conservative
centre location envelope use an authored period mix from Empire through 1990s
housing. The weights are architectural priors, not a survey-derived model.
Storeys, footprint area/compactness, courtyard rings and mansard/gambrel roofs
weakly favour older centre forms. Outside supported contexts, the plain fallback
remains available. A separate seed stream uses register ID, then OSM ID, then the
building ID; donor order and unrelated recipe fields do not change its draw.
Existing AI descriptions, stored maps and reviewed Linnankatu authority are
unchanged by these rule/enrichment changes.

The same **21,774 cached IDs** now have this distribution. This inventory uses
cached register fields and LOD2 footprint proximity; live OSM can further refine
actual generated tiles. Of 5,622 undated IDs, 5,458 have nearby dated candidates,
one uses the centre prior without neighbours, and 163 have neither. Explicit
use/cladding still classifies some of those 163: **131 buildings retain the plain
contemporary fallback**. The contemporary family totals **3,089**, down from
7,022; that total also includes supported contemporary periods and must not be
read as a missing-data count.

| Family | Updated cached IDs |
| --- | ---: |
| Empire | 123 |
| Neo-Renaissance | 202 |
| Jugend | 500 |
| Nordic classicism | 1,756 |
| Functionalism | 1,242 |
| Postwar plaster | 3,695 |
| Postwar brick | 14 |
| Sixties slab | 1,649 |
| Concrete panel | 1,629 |
| Postmodern brick | 20 |
| Postmodern plaster | 3,311 |
| Contemporary housing | 3,089 |
| Glass office | 0 |
| Metal office | 0 |
| Traditional timber | 167 |
| Modern timber | 19 |
| Industrial brick | 39 |
| Warehouse | 3,937 |
| Shopping | 0 |
| Civic | 382 |

The inventory took 19.3 s on the local cached input. Direct production appearance
and WFS preparation was also exercised on `t0_0_0`, `t0_0_1` and `t0_1_0`, with
106/70/87 dated footprint donors respectively. This checks data enrichment, not
visual fidelity. Raw assignments and production diagnostics remain in the
sandbox under `reports/archetypes/`. No screenshots, driving checks or area builds
were requested. The appearance dataset hashes its own LOD2/register/OSM inputs;
unchanged ground and WFS preparation stays reusable, and served tiles regenerate
on demand through the existing stage caches.

## Local visual and timing evidence

Local captures are under `$TURKU3D_WORK/reports/texture-sanity/{before,after}/`:
`linnankatu-east-block.png`, `linnankatu-west-block.png` and
`linnankatu-shopfront.png`. They use the game's WebGPU renderer, materials,
environment and postprocessing, with five existing central tile outputs visible.
They are static street-camera captures, not a controller playthrough. Detailed
existing maps remain visible. Both blocks render without WebGPU errors. Sun glare
limits colour judgement in the west view; the captures are not colour scores.

The first three-tile cold encode observation was 9.436/3.007/1.046 seconds before
and 8.780/3.030/1.039 seconds with programs: 13.489 versus 12.849 seconds total.
The 0.023-second increase on one tile is below a useful single-run conclusion.
Subsequent warm-cache texture stages on the same central tile IDs were:

| Tile | Before (s) | Pilot (s) | All programs (s) |
| --- | ---: | ---: | ---: |
| t0_0_0 | 0.286 | 0.023 | 0.027 |
| t0_0_1 | 0.149 | 0.018 | 0.022 |
| t0_1_0 | 0.422 | 0.017 | 0.017 |

These are local M1 Max observations, not OCI throughput claims. The programs add
no wall-image encode. The baseline source snapshot and both sets of tile outputs
are retained locally. The baseline received the same required four-pixel KTX2
dimension alignment fix as main so that arbitrary roof crops render on WebGPU.
Concurrent upstream changes and host load mean total tile time is not an isolated
facade speed benchmark. No full area build is a delivery gate.

Seven repeated runs on identical frozen shell inputs measured local wall/roof
preparation medians of 0.199/0.211/0.375 seconds before and
0.450/0.232/0.462 seconds with programs. The explicit program construction adds
CPU work even though texture encoding stays fast. Surveyed-map matching now
prepares invariant geometry once per building and compares candidates in arrays.
These preparation timings are separate from the texture table above.

The deployed pilot's OCI warm texture stages were 0.079/0.064/0.061 seconds for
the same three tile IDs. Raw observations live in
`$TURKU3D_WORK/reports/archetypes/oci-pilot-timings.json`; the completed run result
records the corresponding worldwide deployment observation. Local pilot captures
are also retained separately under `reports/archetypes/pilot/`.

## Reproduction

```sh
source ../sandbox.env
source ~/turku3d/envs/pipeline.env
PYTHONPATH=pipeline:textures bash pipeline/env/run.sh python -m pytest -q \
  pipeline/tests/test_facade_programs.py pipeline/tests/test_facade_assets.py
node --test pipeline/turku3d/package/facade_kit.test.mjs
npm --prefix game test -- tests/unit/facade-program.test.ts
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- \
  .venv/bin/python tools/research/facade_characterization/archetype_inventory.py
PYTHONPATH=pipeline:textures scripts/dev/codex-task wait-cmd -- \
  python3 scripts/dev/host_slots.py browser -- node qa/render/texture-sanity.mjs after
```

The renderer command expects the local evidence tiles and dev server on the
sandbox's port. The inventory writes a JSON report under the sandbox work root.
The shared measurement evidence is documented separately in
[the Linnankatu pilot](../research/facade-linnankatu-pilot.md).


## Reviewed pilot application and opening heads (2026-10-29)

OCI's earlier `source: rule-sampled` was misleading: individual walls already
carried `measured_wall_id`, but stored-map binding could bypass those instructions.
Diagnostics now expose the actual `paint_source` of every wall, a stable generated
`wall_id`, the reviewed `measured_wall_id` separately, and the building `part_id`.
Buildings with a reviewed wall advertise `source: reviewed-overlay`.

Opening sections (`windows`, `base`, `doors`) accept a provenance-wrapped
`head_shape`: `flat`, `segmental-arch` or `round-arch`. `windows.head_shapes` is a
parallel list for individual `rectangles_m`; a door candidate can override its
section with a scalar `head_shape`. Missing fields retain a supported prior.
Archetypes sample heads from a separate stable stream for historic/civic families.
The painter warps the atlas opening/frame to a circular cap, including portals,
and samples wall PBR outside the curved head. Flat v4 instructions remain readable;
head codes use the existing detail attribute and add no GPU buffers or textures.
Openings clipped by roof fragments retain the full opening origin and dimensions.

Linnankatu 20 has fourteen upper rectangles (seven flat top heads, seven arched
middle heads), six arched ground windows and one arched central portal. These
three observed opening rows do not change the register storey count. Linnankatu's
other explicitly reviewed arch observations are retained in their own programs.

To report every pilot building/part/wall from rebuilt tile diagnostics, run:

```sh
python3 tools/research/facade_characterization/pilot_sources.py "$TURKU3D_WORK/coarse"
python3 tools/research/facade_characterization/pilot_sources.py "$TURKU3D_WORK/coarse" --markdown
```

The audit requires every accepted pilot to bind a reviewed wall, rejects missing
paint provenance, and lists every other wall's stored-map, description or rule
source. It explicitly allows the scaffold-rejected pilot to retain its priors.
Only requested tiles revalidate; changed appearance stages reuse structural and
collision caches. No area rebuild, screenshot or drive check is required.
