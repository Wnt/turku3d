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
}

type StreetMetres = (from: WorldPoint, to: WorldPoint) => number;

export function longOrder(pool: readonly PoolAddress[], save: MissionSave) {
  return drawLongOrder(pool, save.longDeliveries, save.seed, new Set(save.delivered));
}

export function longQuote(pool: readonly PoolAddress[], save: MissionSave, vehicle: VehicleOwnership): OrderQuote | null {
  const stop = longOrder(pool, save);
  return stop ? { address: stop.address, metres: stop.car!, par: parSeconds(stop.car!, vehicle, 'long'),
    stops: 1, pay: basePay(stop.car!, 'long') } : null;
}

/** Published route metres for pool addresses; scripted stops use the street router. */
export function metresFromPizzeria(stop: OrderStop, vehicle: VehicleOwnership, job: DeliveryJob, streetMetres: StreetMetres): number {
  if (stop.pool) return vehicle === 'none' && job !== 'long' ? stop.pool.foot : stop.pool.car ?? stop.pool.foot;
  return streetMetres(PIZZERIA.point, stop.point);
}

export function orderQuote(stops: OrderStop[], vehicle: VehicleOwnership, job: DeliveryJob, streetMetres: StreetMetres) {
  const first = metresFromPizzeria(stops[0], vehicle, job, streetMetres);
  const firstPar = parSeconds(first, vehicle, job);
  const leg = stops.length > 1 ? streetMetres(stops[0].point, stops[1].point) : 0;
  const par = stops.length > 1 ? firstPar + parSeconds(leg, vehicle, job) : firstPar;
  return { firstPar: stops.length > 1 ? firstPar : 0,
    quote: { address: stops.map(o => o.address).join(' + '), metres: first + leg, par, stops: stops.length, pay: basePay(first + leg, job) } };
}
