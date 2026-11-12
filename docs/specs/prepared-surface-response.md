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
