# W1-02: Structures layer: stairs, retaining walls, fences, ramps, quays (Phase 1 tasks 1.5–1.6)

Read `docs/briefs/COMMON.md` and `AGENTS.md`. Your layer is `structures` (COMMON §4).

## Goal

Every stair, wall, fence, ramp and quay edge around the office block exists in collision and in visuals. Stairs
follow the GTA V approach: **one smooth sloped collider** (`stair_slope`) plus visual treads, and a query-only
`stair_tread_helper` mesh that the character's foot IK raycasts against. The riverside quay along Läntinen
Rantakatu matters most: walls and railings there must stop the player and the car.

## Inputs

- Kantakartta `kanta:Rakenne`: porras 66626, step edges 66618, wall tops 66634, ramps 62540, fences.
- Curb lines with a step above 1 m, which the `ground` layer (W1-01) flags as quay. Read its layer output, or rerun
  its classification function. Do not duplicate the logic: import it.
- Lidar for heights, class 1 points for fence heights, and OSM `highway=steps` / `barrier=*` for gaps.

## Deliverables

1. **Stairs**:
   - Group the lines into flights. The rise comes from lidar top and bottom; riser count = round(rise / 0.16).
   - Visual treads, side walls/cheeks, a sloped collider and the tread helper mesh.
   - Flag flights with a slope above 40°.
2. **Retaining walls**: vertical quads down to the lower DTM plus a 0.3 m box, height from lidar.
3. **Fences and railings**: thin boxes or primitives (default 1.2 m, checked against class 1 points).
4. **Ramps**: preserved geometry and the `ramp` material.
5. **Quays**: wall plus railing collider along every quay line, with a visual quay stone edge.
6. Write the `structures` layer. Its ground contact edges must meet the `ground` layer without gaps. Coordinate
   through the layer data, not by editing W1-01's code paths unless a real bug is found there; if so, fix it properly.
7. Metrics JSON and Markdown report.

## Acceptance

- Each flight's top and bottom are within 5 cm of the lidar. Visual and collider edges coincide within 2 cm.
- A synthetic validator test shows the character climbs every flight.
- Wall height is within 10 cm of the lidar. A wall above 0.3 m or a fence cannot be stepped over. Ramps are climbable.
- The quay blocks the character and a car at 10 m/s, checked in the validator (W1-05 adds car checks; add a primitive
  test of your own if theirs has not landed yet).
- Fast unit tests on synthetic fixtures.
