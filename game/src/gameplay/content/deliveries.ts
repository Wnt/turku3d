// Delivery missions in play order; after the last one the list repeats (docs/design/story.md, "Deliveries").
// Each drop-off point is the street-side spot in front of the address; distances are street routes from the
// pizzeria on the OSM graph.
import type { WorldPoint } from '../contract';

export interface Delivery {
  id: string;
  address: string;
  point: WorldPoint;
  /** Dialogue line ids: the step hint, the owner's hand-over line and the customer's reaction by tip band. */
  hint: string;
  handover: string;
  customer: { fast: string; ok: string; slow: string };
}

const delivery = (n: number, address: string, point: WorldPoint): Delivery => ({
  id: `delivery_${n}`, address, point, hint: `hint_deliver_${n}`, handover: `pizzeria_handover_${n}`,
  customer: { fast: `customer_${n}_fast`, ok: `customer_${n}_ok`, slow: `customer_${n}_slow` },
});

export const DELIVERIES: readonly Delivery[] = [
  delivery(1, 'Linnankatu 13a', { x: 191.0, z: 106.5 }), // ~140 m
  delivery(2, 'Aurakatu 1', { x: 294.5, z: 185.2 }), // ~290 m
  delivery(3, 'Läntinen Rantakatu 3', { x: 439.5, z: 54.8 }), // ~440 m
  delivery(4, 'Kaskenkatu 4a', { x: 474.3, z: 402.0 }), // ~610 m
];
