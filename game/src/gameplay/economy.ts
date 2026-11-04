// Money, prices and delivery pay (docs/design/story.md, "Economy"). Whole euros only.

export type VehicleOwnership = 'none' | 'scooter' | 'car' | 'truck';

export const START_MONEY = 120;
/** A rented courier permit covers this many delivery orders; renting adds to what is left. */
export const PERMIT_PRICE = 80;
export const PERMIT_DELIVERIES = 10;
/** The seller rents at most this many deliveries ahead. */
export const PERMIT_MAX = 20;
/** The bought-back expiry warning speaks after a hand-over when at most this many deliveries are left. */
export const PERMIT_WARN_AT = 2;
/** With no permit left and less than PERMIT_PRICE in cash, the black market rents the permit on credit for this much
 *  debt (missions/missions.ts `permit_on_credit`), so a courier is never left without work. */
export const CREDIT_PERMIT_DEBT = PERMIT_PRICE;
/** The repayment rule: each drop-off's base pay goes to the debt until it is paid; tips and the
 *  double bonus stay with the courier, so cash still visibly grows. Euros of `basePay` that repay `debt`. */
export const debtRepayment = (debt: number, basePay: number) => Math.max(0, Math.min(debt, basePay));
export const WARNING_PRICE = 10;
export const PLATES_PRICE = 120;
/** The truck heist pays this in cash on every run after the first, once the player already has the forged plates. */
export const HEIST_CASH = 120;
export const KIDNEY_PAYOUT = 400;
/** Run and sprint speed after the kidney sale, unless a slice is still giving energy. */
export const KIDNEY_RUN_FACTOR = 0.85;
export const SLICE_PRICE = 4;
export const SLICE_SECONDS = 120;
export const VEHICLE_PRICE: Record<Exclude<VehicleOwnership, 'none'>, number> = { scooter: 60, car: 350, truck: 600 };
/** The dealer buys a vehicle back at half its price, rounded down to whole euros. */
export const sellBackPrice = (vehicle: Exclude<VehicleOwnership, 'none'>) => Math.floor(VEHICLE_PRICE[vehicle] / 2);
export const BASE_PAY = 6;
export const MAX_TIP = 8;
/** Paid with the second pizza of a double order unless it is late enough to earn no tip. */
export const DOUBLE_BONUS = 4;

/** Par travel speed in m/s for the vehicle class the player owns. */
/** Route averages, below each top speed: the scooter peaks at 12.5 m/s (45 km/h), the car at about 25 m/s, the
 *  trophy truck at 33.3 m/s (120 km/h) and takes kerbs without slowing. */
const PAR_SPEED: Record<VehicleOwnership, number> = { none: 1.4, scooter: 7, car: 9, truck: 10.5 };
/** Seconds added to every par time for the hand-over at both ends. */
export const PAR_HANDLING_SECONDS = 20;
/** The tip falls linearly from MAX_TIP at this fraction of the par time ... */
const TIP_FULL_UNTIL = 0.7;
/** ... to zero at this fraction. */
const TIP_NONE_FROM = 1.3;
/** A delivery fails (the customer cancels) after this multiple of the par time. */
const FAIL_AFTER_PAR = 2;

/** A short order, a long-range job, or the pizzeria's LIMITED TIME job (a long-range run of LIMITED_MIN_METRES+). */
export type DeliveryJob = 'short' | 'long' | 'limited';
/** Finished short deliveries (delivered or failed) that unlock long-range jobs. */
export const LONG_UNLOCK_DELIVERIES = 4;
export const LONG_MAX_METRES = 7000;
/** Orders whose car route totals this much or more (both legs of a double) are offered only as the LIMITED TIME job;
 *  normal long jobs stay below it. */
export const LIMITED_MIN_METRES = 3000;
/** A LIMITED TIME offer stays in the pizzeria menu this many seconds of game time; declining is letting it run out. */
export const LIMITED_OFFER_SECONDS = 240;
/** The next LIMITED TIME offer appears this many game seconds after the last one was taken or ran out. */
export const LIMITED_COOLDOWN_SECONDS = 180;
/** A LIMITED TIME job pays this multiple of the long base pay; tips and the double bonus are unchanged. */
export const LIMITED_PAY_FACTOR = 2;
/** A long run pays 24 euros plus 12 per road kilometre, even when its tip is gone; a LIMITED TIME run pays double. */
export const basePay = (metres: number, job: DeliveryJob = 'short') =>
  job === 'short' ? BASE_PAY : (job === 'limited' ? LIMITED_PAY_FACTOR : 1) * (24 + Math.ceil(metres * 0.012));

export function parSeconds(routeMetres: number, vehicle: VehicleOwnership, job: DeliveryJob = 'short'): number {
  const boost = job !== 'short' ? (vehicle === 'scooter' ? 1.2 : 1.4) : 1;
  return routeMetres / (PAR_SPEED[vehicle] * boost) + PAR_HANDLING_SECONDS;
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
