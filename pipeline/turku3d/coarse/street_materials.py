"""City finish classification, independent of procedural texture recipes.

These reserved ids select visual materials only. Collision retains the physical
surface classes from ground.materials.
"""

FIELDSTONE = 128
RED_ASPHALT = 129
FINISH_KEYS = {FIELDSTONE: "fieldstone", RED_ASPHALT: "asphalt_red"}


def street_finish(surface):
    value = surface.casefold() if isinstance(surface, str) else ""
    if "kenttäkivi" in value:
        return FIELDSTONE
    if "asfalt" in value and "punai" in value:
        return RED_ASPHALT
    return None
