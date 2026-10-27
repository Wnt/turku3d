// The scripted opening: four delivery missions in play order; without an address pool the list repeats
// (docs/design/story.md, "Deliveries"). The mission system takes each address's drop-off, route distance and par
// from the published pool (content/addresses.ts); `point` is the fallback for a pool that lacks the address: the
// sidewalk spot SIDEWALK_M (2.5 m) in front of the door, outside every footprint, as the pipeline places it.
// Distances in the comments are walking routes from the pizzeria on the road graph.
import type { WorldPoint } from '../contract';

export interface Delivery {
  id: string;
  address: string;
  /** Fallback drop-off when the published pool lacks the address. */
  point: WorldPoint;
}

const delivery = (n: number, address: string, point: WorldPoint): Delivery => ({
  id: `delivery_${n}`, address, point,
});

export const DELIVERIES: readonly Delivery[] = [
  // Not in the pool (its door search picks a party wall): the Linnankatu wall nearest the register point.
  delivery(1, 'Linnankatu 13a', { x: 183.24, z: 116.26 }), // ~129 m
  delivery(2, 'Aurakatu 1', { x: 267.03, z: 156.12 }), // ~245 m
  delivery(3, 'Läntinen Rantakatu 3', { x: 433.79, z: 69.71 }), // ~472 m
  delivery(4, 'Kaskenkatu 4a', { x: 446.4, z: 417.37 }), // ~551 m
];
