// Money, prices and delivery pay (docs/design/story.md, "Economy"). Whole euros only.

export type VehicleOwnership = 'none' | 'bicycle' | 'car';

export const START_MONEY = 120;
/** A rented courier permit covers this many delivery orders; renting adds to what is left. */
export const PERMIT_PRICE = 80;
export const PERMIT_DELIVERIES = 10;
/** The seller rents at most this many deliveries ahead. */
export const PERMIT_MAX = 20;
/** The bought-back expiry warning speaks after a hand-over when at most this many deliveries are left. */
export const PERMIT_WARN_AT = 2;
export const WARNING_PRICE = 10;
export const PLATES_PRICE = 120;
export const KIDNEY_PAYOUT = 400;
/** Run and sprint speed after the kidney sale, unless a slice is still giving energy. */
export const KIDNEY_RUN_FACTOR = 0.85;
export const SLICE_PRICE = 4;
export const SLICE_SECONDS = 120;
export const VEHICLE_PRICE: Record<Exclude<VehicleOwnership, 'none'>, number> = { bicycle: 60, car: 350 };
/** The dealer buys a vehicle back at half its price, rounded down to whole euros. */
export const sellBackPrice = (vehicle: Exclude<VehicleOwnership, 'none'>) => Math.floor(VEHICLE_PRICE[vehicle] / 2);
export const BASE_PAY = 6;
export const MAX_TIP = 8;
/** Paid with the second pizza of a double order unless it is late enough to earn no tip. */
export const DOUBLE_BONUS = 4;

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
