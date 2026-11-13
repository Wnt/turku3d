"""Where open water meets built land: the lines along which a quay wall stands (the `wall` kind) and behind which
the ground holds its level up to the edge (the old generator's `coarse/banks.py kade_crest`)."""

from __future__ import annotations

import shapely

from ...street import osm, registers

BUILT_REACH = 8.0   # m: built land this near the water outline makes the edge a quay (a planted strip may lie between)
QUAY_REACH = 3.0    # m from the outline within which the lidar smears the drop; the crest is measured beyond it


def quay_lines(box):
    """(mapped open water, its outline where a polygon of the street register lies within BUILT_REACH), survey frame;
    (None, None) without water. Green land alone beside water is a natural bank."""
    mapped = osm.features(box)
    if mapped is None:
        return None, None
    areas = mapped[mapped.geom_type.isin(('Polygon', 'MultiPolygon')) & (mapped.natural == 'water')]
    if not len(areas):
        return None, None
    wet = shapely.unary_union([shapely.make_valid(g) for g in areas.geometry])
    streets = registers.layer('GIS:Katualueet', box)
    built = shapely.unary_union([shapely.make_valid(shapely.force_2d(g)) for g in streets.geometry if g.geom_type == 'Polygon']
                                or [shapely.Polygon()])
    return wet, wet.boundary.intersection(built.buffer(BUILT_REACH))
