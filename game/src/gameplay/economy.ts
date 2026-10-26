// Money, prices and delivery pay (docs/design/story.md, "Economy"). Whole euros only.

export type VehicleOwnership = 'none' | 'bicycle' | 'car';

export const START_MONEY = 120;
export const LICENCE_PRICE = 80;
export const VEHICLE_PRICE: Record<Exclude<VehicleOwnership, 'none'>, number> = { bicycle: 60, car: 350 };
export const BASE_PAY = 6;
export const MAX_TIP = 8;

/** Par travel speed in m/s for the vehicle class the player owns. */
const PAR_SPEED: Record<VehicleOwnership, number> = { none: 1.4, bicycle: 5, car: 9 };
/** Seconds added to every par time for the hand-over at both ends. */
export const PAR_HANDLING_SECONDS = 20;
/** The tip falls linearly from MAX_TIP at this fraction of the par time ... */
const TIP_FULL_UNTIL = 0.7;
/** ... to zero at this fraction. */
const TIP_NONE_FROM = 1.3;
/** A delivery fails (the customer cancels) after this multiple of the par time. */
const FAIL_AFTER_PAR = 2;
/** Top speed of the player's own car while it stands in for the bicycle (m/s). */
export const BICYCLE_TOP_SPEED = 6;

export function parSeconds(routeMetres: number, vehicle: VehicleOwnership): number {
  return routeMetres / PAR_SPEED[vehicle] + PAR_HANDLING_SECONDS;
}

/** Whole-euro tip for a delivery that took `elapsed` seconds against `par` seconds. */
export function tip(elapsed: number, par: number): number {
  const full = TIP_FULL_UNTIL * par, none = TIP_NONE_FROM * par;
  const share = Math.min(1, Math.max(0, (none - elapsed) / (none - full)));
  return Math.round(MAX_TIP * share);
}

export function deliveryDeadline(par: number): number {
  return FAIL_AFTER_PAR * par;
}

/** What the dealer offers: the car once it is affordable or the bicycle is owned, else the bicycle; null after the car. */
export function nextVehicleOffer(owned: VehicleOwnership, money: number): Exclude<VehicleOwnership, 'none'> | null {
  if (owned === 'car') return null;
  return owned === 'bicycle' || money >= VEHICLE_PRICE.car ? 'car' : 'bicycle';
}
