// The three story locations (docs/design/story.md, "Locations"). Points are engine metres (core/origin.ts),
// placed on walkable ground checked against the published collision tiles.
import type { RouteTarget } from '../contract';

export const PIZZERIA: RouteTarget = { id: 'pizzeria', kind: 'pickup', label: 'Pizzeria Linnanportti, Linnankatu 17',
  point: { x: 77.1, z: 187.6 } };
export const MARKET: RouteTarget = { id: 'market', kind: 'market', label: 'Black market under Auransilta',
  point: { x: 297, z: 205.5 } };
export const DEALER: RouteTarget = { id: 'dealer', kind: 'shop', label: 'Rantakadun Kulkine, Kristiinankatu',
  point: { x: 101.5, z: 290.5 } };
/** Where a bought vehicle waits: the player's car is moved to this kerb on Läntinen Rantakatu. */
export const DEALER_VEHICLE_SPOT = { x: 119, z: 286.5, yaw: 0.473 };
