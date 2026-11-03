// Order selection and price/time quotations share the same route-distance rules.
import type { WorldPoint } from '../contract';
import type { PoolAddress } from '../content/addresses';
import { PIZZERIA } from '../content/locations';
import { basePay, parSeconds, type DeliveryJob, type VehicleOwnership } from '../economy';
import { drawLongOrder } from './orders';
import type { MissionSave } from './save';
import type { OrderStop } from './stops';

/** What the pizzeria quotes before an order is accepted. */
export interface OrderQuote {
  /** The drop-off address, or both joined with ` + ` for a double order. */
  address: string;
  metres: number;
  par: number;
  stops: number;
  pay?: number;
  /** Whole seconds left of a LIMITED TIME offer. */
  expires?: number;
  /** Euros off the pay when the order is carried on the pizzeria's loaner scooter (economy.ts LOANER_FEE). */
  fee?: number;
}

type StreetMetres = (from: WorldPoint, to: WorldPoint) => number;
/** Route metres from one drop-off to the next. */
export type StopLeg = (from: OrderStop, to: OrderStop) => number;

/** Pool ids of the next long job: one landmark, or two for a long double (missions/orders.ts drawLongOrder). */
export function longOrder(pool: readonly PoolAddress[], save: MissionSave,
  leg?: (from: PoolAddress, to: PoolAddress) => number): string[] | null {
  return drawLongOrder(pool, save.longDeliveries, save.seed, new Set(save.delivered), leg)?.map(a => a.id) ?? null;
}

/** Published route metres for pool addresses; scripted stops use the street router. */
export function metresFromPizzeria(stop: OrderStop, vehicle: VehicleOwnership, job: DeliveryJob, streetMetres: StreetMetres): number {
  if (stop.pool) return vehicle === 'none' && job === 'short' ? stop.pool.foot : stop.pool.car ?? stop.pool.foot;
  return streetMetres(PIZZERIA.point, stop.point);
}

/** Route metres of each paid leg: pizzeria to the first drop-off, then drop-off to drop-off (`leg`, by default the
 *  street router between the two points). */
export function legMetres(stops: OrderStop[], vehicle: VehicleOwnership, job: DeliveryJob, streetMetres: StreetMetres,
  leg: StopLeg = (a, b) => streetMetres(a.point, b.point)): number[] {
  return stops.map((stop, i) => i === 0 ? metresFromPizzeria(stop, vehicle, job, streetMetres) : leg(stops[i - 1], stop));
}

/** Par, metres and base pay of any order, short or long, single or double: par adds each leg's par, and each pizza
 *  pays its own leg's base pay (the double bonus and tips come on top). */
export function orderQuote(stops: OrderStop[], vehicle: VehicleOwnership, job: DeliveryJob, streetMetres: StreetMetres,
  leg?: StopLeg) {
  const legs = legMetres(stops, vehicle, job, streetMetres, leg);
  const pars = legs.map(m => parSeconds(m, vehicle, job));
  const sum = (v: number[]) => v.reduce((a, b) => a + b, 0);
  return { firstPar: stops.length > 1 ? pars[0] : 0,
    quote: { address: stops.map(o => o.address).join(' + '), metres: sum(legs), par: sum(pars), stops: stops.length,
      pay: sum(legs.map(m => basePay(m, job))) } };
}
