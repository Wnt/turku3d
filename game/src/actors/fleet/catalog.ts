import { AUTHORED_MODELS } from './registry';
import type { DamageBox } from '../../vehicle/damage';

/** Real-model fleet (actors/fleet/models/*.json): converted CC BY sources and Blender-authored models, true size,
 *  metres, +Z forward. The procedural and Kenney fleets are gone; every role draws one of these. */
export type BodyType = 'hatch' | 'estate' | 'sedan' | 'suv' | 'mpv' | 'van' | 'pickup' | 'classic' | 'microcar';
export interface FleetModel {
  id: string; name: string; body: BodyType; family: string;
  length: number; width: number; height: number; wheelbase: number;
  hood: number; rear: number; taper: number; wheel: number; belt: number;
  face: number; accessory: 'none' | 'box' | 'rack' | 'taxi'; age: number;
  /** Selection weight within its family (body default when absent). */
  weight?: number;
}
export const FLEET: readonly FleetModel[] = AUTHORED_MODELS.map(model => model.fleet);
/** The box the body-damage cage spans (vehicle/damage.ts): the model's true size, centred, tyres on Y = 0. */
export const fleetDamageBox = (m: FleetModel): DamageBox => ({ center: [0, m.height / 2, 0], half: [m.width / 2, m.height / 2, m.length / 2] });
export const FLEET_BY_ID = new Map(FLEET.map(model => [model.id, model]));
/** Families that parking records and ambient traffic select from; special roles (bus, truck, ambulance, fire,
 *  microcar) need an exact id. */
export const SELECTABLE_FAMILIES: ReadonlySet<string> = new Set(['sedan', 'suv', 'hatchback-sports', 'van']);
/** The starter car ("Your car is parked nearby"): a common Finnish everyday hatchback. */
export const PLAYER_MODEL = 'golf7';
export const PLAYER_COLOR = 'red';
/** Light real models that are always resident: a car whose own model fails to load is drawn as one of these. */
export const FALLBACK_MODELS = ['carla-nissan-micra', 'passat-b8', 'carla-tesla-model3'] as const;

export function fleetHash(value: string): number {
  let h = 2166136261;
  for (let i = 0; i < value.length; i++) h = Math.imul(h ^ value.charCodeAt(i), 16777619);
  h = Math.imul(h ^ h >>> 16, 0x7feb352d);
  h = Math.imul(h ^ h >>> 15, 0x846ca68b);
  return (h ^ h >>> 16) >>> 0;
}

/** A parking record's box and its free space ahead and behind (metres). */
export interface FleetSlot { length: number; clear_front_m: number; clear_rear_m: number }
/** Models keep their true size, so in a parking slot they must fit: the overhang beyond the record box stays
 *  within the smaller free gap minus a 0.3 m margin. A record without a size accepts any model. */
export const fitsSlot = (m: FleetModel, slot?: FleetSlot) => !slot || !Number.isFinite(slot.length)
  || (m.length - slot.length) / 2 <= Math.max(0, Math.min(slot.clear_front_m, slot.clear_rear_m) - .3);
const weight = (m: FleetModel) => m.weight ?? (m.body === 'pickup' ? .08 : m.body === 'classic' ? .1 : 1);

/** The real models a record family selects from (every slot size); computed once per name, since every traffic
 *  car of every frame asks (traffic/client.ts). */
const familyChoices = new Map<string, readonly FleetModel[]>();
function choicesOf(model: string): readonly FleetModel[] {
  let choices = familyChoices.get(model);
  if (!choices) {
    const family = model === 'suv-luxury' ? 'suv' : model === 'sedan-sports' ? 'sedan' : model;
    choices = FLEET.filter(m => weight(m) > 0 && (model === 'sedan-sports' ? m.body === 'classic' || m.body === 'sedan' : m.family === family));
    familyChoices.set(model, choices);
  }
  return choices;
}

/** The always-resident light model drawn for `seed` when its own model is unknown or its asset failed. A catalog
 *  without the fallbacks (a dev server whose model glob is stale) still yields one of its own models. */
export function fallbackModel(seed: string): FleetModel {
  const hash = fleetHash(seed);
  return FLEET_BY_ID.get(FALLBACK_MODELS[hash % FALLBACK_MODELS.length]) ?? FLEET[hash % FLEET.length];
}

/** The model for a record or traffic car: an exact model id, or a weighted, stable pick within its body family.
 *  Ambient traffic picks within its family (traffic/constants.ts TRAFFIC_MODELS) here, in the worker and on the main
 *  thread alike, so every drawn traffic car carries a real model id.
 *  With a parking slot only fitting models are offered; when none fits the slot stays empty (null). */
export function fleetModel(model: string, seed: string): FleetModel;
export function fleetModel(model: string, seed: string, slot?: FleetSlot): FleetModel | null;
export function fleetModel(model: string, seed: string, slot?: FleetSlot): FleetModel | null {
  const exact = FLEET_BY_ID.get(model);
  if (exact) return fitsSlot(exact, slot) ? exact : null;
  const choices = slot ? choicesOf(model).filter(m => fitsSlot(m, slot)) : choicesOf(model);
  if (!choices.length) return slot ? null : fallbackModel(seed);
  let pick = fleetHash(seed) / 4294967296 * choices.reduce((s, m) => s + weight(m), 0);
  for (const m of choices) { pick -= weight(m); if (pick < 0) return m; }
  return choices[choices.length - 1];
}
