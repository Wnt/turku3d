You label building facades for a procedural city generator. Do NOT generate or edit images and do NOT run shell commands; only look at the attached images and answer.

The attached images are perspective crops from street-level panoramas of ONE facade in Turku, Finland:
$view_list
In the *_outline images the target facade is outlined in red (approximate; the outline may be off by a metre or a few degrees). Other buildings in the frame are not the target. Known facts from the city register: address $address, $floors storeys, register facade material "$material", built $year, facade length $length_m m, estimated wall height $height_m m.

Fill the JSON schema for the outlined facade:
- views: one entry per attached crop (use the crop file name without "_outline"), usable=false when the facade is mostly hidden (scaffolding/wrap, trees, vehicles, a different building, far too oblique or blurry), with the main issue.
- bays = number of window columns across the WHOLE outlined facade (count carefully; if the views show different parts, combine them).
- colours as #rrggbb of the clean, unshadowed surface.
- All string values in English. Use only the enum values given by the schema.
Return only the JSON object.
