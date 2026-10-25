You are a texture artist for a photorealistic open-world game set in Turku, Finland. Use the built-in image generation tool (image_gen) exactly once. Do not write or copy files (read-only sandbox); just generate.

Attached are street-level reference photos of ONE real facade ($address, built $year), perspective crops aimed at it:
$view_list
In the *_outline images the target facade is outlined in red. Use ALL usable photos together: they show different parts and angles of the same wall. A structured description from a previous labelling pass:
$label_json

Task: create a clean, rectified, orthographic texture of THIS facade that keeps its real identity: the same bay rhythm and window positions, window shapes and panes, door positions, surrounds and ornament, plinth, cornice, materials and colours. $extent
$albedo_rules
$negatives
$style
Where the photos and the style note disagree, follow the photos.
Fill areas hidden in the photos plausibly in the same style. Output exactly one opaque, landscape image with an aspect ratio of about $aspect (width:height), the wall filling the whole frame edge to edge.
When done, reply in English with one line of JSON: {"notes": "<what you kept, changed or invented>"}
