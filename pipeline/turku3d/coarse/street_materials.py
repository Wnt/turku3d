"""City finish classification, independent of procedural texture recipes.

These reserved ids select visual materials only. Collision retains the physical
surface classes from ground.materials.
"""

FIELDSTONE = 128
RED_ASPHALT = 129
BRICK = 130
# Sports venue finishes (turku3d.coarse.venues): athletics track rubber and mown pitch stripes.
TRACK = 131
PITCH_LIGHT = 132
PITCH_DARK = 133
FINISH_KEYS = {FIELDSTONE: "fieldstone", RED_ASPHALT: "asphalt_red", BRICK: "brick_paving",
               TRACK: "track_rubber", PITCH_LIGHT: "pitch_grass_light", PITCH_DARK: "pitch_grass_dark"}


def street_finish(surface):
    value = surface.casefold() if isinstance(surface, str) else ""
    if "kenttäkivi" in value:
        return FIELDSTONE
    if "asfalt" in value and "punai" in value:
        return RED_ASPHALT
    if "maatiili" in value:  # clay brick paving (Turku register; Dutch BGT "gebakken klinkers")
        return BRICK
    return None
