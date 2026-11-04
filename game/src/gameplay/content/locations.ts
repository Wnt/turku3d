// The three story locations (docs/design/story.md, "Locations"). Points are engine metres (core/origin.ts),
// placed on walkable ground checked against the published collision tiles.
import type { RouteTarget } from '../contract';

/** The pizzeria's mission point on the Linnankatu sidewalk, 1.6 m in front of its facade (the wall from (82.50, 181.96)
 *  to (67.37, 192.32) in the collision of bulk generation 2e6515f4). It sits 2.2 m east along that facade from the first
 *  hand-set point (77.1, 187.6), which stood 1.24 m from the register lamp post at (77.01, 188.83); now 3.1 m. The
 *  nearest other furniture is over 15 m away. Pools built from the old point stay accepted (addresses.ts LEGACY_ORIGIN). */
export const PIZZERIA: RouteTarget = { id: 'pizzeria', kind: 'pickup', label: 'Pizzeria Linnanportti, Linnankatu 17',
  point: { x: 78.92, z: 186.36 } };
export const MARKET: RouteTarget = { id: 'market', kind: 'market', label: 'Black market under Auransilta',
  point: { x: 291.32, y: 1.12, z: 210.84 } };
export const DEALER: RouteTarget = { id: 'dealer', kind: 'shop', label: 'Rantakadun Kulkine, Kristiinankatu',
  point: { x: 101.5, z: 290.5 } };
/** The black market's truck heist (docs/design/story.md, "Truck heist"). The trophy truck waits in the inner yard of
 *  Rettigin palatsi (Itäinen Rantakatu 4–6): open sky, road surface at y 10.37 in the collision, a straight 4–11 m wide
 *  drivable lane south to Hämeenkatu past the private driveways and their gate nodes, which carry no collision. The
 *  route ends where the street graph reaches the yard (the service way to its north-east gate). */
export const HEIST_YARD: RouteTarget = { id: 'heist_yard', kind: 'job', label: 'Rettigin palatsi inner yard',
  point: { x: 546.9, y: 10.37, z: 122.7 } };
/** The truck's spawn pose in the yard, nose towards the Hämeenkatu exit. */
export const HEIST_TRUCK_SPOT = { x: 546.9, y: 10.37, z: 122.7, yaw: -1.65 };
/** The drop-off on the road under Aninkaistensilta: ground y 9.67, the deck's underside at 14.6 and its road at 15.5.
 *  The street graph's nearest edge here is the bridge itself, so the route ends on Ratapihankatu south of it. */
export const HEIST_DROP: RouteTarget = { id: 'heist_drop', kind: 'job', label: 'Under Aninkaistensilta',
  point: { x: 243.4, y: 9.67, z: -873.5 }, approach: { x: 242.2, z: -907.9 } };

/** Where a bought vehicle waits: the player's car is moved to this kerb on Läntinen Rantakatu. */
export const DEALER_VEHICLE_SPOT = { x: 119, z: 286.5, yaw: 0.473 };
/** The pizzeria's kerb on Linnankatu, where "Bring my vehicle" parks the player's vehicle: the nearest `kerbSpots`
 *  pose (gameplay/kerbSpots.ts, half width 1 m: every vehicle fits) to PIZZERIA on the published road graph (navigation.json of bulk generation 2e6515f4), on the pizzeria's side of the two-lane street,
 *  facing the traffic of that side. */
export const PIZZERIA_VEHICLE_SPOT = { x: 81.04, z: 190.96, yaw: -2.556 };
