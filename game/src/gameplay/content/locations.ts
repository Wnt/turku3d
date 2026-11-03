// The three story locations (docs/design/story.md, "Locations"). Points are engine metres (core/origin.ts),
// placed on walkable ground checked against the published collision tiles.
import type { RouteTarget } from '../contract';

export const PIZZERIA: RouteTarget = { id: 'pizzeria', kind: 'pickup', label: 'Pizzeria Linnanportti, Linnankatu 17',
  point: { x: 77.1, z: 187.6 } };
export const MARKET: RouteTarget = { id: 'market', kind: 'market', label: 'Black market under Auransilta',
  point: { x: 291.32, y: 1.12, z: 210.84 } };
export const DEALER: RouteTarget = { id: 'dealer', kind: 'shop', label: 'Rantakadun Kulkine, Kristiinankatu',
  point: { x: 101.5, z: 290.5 } };
/** Where a bought vehicle waits: the player's car is moved to this kerb on Läntinen Rantakatu. */
export const DEALER_VEHICLE_SPOT = { x: 119, z: 286.5, yaw: 0.473 };
/** The pizzeria's kerb on Linnankatu, where "Bring my vehicle" parks the player's vehicle and the loaner scooter
 *  waits: the nearest `kerbSpots` pose (gameplay/kerbSpots.ts, half width 1 m: every vehicle fits) to PIZZERIA on the
 *  published road graph (navigation.json of bulk generation 2e6515f4), on the pizzeria's side of the two-lane street,
 *  facing the traffic of that side. */
export const PIZZERIA_VEHICLE_SPOT = { x: 81.04, z: 190.96, yaw: -2.556 };
