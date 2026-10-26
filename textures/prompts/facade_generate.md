Create one NEW opaque albedo texture for a Turku building wall with image_gen exactly once. The attached rectified street photos are appearance references only; do not copy their pixels. They all show the same target wall. The red outline in the perspective photo identifies it.

Target wall: $width_m metres wide, $height_m metres high, aspect $aspect. $bay_instruction
Reference views:
$view_list
$label_instruction

Draw a perfectly orthographic, evenly lit architectural elevation filling the wall rectangle edge to edge. Reconstruct the observed cladding, window and door positions, storey count, trim, cornice and plinth. Preserve unique openings rather than repeating a convenient bay. Generate every surface pixel anew. Remove people, vehicles, signs, poles, trees, awnings from other buildings, street reflections, cast shadows, neighbouring structures and any perspective. No ground or sky, border, text or watermark.
$layout_instruction
$albedo_rules
$style

Reply in English with one JSON line: {"notes":"observed features preserved and uncertain details generated"}
