# Turku street-light register investigation, 2026-11-09

Turku exposes useful lamp-pole positions without a key. The layer is already in
the farm's pinned source snapshot; it is not a new, richer lighting-network register.

## Public interface and licence

- Endpoint: `https://turku.asiointi.fi/TeklaOGCWeb/WFS.ashx`.
- Layer: `frn:CityFurniture_LOD2`, WFS 1.1.0, CityGML 2.0 / GML; anonymous
  GetCapabilities, DescribeFeatureType and bounded GetFeature requests returned 200.
- GetCapabilities also lists LOD1/LOD3 furniture. No separately named lighting
  register was found among the WFS layers or the 146 OGC API collections.
- `kanta:Sahkoverkonosa` is advertised but an anonymous GetFeature returned 401;
  it cannot be used as an open electricity/lighting-network source.
- Licence: CC BY 4.0, attribution **© Turun kaupunki**. The city's
  [interface and publication instructions](https://www.turku.fi/tietoa-turusta/kartat-ja-paikkatieto)
  explicitly cover its open geographic interfaces and CityGML model. The
  [dataset catalogue](https://avoindata.suomi.fi/data/fi/dataset/turun-kaupungin-paikkatietoja)
  also specifies CC BY 4.0.

The advertised LOD2 geographic extent is approximately 22.07448–22.46363 E,
60.35167–60.69068 N. This is an extent, not a completeness claim. All four
sample areas below contained poles; no city-wide completeness percentage was measured.

## Fields actually returned

Across 552 sampled features, the only populated generic attribute was the string
`Tasonimi`. Features also carried `gml:id`, a CityGML `creationDate`, and a
`lod2Geometry/gml:Point/gml:pos` with three numeric coordinates in EPSG:3877,
with absolute N2000 elevation. The Z coordinate is not a pole-height attribute.

| `Tasonimi` value | Meaning usable by the generator |
| --- | --- |
| `Kiilai:uv-pylväs mitattu` | Surveyed outdoor-lighting pole; highest existing source priority |
| `rr:js,pylväs,valaisin` | Pole/luminaire register entry |
| `rr:js,uv-pylväs` | Outdoor-lighting pole register entry |
| `Kiilai:uv-pylväs digitoitu` | Digitised outdoor-lighting pole; lower source priority |

No sampled feature supplied fixture technology, pole height, arm length or
orientation, suspended/wall mounting, span wire, power, colour or temperature.
The generic CityGML schema should not be mistaken for populated fixture metadata.
OSM occasionally supplies `lamp_mount`, `support`, `lamp_type`, directions or
light-shape/count tags that this WFS sample lacks.

## Comparison with OSM

These are fixed **500 × 500 m sample boxes**, not whole-district totals. Coordinates
are EPSG:3877 `(Emin,Nmin,Emax,Nmax)`. Current anonymous WFS downloads were compared
with the farm's pinned OSM source dated 2026-10-24. Pole rows were deduplicated at
1.5 m, with the generator's existing `Tasonimi` priorities; OSM matches use 3 m.
Several nearby register poles can match one OSM point.

| Area | Box | WFS rows | Deduplicated poles | OSM lamps | OSM lamps with a register pole ≤3 m |
| --- | --- | ---: | ---: | ---: | ---: |
| Centre | 23459500,6704500,23460000,6705000 | 96 | 86 | 24 | 22 |
| Kaskenmäki | 23460000,6703500,23460500,6704000 | 168 | 129 | 3 | 3 |
| Port Arthur | 23458500,6703500,23459000,6704000 | 150 | 110 | 6 | 6 |
| Varissuo | 23464500,6704000,23465000,6704500 | 138 | 118 | 21 | 3 |

Varissuo has weaker alignment: 10 register poles lie within 10 m of an OSM lamp,
versus 3 within 3 m. Keep unmatched OSM lamps; neither dataset proves the other
wrong. The WFS contains substantially more positions in every sample, but lacks
the fixture metadata and suspended lamps needed to replace all inference.

## Recommendation

1. Continue using the register as the primary pole-position source. This already
   happens in `props`: measured rows outrank digitised rows, register duplicates
   are removed within 1.5 m, and matching OSM lamp tags can enrich them. All four
   farm WFS Parquets exist and have the same row counts as the new downloads;
   no new farm source or raw-tree write is needed.
2. Feed these deduplicated register poles into **square-lights' darkness test**,
   which currently considers OSM lamps only. Estimate coverage with existing
   height/throw rules; retain square masts where a genuinely uncovered area remains.
   Do not replace a whole square's light set merely because one pole is mapped.
3. Keep **span-lamps** for survey wires over lit streets. This pole register does
   not map the suspended fixture positions, wires or mounting needed to replace
   that inference. Use positively identified hung fixtures to suppress inference
   if a richer open source becomes available.
4. Retain current street-class height/colour defaults and OSM overrides. Do not
   invent photometric attributes from the register's absolute Z or source labels.

These changes can feed the existing light/post/wire records. **No tile-format
change is proposed or made by this investigation.** The square-lights integration
above is a recommendation, not part of the lamp-seam migration.

## Reproduction and retained evidence

For each sample box, use the endpoint above with:

```text
service=WFS&version=1.1.0&request=GetFeature
typeName=frn:CityFurniture_LOD2&bbox=Emin,Nmin,Emax,Nmax
```

The local investigation and comparison commands are retained under
`~/turku3d/loops/lampseams/tools/{register,compare-register}.py`; raw responses,
schema, capabilities and comparison results remain outside Git in the loop's
evidence directory and `/bulk/turku3d/scratch/lampseams/` on osgallery-dev.
The shared `data/raw` tree was not written.
