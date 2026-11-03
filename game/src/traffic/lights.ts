import type { FlowCar } from './flow';
import { hash01 } from './hash';

/** The worker and evidence renderer use the same shared-clock lamp state. */
export function vehicleLights(car: FlowCar, time: number): { indicator: -1 | 0 | 1; braking: boolean } {
  const turn = car.route.connector?.turn ?? 0;
  const preparing = car.route.length - car.s < Math.max(22, car.speed * 3);
  const intent = car.change?.direction ?? (car.signal ? Math.sign(car.signal.target.k - car.lane.k)
    : preparing && Math.abs(turn) > 0.5 ? Math.sign(turn) : 0);
  return { indicator: (Math.floor(time * 3) % 2 === 0 ? intent : 0) as -1 | 0 | 1,
    braking: car.previous.speed - car.speed > 0.04 || car.speed < 0.2 };
}

/** A driver held up by a player honks: a tap once the car has waited about a second, a longer blast if the player
 *  stays, then a short reminder every 6–10 s. Patience varies per driver; the pattern follows the shared hold time. */
export function honking(car: Pick<FlowCar, 'key' | 'held'>): boolean {
  const patience = 0.8 + 0.8 * hash01('honk-patience', car.key), h = car.held / patience;
  if (h < 1) return false;
  if (h < 1.3) return true;
  if (h < 3) return false;
  if (h < 3.9) return true;
  const period = 6 + 4 * hash01('honk-period', car.key);
  return (h - 3.9) % period > period - 0.5;
}
