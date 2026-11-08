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
CONCRETE_PAVERS = 134
# Forest floors under closed canopy (turku3d.coarse.forest_floor): leaf litter, needle duff and moss. Collision keeps
# the physical ground class.
FOREST_BROADLEAF = 135
FOREST_CONIFER = 136
FOREST_MOSS = 137
# Unmown grass (turku3d.coarse.meadows): the green register's meadows and open areas, OSM meadow and grassland. The
# client grows meadow grass on it; collision keeps the grass class.
MEADOW = 138
FINISH_KEYS = {FIELDSTONE: "fieldstone", RED_ASPHALT: "asphalt_red", BRICK: "brick_paving",
               TRACK: "track_rubber", PITCH_LIGHT: "pitch_grass_light", PITCH_DARK: "pitch_grass_dark",
               CONCRETE_PAVERS: "concrete_pavers", FOREST_BROADLEAF: "forest_floor_broadleaf",
               FOREST_CONIFER: "forest_floor_conifer", FOREST_MOSS: "forest_floor_moss", MEADOW: "grass_meadow"}
# OSM surface=* values that name a unit paving finish (used where the register says nothing).
OSM_FINISHES = {"brick": BRICK, "bricks": BRICK, "paving_stones": CONCRETE_PAVERS}


def street_finish(surface):
    value = surface.casefold() if isinstance(surface, str) else ""
    if "kenttäkivi" in value:
        return FIELDSTONE
    if "asfalt" in value and "punai" in value:
        return RED_ASPHALT
    if "maatiili" in value:  # clay brick paving (Turku register; Dutch BGT "gebakken klinkers")
        return BRICK
    # Street panoramas of sampled register parts: Betonikivi (concrete paving stones) is mostly
    # red-brown, Betonilaatta sidewalks are small grey pavers in running bond, not 50 cm slabs.
    if "betonikivi" in value:
        return BRICK
    if "betonilaatta" in value:
        return CONCRETE_PAVERS
    return None
