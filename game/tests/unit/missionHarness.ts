// The mission system test harness shared by missions.test.ts and truck-dealer.test.ts.
import type { MissionHudState, Router, WorldPoint } from '../../src/gameplay/contract';
import { MARKET, PIZZERIA } from '../../src/gameplay/content/locations';
import type { OptionId } from '../../src/gameplay/content/shops';
import { MissionSystem, type MissionFrame, type PaidDropoff } from '../../src/gameplay/missions/missions';

/** Synthetic router: an L-shaped path (x first, then z), so route length differs from the straight line. */
export const lRouter: Router = {
  route: (from, to) => [from, { x: to.x, z: from.z }, to],
  distance: path => path.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - path[i].x, p.z - path[i].z), 0),
};
export const lDistance = (a: WorldPoint, b: WorldPoint) => Math.abs(a.x - b.x) + Math.abs(a.z - b.z);

export const near = (p: WorldPoint, dx = 1): WorldPoint => ({ x: p.x + dx, z: p.z });
export const far: WorldPoint = { x: -500, z: -500 };

/** A mission system, continued from a `serialize()` text when `saved` is given. */
export function harness(saved: string | null = null, onPaid?: (drop: PaidDropoff) => void) {
  const missions = new MissionSystem({ router: lRouter, onPaid });
  if (saved !== null) missions.restore(saved);
  const states: MissionHudState[] = [];
  missions.subscribe(s => states.push(s));
  const frame = (position: WorldPoint, extra: Partial<MissionFrame> = {}): MissionFrame =>
    ({ position, interact: false, input: 'keyboard', vehicleId: null, ...extra });
  const at = (position: WorldPoint, dt = 0.1, extra: Partial<MissionFrame> = {}) => missions.update(dt, frame(position, extra));
  const press = (position: WorldPoint, extra: Partial<MissionFrame> = {}) => at(position, 0.1, { ...extra, interact: true });
  /** Lets the dialogue queue drain without moving. */
  const wait = (position: WorldPoint, seconds: number) => { for (let t = 0; t < seconds; t += 0.5) at(position, 0.5); };
  /** Selects a menu option in the shop at `point`, opening the shop first when it is closed. */
  const choose = (point: WorldPoint, id: OptionId, extra: Partial<MissionFrame> = {}) => {
    if (!missions.shop) press(near(point), extra);
    return at(near(point), 0.1, { ...extra, choose: id });
  };
  const leave = (point: WorldPoint) => at(near(point), 0.1, { leave: true });
  const option = (id: OptionId) => missions.shop!.options.find(o => o.id === id)!;
  /** Rents a permit (start money) and runs deliveries until `money` is reached. */
  const earn = (money: number) => {
    while (missions.money < money) {
      if (missions.permit === 0) { choose(MARKET.point, 'rent_permit'); leave(MARKET.point); }
      choose(PIZZERIA.point, 'take_order');
      press(near(missions.currentStop.point));
    }
  };
  return { missions, states, at, press, wait, choose, leave, option, earn, last: () => states[states.length - 1] };
}
