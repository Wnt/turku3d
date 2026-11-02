import type { FlowCar } from './flow';

/** The worker and evidence renderer use the same shared-clock lamp state. */
export function vehicleLights(car: FlowCar, time: number): { indicator: -1 | 0 | 1; braking: boolean } {
  const turn = car.route.connector?.turn ?? 0;
  const preparing = car.route.length - car.s < Math.max(22, car.speed * 3);
  const intent = car.change?.direction ?? (car.signal ? Math.sign(car.signal.target.k - car.lane.k)
    : preparing && Math.abs(turn) > 0.5 ? Math.sign(turn) : 0);
  return { indicator: (Math.floor(time * 3) % 2 === 0 ? intent : 0) as -1 | 0 | 1,
    braking: car.previous.speed - car.speed > 0.04 || car.speed < 0.2 };
}
