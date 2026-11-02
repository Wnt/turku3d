// Shared-clock lane-change intentions and conservative front/rear gap checks. Geometry and collision-checked
// movement stay in TrafficFlow; the camera and local render cap never participate in a lane-change decision.
import { hash01, hash32 } from './hash';
import type { FlowCar } from './flow';
import type { Lane } from './tileNetwork';

export interface LaneChange {
  from: Lane; direction: -1 | 1; progress: number; duration: number; rate: number;
}
export interface LaneSignal { target: Lane; since: number; reason: 'route' | 'queue' }
const kind = (lane: Lane): -1 | 0 | 1 => {
  const turn = lane.connector?.turn ?? 0;
  return turn < -0.5 ? -1 : turn > 0.5 ? 1 : 0;
};
const cmp = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;

export class LaneChangePlanner {
  private siblings = new Map<Lane, Lane[]>();
  constructor(lanes: readonly Lane[], private movements: ReadonlyMap<Lane, readonly Lane[]>) {
    for (const lane of lanes) this.siblings.set(lane, lanes.filter(other => other.tile === lane.tile
      && other.carriageway === lane.carriageway && other.start.node === lane.start.node && other.end.node === lane.end.node)
      .sort((a, b) => a.k - b.k || cmp(a.id, b.id)));
  }

  intent(lane: Lane, key: string): -1 | 0 | 1 {
    const available = new Set((this.siblings.get(lane) ?? [lane]).flatMap(l => (this.movements.get(l) ?? []).map(kind)));
    const options = [0, 1, -1].filter(k => available.has(k as -1 | 0 | 1)) as (-1 | 0 | 1)[];
    const weights = options.map(k => (k === 0 ? 0.65 : k === 1 ? 0.22 : 0.13) * Math.max(0.01,
      ...((this.siblings.get(lane) ?? [lane]).flatMap(l => (this.movements.get(l) ?? [])
        .filter(r => kind(r) === k).map(r => Math.min(1, (r.next?.flow ?? lane.flow) / lane.flow))))));
    let value = hash01('turn-intent', key, lane.tile, lane.carriageway) * weights.reduce((a, b) => a + b, 0);
    for (let i = 0; i < options.length; i++) { value -= weights[i]; if (value <= 0) return options[i]; }
    return options.at(-1) ?? 0;
  }

  routes(lane: Lane, key: string): readonly Lane[] {
    const all = this.movements.get(lane) ?? [lane], intent = this.intent(lane, key);
    const intended = all.filter(l => kind(l) === intent);
    return intended.length ? intended : all;
  }

  /** Returns the adjacent target only after at least one second of signalling and safe projected gaps. */
  target(car: FlowCar, tick: number, byLane: ReadonlyMap<Lane, readonly FlowCar[]>): Lane | null {
    const lane = car.lane, remaining = lane.length - car.s;
    if (car.change || car.reservation || car.s < 15 || remaining < 28 || tick < car.changeCooldown) {
      if (!car.change) car.signal = null;
      return null;
    }
    const siblings = this.siblings.get(lane) ?? [];
    if (siblings.length < 2) return null;
    // Spread planning work across ticks and prevent weaving when a small gap fluctuates.
    if (!car.signal && (tick + hash32(car.key)) % 10 !== 0) return null;
    const intent = this.intent(lane, `${car.key}/${car.hops}`);
    let wanted = siblings.filter(l => (this.movements.get(l) ?? []).some(r => kind(r) === intent))
      .sort((a, b) => Math.abs(a.k - lane.k) - Math.abs(b.k - lane.k) || a.k - b.k)[0];
    let reason: LaneSignal['reason'] = 'route';
    if (!wanted || wanted === lane) {
      const leader = byLane.get(lane)?.filter(c => c !== car && c.s > car.s).at(-1);
      if (!leader || leader.speed > 1 || leader.stopped < 4 || leader.s - car.s > 30 || car.speed > 7) {
        car.signal = null; return null;
      }
      reason = 'queue';
      wanted = siblings.filter(l => Math.abs(l.k - lane.k) === 1).sort((a, b) => {
        const gap = (l: Lane) => byLane.get(l)?.filter(c => c.s > car.s / lane.length * l.length).at(-1)?.s ?? l.length;
        return gap(b) - gap(a) || a.k - b.k;
      })[0];
    }
    if (!wanted || wanted === lane) return null;
    const target = siblings.find(l => l.k === lane.k + Math.sign(wanted.k - lane.k));
    if (!target) return null;
    if (!car.signal || car.signal.target !== target || car.signal.reason !== reason) {
      car.signal = { target, since: tick, reason }; return null;
    }
    if (tick - car.signal.since < 10) return null;
    const s = car.s / lane.length * target.length, duration = 3.2;
    let front: FlowCar | undefined, rear: FlowCar | undefined;
    for (const other of byLane.get(target) ?? []) {
      if (other === car) continue;
      if (other.s >= s && (!front || other.s < front.s)) front = other;
      if (other.s < s && (!rear || other.s > rear.s)) rear = other;
    }
    if (front) {
      const gap = front.s - s - (front.length + car.length) / 2;
      if (gap < 3 + car.speed * 1.3 || gap + (front.speed - car.speed) * duration < 3 + car.speed) return null;
    }
    if (rear) {
      const gap = s - rear.s - (rear.length + car.length) / 2;
      if (gap < 3 + rear.speed * 1.6 || gap + (car.speed - rear.speed) * duration < 3 + rear.speed * 1.4) return null;
    }
    // For a discretionary queue pass require a useful gain, rather than moving into the same queue.
    if (reason === 'queue' && front && front.speed < car.speed + 1 && front.s - s < 35) return null;
    return target;
  }
}
