# Prepared surface response

`PreparedMaterial.surface` is an optional source assignment, restricted to
`asphalt`, `granite`, `cobblestone`, `stone` or `grass`. The consumer sets the
fixed `prepared_<surface>` material key for the existing MaterialStyler. It
does not classify a material from its ID, pigment, texture or triangles.

| Explicit recipe | Existing graph response |
| --- | --- |
| asphalt | Detail tile at 4 repeats/metre; mineral wet film and puddles |
| granite | Detail tile at 2.5 repeats/metre; mineral wet film and puddles |
| cobblestone | Detail tile at 2 repeats/metre; mineral wet film and puddles |
| stone | Mineral wet film and puddles; no extra dry grain |
| grass | Detail tile at 1.4 repeats/metre and existing broad tint variation; no puddles |

The prepared albedo, normal and ORM textures and their metric UVs remain
unchanged. Extra bump is used only where the source has no normal/bump map.
At medium, grain changes albedo by at most ±2.75%; roughness uses the existing
detail response. Wetness is the shared weather uniform: exposed mineral
surfaces darken and their roughness moves toward film/puddle values. The
existing exposure/depression field controls sheltered areas and puddles.

Without this field the native prepared material remains its authored plain
PBR material. This was why the initial prepared paving lacked the existing
weather response despite keeping its PBR maps.

Native material batches keep their prepared ambient visibility and street
irradiance attributes. They deliberately do not advertise the legacy ground
layer used by the surface-array merger. This change does not add reflection
eligibility, new textures, new weather algorithms or runtime material inference.

The current office recipe records the following assignments from its approved
library maps. They describe the rendering asset, not a new claim about the
surveyed real-world substrate; later source classification may replace a map.

| Prepared material | Explicit library source | Response |
| --- | --- | --- |
| road | draft_asphalt_worn | asphalt |
| footway, quay | draft_concrete_slabs | stone |
| quay-wall | draft_quay_stone | stone |
| ground, cobblestone | draft_cobblestone | cobblestone |

The source importer also maps explicitly selected `draft_concrete_pavers`,
`draft_brick_paving` and `draft_fieldstone` to generic `stone`, and
`draft_granite_curb` to `granite`. It leaves unlisted assets, including gravel,
unassigned: the current mineral response includes standing puddles and is not
a suitable claim of porous-surface behavior.

Bridge descriptors use only their producer's explicit `surface` metadata:
`asphalt` → `asphalt`, `concrete`/`stone` → `stone`, and `granite` → `granite`.
An implicit default concrete map does not establish a response assignment.
Steel, paint, wood and other bridge materials retain their existing PBR.
