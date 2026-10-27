Describe ONE building wall $facade_id in Turku, Finland, from a street photo, so that a program can draw a rough texture of it. Do not generate images or run commands. The attachment is a wall-plane rectified photo: it should show the target wall flattened to its measured rectangle, $width_m metres wide and $height_m metres high (aspect $aspect).

wall_visible is true only if the photo shows a building wall across most of the rectangle. It is false when it mainly shows pavement, sky, water, a boat, vehicles, a hoarding or construction fence, foliage, or when the wall is too dark, oblique or occluded to read; problem names that reason in a few English words and is empty when the wall is visible. When the wall is not visible, still give your best guess for every other field.

Fields, for the wall surface only (ignore people, cars, trees, signs and shadows):
- material: the main cladding, one of plaster (rendered or painted masonry), brick (exposed brick), stone, wood (boards or logs), concrete (panels or bare concrete), glass (curtain wall), metal (sheet cladding).
- wall_colour: the main wall colour as #rrggbb, as it would look in even daylight; trim_colour: window frames, cornice, bands and corners as #rrggbb (the wall colour again when there is no distinct trim).
- storeys: storeys including the ground floor; window_columns: window openings in a full upper row (the most common count; 0 for a single-storey wall or a blank wall).
- window_width: an upper window's width divided by the spacing between neighbouring window centres (about 0.3 to 0.7); window_height: an upper window's height divided by the storey height (about 0.4 to 0.7). Give 0 for both when there are no upper windows.
- ground_floor: shops (large display windows), doors (an entrance or gates among ordinary windows or plain wall), windows (like the upper storeys), plain (blank); ground_openings: the doors, shop windows and gates in the ground storey.
- roof_line: the top edge, cornice (a projecting moulded band), eaves (a roof overhang), parapet (a plain raised edge), none.
- plinth: whether a distinct darker base band is visible.
- features: any of balconies, pilasters, string_courses (horizontal bands between storeys), shutters, ornament (window surrounds, reliefs), arcade, only when clearly visible.

Respond ONLY with the strict schema JSON. facade_id must be exactly $facade_id.
