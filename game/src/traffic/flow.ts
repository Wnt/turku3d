// Deterministic fixed-step traffic. Demand still comes from the street/area/hour slot model, but a slot
// creates a driver, not a moving timetable: drivers follow queues and reserve space before entering a junction.
// All resident lanes participate, independent of the camera ring, render cap or local frame rate.
import { MODEL_SIZE, MODEL_WEIGHTS, TRAFFIC, TRAFFIC_MODELS } from './constants';
import { hash01, mod, pick } from './hash';
import { blankPose, entryTime, pointAt, type CarPose, type LaneHeight, type TrafficNetwork } from './network';
import { connector, cumulative, type Lane } from './tileNetwork';
import { LaneChangePlanner, type LaneChange, type LaneSignal } from './laneChanges';

export const FLOW_STEP = 0.1;
const GAP = 2, BRAKE = 3.5, ACCEL = 1.6;
const cmp = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;

export interface FlowCar {
  key: string; lane: Lane; route: Lane; n: number; s: number; speed: number; hops: number;
  length: number; width: number; model: number; born: number;
  /** The connector remains reserved until the rear clears the destination lane. */
  reservation: Lane | null;
  stopped: number; waiting: number;
  blockedBy: string | null;
  change: LaneChange | null; signal: LaneSignal | null; changeCooldown: number;
  previous: { route: Lane; s: number; speed: number; change: LaneChange | null };
  pose: CarPose;
}

export interface TrafficCheckpoint {
  version: number; roads: string; tick: number; epoch: number;
  suppressed: string[];
  demand: [string, number][];
  cars: { key: string; lane: string; route: string; reservation: string | null; n: number; s: number; speed: number;
    born: number; stopped: number; waiting: number; changeCooldown: number; hops: number;
    change: (Omit<LaneChange, 'from'> & { from: string }) | null;
    signal: (Omit<LaneSignal, 'target'> & { target: string }) | null;
    previous: { route: string; s: number; speed: number; change: (Omit<LaneChange, 'from'> & { from: string }) | null } }[];
}

const saveChange = (change: LaneChange | null) => change ? { ...change, from: `${change.from.tile}:${change.from.id}` } : null;

/** Finnish signal sequence: green → amber → all red → red+amber → green. Two opposing approach groups.
 * Controller existence comes from roads.json; timings are a deterministic fallback, not measured Turku plans. */
export function signalState(lane: Lane, t: number): 'green' | 'amber' | 'red' | 'red-amber' {
  if (lane.signal === null) return 'green';
  const p = lane.points, n = p.length;
  const dx = p[n - 2] - p[n - 4], dz = p[n - 1] - p[n - 3];
  const group = Math.abs(dx) >= Math.abs(dz) ? 0 : 1;
  const phase = mod(t + hash01('signal-clock', lane.signal) * 60 - group * 30, 60);
  // The last six seconds serve left-turn arrows after through admissions stop and the box drains.
  const greenEnd = (lane.connector?.turn ?? 0) < -0.35 ? 24 : 18;
  return phase < greenEnd ? 'green' : phase < greenEnd + 3 ? 'amber' : phase < 59 ? 'red' : 'red-amber';
}

function protectedLeft(lane: Lane, t: number) {
  if (!lane.signal || !lane.connector || lane.connector.turn >= -0.35) return false;
  const h = heading(lane), group = Math.abs(h[0]) >= Math.abs(h[1]) ? 0 : 1;
  const phase = mod(t + hash01('signal-clock', lane.signal) * 60 - group * 30, 60);
  return phase >= 18 && phase < 24;
}

export class TrafficFlow {
  readonly cars = new Map<string, FlowCar>();
  tick = 0;
  private revision = -1;
  private ready = false;
  private epoch: number | null;
  private lanes: Lane[] = [];
  private sources: Lane[] = [];
  private byLane = new Map<Lane, FlowCar[]>();
  private conflicts = new Map<Lane, Set<Lane>>();
  private movements = new Map<Lane, Lane[]>();
  private baseOf = new Map<Lane, Lane>();
  private demand = new Map<Lane, number>();
  private occupancy = new Map<string, Set<FlowCar>>();
  private suppressed = new Set<string>();
  private changer: LaneChangePlanner | null = null;
  private departing = new Map<Lane, FlowCar[]>();
  stats = { entered: 0, exited: 0, blocked: 0, resets: 0, ticks: 0, laneChanges: 0 };

  constructor(readonly network: TrafficNetwork, epoch?: number) { this.epoch = epoch ?? null; }

  private roadIdentity() { return [...this.network.tiles.values()].filter(t => this.network.isEnabled(t.tile))
    .map(t => `${t.tile}:${t.hash}`).sort(cmp).join('|'); }

  /** Shared-world contract: a pinned road generation plus this tick-stamped state is sufficient for a late
   * joining peer. Camera residency and the wall time at which a client starts are not part of a checkpoint. */
  checkpoint(): TrafficCheckpoint {
    return { version: TRAFFIC.version, roads: this.roadIdentity(), tick: this.tick, epoch: this.epoch!,
      suppressed: [...this.suppressed].sort(cmp),
      demand: [...this.demand].map(([lane, n]) => [`${lane.tile}:${lane.id}`, n]),
      cars: [...this.cars.values()].map(c => ({ key: c.key, lane: `${c.lane.tile}:${c.lane.id}`, route: `${c.route.tile}:${c.route.id}`,
        reservation: c.reservation ? `${c.reservation.tile}:${c.reservation.id}` : null, n: c.n, s: c.s, speed: c.speed, born: c.born,
        stopped: c.stopped, waiting: c.waiting, hops: c.hops, change: saveChange(c.change), changeCooldown: c.changeCooldown,
        signal: c.signal ? { ...c.signal, target: `${c.signal.target.tile}:${c.signal.target.id}` } : null,
        previous: { ...c.previous, route: `${c.previous.route.tile}:${c.previous.route.id}`, change: saveChange(c.previous.change) } })) };
  }

  restore(state: TrafficCheckpoint) {
    if (state.version !== TRAFFIC.version || state.roads !== this.roadIdentity()) throw new Error('Traffic checkpoint road generation mismatch');
    this.epoch = state.epoch; this.reset(state.tick * FLOW_STEP);
    const lanes = new Map(this.lanes.map(l => [`${l.tile}:${l.id}`, l]));
    const routes = new Map([...this.movements.values()].flat().map(r => [`${r.tile}:${r.id}`, r]));
    const loadChange = (change: TrafficCheckpoint['cars'][number]['change']): LaneChange | null =>
      change ? { ...change, from: lanes.get(change.from)! } : null;
    this.suppressed = new Set(state.suppressed);
    this.demand = new Map(state.demand.map(([id, n]) => [lanes.get(id)!, n]));
    for (const saved of state.cars) {
      const lane = lanes.get(saved.lane), route = routes.get(saved.route), previous = routes.get(saved.previous.route);
      if (!lane || !route || !previous) throw new Error('Traffic checkpoint contains an unknown movement');
      const model = pick(hash01('model', saved.key), MODEL_WEIGHTS), [width, , length] = MODEL_SIZE[TRAFFIC_MODELS[model]];
      const car: FlowCar = { ...saved, lane, route, model, width, length, blockedBy: null, pose: blankPose(),
        change: loadChange(saved.change), signal: saved.signal ? { ...saved.signal, target: lanes.get(saved.signal.target)! } : null,
        reservation: saved.reservation ? routes.get(saved.reservation)! : null,
        previous: { ...saved.previous, route: previous, change: loadChange(saved.previous.change) } };
      sampleFlowPose(car, 0, () => 0, car.pose); this.cars.set(car.key, car);
    }
  }

  suppress(key: string) {
    this.suppressed.add(key);
    const car = this.cars.get(key);
    if (car) { this.unoccupy(car); this.cars.delete(key); }
  }

  /** Reconstruct from a shared hourly epoch on a graph change or clock jump. No random wall-time seed.
   * A generation-pinned multiplayer world can call reset(epoch) once and advance the same fixed ticks. */
  update(t: number) {
    const target = Math.floor((t + 1e-7) / FLOW_STEP);
    if (this.ready && this.revision !== this.network.revision && target >= this.tick) this.refreshGraph();
    if (!this.ready || target < this.tick) {
      this.epoch ??= Math.floor(t / 60) * 60 - 90;
      this.reset(this.epoch);
    }
    while (this.tick < target) this.step();
  }

  /** Streaming is a tick-stamped graph event. Keep existing drivers instead of replaying an ever-growing
   * session every time one neighbour loads. A shared-world host publishes the same event/checkpoint to peers. */
  private refreshGraph() {
    const existing = [...this.cars.values()], demand = new Map([...this.demand].map(([l, n]) => [`${l.tile}:${l.id}`, n]));
    const t = this.tick * FLOW_STEP;
    this.reset(t);
    const lanes = new Map(this.lanes.map(l => [`${l.tile}:${l.id}`, l]));
    const routes = new Map([...this.movements.values()].flat().map(r => [`${r.tile}:${r.id}`, r]));
    for (const car of existing) {
      const lane = lanes.get(`${car.lane.tile}:${car.lane.id}`);
      if (!lane || this.suppressed.has(car.key)) continue;
      const route = routes.get(`${car.route.tile}:${car.route.id}`);
      if (!route && car.s > lane.length) continue;
      car.lane = lane; car.route = route ?? this.chooseRoute(lane, car.key, car.hops);
      car.reservation = car.reservation ? routes.get(`${car.reservation.tile}:${car.reservation.id}`) ?? null : null;
      const previous = routes.get(`${car.previous.route.tile}:${car.previous.route.id}`);
      car.previous = { ...car.previous, route: previous ?? car.route };
      if (car.change) car.change.from = lanes.get(`${car.change.from.tile}:${car.change.from.id}`) ?? car.change.from;
      if (car.signal) car.signal.target = lanes.get(`${car.signal.target.tile}:${car.signal.target.id}`) ?? car.signal.target;
      sampleFlowPose(car, 0, () => 0, car.pose); this.cars.set(car.key, car);
    }
    for (const lane of this.sources) {
      const n = demand.get(`${lane.tile}:${lane.id}`);
      if (n !== undefined) this.demand.set(lane, n);
    }
  }

  reset(t: number) {
    this.epoch ??= t;
    const rebuild = this.revision !== this.network.revision;
    this.tick = Math.floor(t / FLOW_STEP); this.ready = true; this.revision = this.network.revision;
    this.cars.clear(); this.demand.clear();
    this.stats = { entered: 0, exited: 0, blocked: 0, resets: this.stats.resets + 1, ticks: 0, laneChanges: 0 };
    if (!rebuild) {
      for (const lane of this.sources) this.demand.set(lane, Math.ceil((t - lane.phase) / TRAFFIC.headway));
      return;
    }
    this.conflicts.clear(); this.movements.clear(); this.baseOf.clear();
    this.lanes = [...this.network.tiles.values()].filter(tile => this.network.isEnabled(tile.tile))
      .sort((a, b) => cmp(a.tile, b.tile)).flatMap(tile => [...tile.lanes].sort((a, b) => cmp(a.id, b.id)));
    this.sources = this.lanes.filter(l => l.length >= 12 && (!this.network.predecessor(l) || (l.cycle > 0 && l === l.origin)));
    const movements: Lane[] = [];
    for (const lane of this.lanes) {
      const seamNext = lane.end.kind === 'seam' ? this.network.successor(lane) : null;
      if (lane.end.kind === 'seam' && (!seamNext || seamNext.length >= 12)) { this.movements.set(lane, [lane]); continue; }
      const outgoing = seamNext ? [seamNext]
        : this.lanes.filter(b => b.tile === lane.tile && b.start.kind === 'junction' && b.start.node === lane.end.node);
      const routes: Lane[] = [];
      const fallback: Lane[] = [];
      for (const first of outgoing) {
        let next = first, curve = connector(lane, next);
        let signal = lane.signal, priority = lane.priority;
        const visited = new Set<Lane>([lane]);
        // Crossings and signal heads split OSM roads into 2–5 m links. Those links cannot store a car.
        // Treat them as part of one junction movement and reserve through to a usable receiving lane.
        for (let guard = 0; next.length < 12 && guard < 12 && !visited.has(next); guard++) {
          visited.add(next);
          signal ??= next.signal;
          if (next.priority !== 'normal') priority = next.priority;
          const candidates = next.end.kind === 'seam' ? [this.network.successor(next)].filter((l): l is Lane => !!l)
            : this.lanes.filter(b => b.tile === next.tile && b.start.node === next.end.node && b.start.kind === 'junction');
          const onward = candidates.filter(b => !visited.has(b)).sort((a, b) => {
            const ca = Math.abs(connector(next, a).turn), cb = Math.abs(connector(next, b).turn);
            return ca - cb || Math.abs(a.k - next.k) - Math.abs(b.k - next.k) || cmp(a.id, b.id);
          })[0];
          if (!onward || visited.has(onward)) break;
          const bend = connector(next, onward);
          const points = Float64Array.from([...curve.points, ...next.points.slice(2), ...bend.points.slice(2)]);
          const cum = cumulative(points);
          curve = { points, cum, length: cum.at(-1)!, speed: Math.min(curve.speed, bend.speed), turn: curve.turn + bend.turn };
          next = onward;
        }
        // One physical junction gets one tangent arc; chaining stop-line splinters produced tight S bends.
        if (visited.size > 1 && curve.length < 45) curve = connector(lane, next);
        const turn = Math.atan2(Math.sin(curve.turn), Math.cos(curve.turn));
        curve.turn = turn;
        if (next.length < 12 && !next.end.seam) continue;
        if (Math.abs(turn) > 2.6 && outgoing.some(b => b.carriageway !== first.carriageway)) continue;
        // Conflict bounds cover the junction movement, not the entire incoming street.
        const bbox: [number, number, number, number] = [Infinity, Infinity, -Infinity, -Infinity];
        for (let i = 0; i < curve.points.length; i += 2) {
          bbox[0] = Math.min(bbox[0], curve.points[i]); bbox[1] = Math.min(bbox[1], curve.points[i + 1]);
          bbox[2] = Math.max(bbox[2], curve.points[i]); bbox[3] = Math.max(bbox[3], curve.points[i + 1]);
        }
        const route = { ...lane, id: `${lane.id}>${next.id}`, next, connector: curve, bbox, signal, priority };
        fallback.push(route);
        if (turn > 0.5 && lane.k !== lane.laneCount - 1) continue;
        if (turn < -0.5 && lane.k !== 0) continue;
        if (Math.abs(turn) < 0.5 && next.k !== Math.min(lane.k, next.laneCount - 1)) continue;
        if (turn > 0.5 && next.k !== next.laneCount - 1 || turn < -0.5 && next.k !== 0) continue;
        routes.push(route);
      }
      // Missing lane/turn tags must not reintroduce the conveyor's tiny-link U-turn as a fallback.
      if (!routes.length && fallback.length) routes.push(fallback.sort((a, b) =>
        Math.abs(a.connector!.turn) - Math.abs(b.connector!.turn) || cmp(a.id, b.id))[0]);
      if (!routes.length && lane.end.kind === 'junction') routes.push({ ...lane, next: null, connector: null });
      for (const route of routes) { movements.push(route); this.baseOf.set(route, lane); }
      this.movements.set(lane, routes.length ? routes : [lane]);
    }
    for (const a of movements) {
      if (!a.connector) continue;
      const set = new Set<Lane>();
      for (const b of movements) {
        if (a === b || !b.connector || a.bbox[0] > b.bbox[2] + 8 || a.bbox[2] < b.bbox[0] - 8
          || a.bbox[1] > b.bbox[3] + 8 || a.bbox[3] < b.bbox[1] - 8) continue;
        if (a.next === b.next || pathsConflict(a, b)) set.add(b);
      }
      this.conflicts.set(a, set);
    }
    this.changer = new LaneChangePlanner(this.lanes, this.movements);
    for (const lane of this.sources) this.demand.set(lane, Math.ceil((t - lane.phase) / TRAFFIC.headway));
  }

  private regroup() {
    this.byLane.clear(); this.departing.clear();
    for (const car of this.cars.values()) {
      const list = this.byLane.get(car.lane);
      if (list) list.push(car); else this.byLane.set(car.lane, [car]);
      if (car.change) {
        const from = this.departing.get(car.change.from);
        if (from) from.push(car); else this.departing.set(car.change.from, [car]);
      }
    }
    for (const list of this.byLane.values()) list.sort((a, b) => b.s - a.s || cmp(a.key, b.key));
  }

  private step() {
    const t = ++this.tick * FLOW_STEP;
    this.stats.ticks++;
    this.regroup();
    this.occupancy.clear();
    for (const car of this.cars.values()) {
      car.previous = { route: car.route, s: car.s, speed: car.speed, change: car.change ? { ...car.change } : null };
      sampleFlowPose(car, 0, () => 0, car.pose); this.occupy(car);
    }
    // Demand is tested on its original shared slot hour. A blocked source queues outside the resident area;
    // the next free slot admits a car, rather than placing another body inside an existing queue.
    for (const lane of this.sources) {
      let n = this.demand.get(lane)!;
      while (entryTime(lane, n) <= t + 1e-7) {
        if (this.network.present(lane, n)) this.spawn(lane, n, t);
        n++;
      }
      this.demand.set(lane, n);
    }
    const heads = new Map<Lane, FlowCar>();
    for (const [lane, list] of this.byLane) {
      const front = list.find(c => c.s <= lane.length);
      if (front) heads.set(lane, front);
    }
    const ordered = [...this.cars.values()].sort((a, b) =>
      (b.reservation ? 1 : 0) - (a.reservation ? 1 : 0) || b.waiting - a.waiting || cmp(a.key, b.key));
    const reservations = ordered.filter(c => c.reservation !== null);
    for (const car of ordered) {
      car.blockedBy = null;
      const target = this.changer?.target(car, this.tick, this.byLane);
      if (target) this.beginChange(car, target);
      const lane = car.route, next = lane === car.lane && lane.end.kind === 'seam' ? this.network.successor(car.lane) : lane.next;
      let desired = lane.speed, stop = Infinity;
      const crossing = lane.connector && lane.next === next;
      const remaining = lane.length - car.s;
      if (crossing) {
        const turnSpeed = lane.connector!.speed;
        desired = Math.min(desired, Math.sqrt(turnSpeed * turnSpeed + 2 * BRAKE * Math.max(0, remaining - car.length / 2)));
        if (!car.change && !car.reservation && remaining < Math.max(30, car.speed * car.speed / (2 * BRAKE) + 8)) {
          if (this.mayEnter(car, heads, reservations, t)) {
            // Evaluate the gap while braking, but only lock the box when the nose is about to enter it.
            // Reserving thirty metres upstream blocked nearby junctions for the whole approach.
            if (remaining <= car.length / 2 + 1.2 + car.speed * FLOW_STEP) {
              car.reservation = lane; reservations.push(car);
            }
          } else stop = lane.length - car.length / 2 - 0.8;
        }
      }
      if (car.change) stop = lane.length - 8;
      // Stay behind the actual predecessor, including its rear on the outgoing lane.
      const position = (c: FlowCar) => c.lane === car.lane ? c.s : c.s / c.lane.length * car.lane.length;
      const leader = [...this.byLane.get(car.lane) ?? [], ...this.departing.get(car.lane) ?? []]
        .filter(c => c !== car && position(c) > car.s && (c.route === car.route || position(c) < lane.length + c.length / 2 + GAP))
        .sort((a, b) => position(a) - position(b))[0];
      if (leader) {
        stop = Math.min(stop, position(leader) - (car.length + leader.length) / 2 - GAP);
        if (stop - car.s < 0.5) car.blockedBy = `leader:${leader.key}`;
      }
      if (next && car.s > lane.length - 40) {
        const tail = this.byLane.get(next)?.at(-1);
        if (tail && tail !== car) {
          const exitStop = lane.length + (crossing ? lane.connector!.length : 0) + tail.s - (car.length + tail.length) / 2 - GAP;
          stop = Math.min(stop, exitStop);
          if (exitStop - car.s < 0.5) car.blockedBy = `receiving:${tail.key}`;
        }
      }
      const gap = Math.max(0, stop - car.s);
      desired = Math.min(desired, Math.sqrt(2 * BRAKE * gap));
      // A speed-dependent following headway avoids robotic bumper-to-bumper pursuit.
      if (leader) desired = Math.min(desired, Math.max(0, (position(leader) - car.s - (car.length + leader.length) / 2 - GAP) / 1.25));
      const dv = desired - car.speed;
      car.speed = Math.max(0, car.speed + Math.max(-BRAKE * FLOW_STEP, Math.min(ACCEL * FLOW_STEP, dv)));
      let step = Math.min(car.speed * FLOW_STEP, gap);
      const originalS = car.s;
      if (car.change) {
        const progress = car.change.progress;
        car.change.rate = Math.min(1, car.speed / 3) / car.change.duration;
        car.change.progress = Math.min(1, progress + FLOW_STEP * car.change.rate);
        if (this.obstructed(car, sampleFlowPose(car, 0, () => 0))) { car.change.progress = progress; car.change.rate = 0; }
      }
      // A final swept-footprint guard also covers malformed/fragmented junctions and cross-tile geometry.
      // It stops before contact; it never resolves an overlap by teleporting or hiding a car.
      for (let ds = Math.min(0.3, step); ds <= step + 1e-7 && step > 0; ds = Math.min(step, ds + 0.3)) {
        car.s = originalS + ds;
        const candidate = sampleFlowPose(car, 0, () => 0);
        if (this.obstructed(car, candidate)) {
          let lo = Math.max(0, ds - 0.3), hi = ds;
          for (let k = 0; k < 9; k++) {
            const mid = (lo + hi) / 2; car.s = originalS + mid;
            if (this.obstructed(car, sampleFlowPose(car, 0, () => 0))) hi = mid; else lo = mid;
          }
          step = lo; break;
        }
        if (ds === step) break;
      }
      car.s = originalS + Math.max(0, step);
      if (step < car.speed * FLOW_STEP - 1e-6) car.speed = step / FLOW_STEP;
      car.stopped = car.speed < 0.15 ? car.stopped + FLOW_STEP : 0;
      car.waiting = remaining < 14 && car.speed < 0.5 ? car.waiting + FLOW_STEP : 0;
      if (car.change?.progress === 1) {
        car.change = null; car.signal = null; car.changeCooldown = this.tick + 100; this.stats.laneChanges++;
      }
      if (car.reservation && car.reservation !== car.route && car.s > car.length / 2 + GAP) car.reservation = null;
      const end = lane.length + (crossing ? lane.connector!.length : 0);
      if (car.s >= end - 1e-7) {
        if (!next) { this.unoccupy(car); this.cars.delete(car.key); this.stats.exited++; }
        else {
          const previous = this.byLane.get(car.lane);
          if (previous) { const i = previous.indexOf(car); if (i >= 0) previous.splice(i, 1); }
          car.s -= end; car.lane = next; car.hops++; car.route = this.chooseRoute(next, car.key, car.hops);
          const list = this.byLane.get(next);
          if (list) { list.push(car); list.sort((a, b) => b.s - a.s); } else this.byLane.set(next, [car]);
        }
      }
      this.unoccupy(car);
      sampleFlowPose(car, 0, () => 0, car.pose);
      if (this.cars.has(car.key)) this.occupy(car);
    }
  }

  private spawn(lane: Lane, n: number, t: number) {
    const key = `${lane.tile}:${lane.id}:${n}`;
    if (this.suppressed.has(key)) return;
    const model = pick(hash01('model', key), MODEL_WEIGHTS);
    const [width, , length] = MODEL_SIZE[TRAFFIC_MODELS[model]];
    const tail = this.byLane.get(lane)?.at(-1);
    if (tail && tail.s < (length + tail.length) / 2 + GAP) { this.stats.blocked++; return; }
    const route = this.chooseRoute(lane, key);
    const car: FlowCar = { key, lane, route, n, model, width, length, born: t, s: 0, speed: 0, hops: 0,
      previous: { route, s: 0, speed: 0, change: null }, change: null, signal: null, changeCooldown: 0,
      reservation: null, stopped: 0, waiting: 0, blockedBy: null, pose: blankPose() };
    sampleFlowPose(car, 0, () => 0, car.pose);
    if (this.obstructed(car, car.pose)) { this.stats.blocked++; return; }
    this.cars.set(key, car);
    const list = this.byLane.get(lane);
    if (list) list.push(car); else this.byLane.set(lane, [car]);
    this.stats.entered++;
    this.occupy(car);
  }

  private chooseRoute(lane: Lane, key: string, hops = 0): Lane {
    const seed = `${key}/${hops}`;
    const routes = this.changer?.routes(lane, seed) ?? this.movements.get(lane) ?? [lane];
    const weights = routes.map(r => (!r.connector || Math.abs(r.connector.turn) < 0.5 ? 0.65 : r.connector.turn > 0 ? 0.22 : 0.13)
      * Math.min(1, (r.next?.flow ?? lane.flow) / lane.flow));
    let value = hash01('route', seed, lane.tile, lane.id) * weights.reduce((a, b) => a + b, 0);
    for (let i = 0; i < routes.length; i++) { value -= weights[i]; if (value <= 0) return routes[i]; }
    return routes[routes.length - 1];
  }

  private beginChange(car: FlowCar, target: Lane) {
    const from = car.lane, list = this.byLane.get(from), direction = target.k > from.k ? 1 : -1;
    if (list) { const i = list.indexOf(car); if (i >= 0) list.splice(i, 1); }
    car.change = { from, direction, progress: 0, duration: 3.2, rate: 0 };
    car.s = car.s / from.length * target.length; car.lane = target; car.route = this.chooseRoute(target, car.key, car.hops);
    const to = this.byLane.get(target);
    if (to) { to.push(car); to.sort((a, b) => b.s - a.s); } else this.byLane.set(target, [car]);
    const departing = this.departing.get(from);
    if (departing) departing.push(car); else this.departing.set(from, [car]);
  }

  private cell(p: CarPose) { return `${Math.floor(p.x / 8)},${Math.floor(p.z / 8)}`; }
  private occupy(car: FlowCar) {
    const key = this.cell(car.pose), cell = this.occupancy.get(key);
    if (cell) cell.add(car); else this.occupancy.set(key, new Set([car]));
  }
  private unoccupy(car: FlowCar) { this.occupancy.get(this.cell(car.pose))?.delete(car); }
  private obstructed(car: FlowCar, pose: CarPose) {
    const x = Math.floor(pose.x / 8), z = Math.floor(pose.z / 8);
    for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) {
      for (const other of this.occupancy.get(`${x + i},${z + j}`) ?? []) {
        if (car === other) continue;
        if (bodyOverlap(pose, car, other.pose, other, 0.25)) { car.blockedBy = `footprint:${other.key}`; return true; }
      }
    }
    return false;
  }

  private mayEnter(car: FlowCar, heads: Map<Lane, FlowCar>, reservations: FlowCar[], t: number) {
    const lane = car.route, next = lane === car.lane && lane.end.kind === 'seam' ? this.network.successor(car.lane) : lane.next;
    const blocked = (reason: string) => { car.blockedBy = reason; return false; };
    if (heads.get(car.lane) !== car) return blocked('queue-head');
    if (!next || !lane.connector) return blocked('no-exit');
    if (signalState(lane, t) !== 'green') return blocked('signal');
    if (lane.priority === 'stop' && car.stopped < 1) return blocked('stop-sign');
    const tail = this.byLane.get(next)?.at(-1);
    if (tail && tail !== car && tail.s < car.length + tail.length / 2 + 2 * GAP + 0.2) return blocked(`exit:${tail.key}`);
    const conflicts = this.conflicts.get(lane) ?? new Set<Lane>();
    for (const active of reservations) if (active !== car && active.reservation && conflicts.has(active.reservation)) return blocked(`reservation:${active.key}`);
    const h = heading(lane);
    for (const otherLane of conflicts) {
      const other = heads.get(this.baseOf.get(otherLane) ?? otherLane);
      if (!other || other.route !== otherLane || other === car || other.reservation || signalState(otherLane, t) !== 'green') continue;
      const distance = otherLane.length - other.s - other.length / 2;
      if (distance > Math.max(12, other.speed * 2.5)) continue;
      if (lane.priority !== 'normal' && otherLane.priority === 'normal') return blocked(`give-way:${other.key}`);
      if (lane.priority === 'normal' && otherLane.priority !== 'normal') continue;
      const o = heading(otherLane), cross = h[0] * o[1] - h[1] * o[0], dot = h[0] * o[0] + h[1] * o[1];
      // Oncoming traffic has priority over a left turn. At unsigned intersections yield to the right.
      const yields = dot < -0.5 ? lane.connector.turn < -0.35 : cross < -0.3;
      if (protectedLeft(lane, t)) continue;
      if (!yields) continue;
      // Four simultaneous right-hand obligations need a deterministic courteous tie break after a full stop.
      if (car.waiting > 4 && other.waiting > 4 && (car.waiting > other.waiting + 1
        || Math.abs(car.waiting - other.waiting) <= 1 && car.key < other.key)) continue;
      return blocked(`right-hand:${other.key}`);
    }
    return true;
  }
}

function heading(lane: Lane): [number, number] {
  const p = lane.points, n = p.length, dx = p[n - 2] - p[n - 4], dz = p[n - 1] - p[n - 3], d = Math.hypot(dx, dz) || 1;
  return [dx / d, dz / d];
}

function pathsConflict(a: Lane, b: Lane) {
  const ca = a.connector!, cb = b.connector!;
  // Centreline separation includes both half-widths plus a swept-body margin on corners. Build-time only.
  for (let s = -6; s <= ca.length + 6; s += 1) {
    const p = routePoint(a, s + a.length);
    for (let u = -6; u <= cb.length + 6; u += 1) {
      const q = routePoint(b, u + b.length);
      if (bodyOverlap({ ...p, y: 0, pitch: 0, speed: 0 }, { length: 5, width: 2 },
        { ...q, y: 0, pitch: 0, speed: 0 }, { length: 5, width: 2 }, 0.6)) return true;
    }
  }
  return false;
}

export function bodyOverlap(a: CarPose, sizeA: { width: number; length: number },
  b: CarPose, sizeB: { width: number; length: number }, margin = 0) {
  const dx = b.x - a.x, dz = b.z - a.z;
  if (Math.abs(dx) > 7 || Math.abs(dz) > 7) return false;
  for (const [x, z] of [[a.hx, a.hz], [-a.hz, a.hx], [b.hx, b.hz], [-b.hz, b.hx]]) {
    const ra = Math.abs(a.hx * x + a.hz * z) * (sizeA.length / 2 + margin)
      + Math.abs(-a.hz * x + a.hx * z) * (sizeA.width / 2 + margin);
    const rb = Math.abs(b.hx * x + b.hz * z) * sizeB.length / 2 + Math.abs(-b.hz * x + b.hx * z) * sizeB.width / 2;
    if (Math.abs(dx * x + dz * z) >= ra + rb) return false;
  }
  return true;
}

function routePoint(lane: Lane, s: number) {
  if (s <= lane.length || !lane.connector) return pointAt(lane.points, lane.cum, s);
  if (s <= lane.length + lane.connector.length || !lane.next) return pointAt(lane.connector.points, lane.connector.cum, s - lane.length);
  return pointAt(lane.next.points, lane.next.cum, s - lane.length - lane.connector.length);
}

export function sampleFlowPose(car: FlowCar, _dt: number, height: LaneHeight, out: CarPose = blankPose()): CarPose {
  const lane = car.route, s = car.s;
  const mid = routePoint(lane, s), front = routePoint(lane, s + 1.3), back = routePoint(lane, s - 1.3);
  const dx = front.x - back.x, dz = front.z - back.z, d = Math.hypot(dx, dz);
  out.x = mid.x; out.z = mid.z; out.hx = d > 0.01 ? dx / d : mid.hx; out.hz = d > 0.01 ? dz / d : mid.hz;
  out.speed = car.speed;
  const y = height(lane, Math.min(s, lane.length));
  if (s > lane.length && lane.next && lane.connector) {
    const yn = height(lane.next, 0), f = Math.min(1, (s - lane.length) / lane.connector.length);
    out.y = y === null ? yn ?? NaN : yn === null ? y : y + (yn - y) * f;
  } else out.y = y ?? NaN;
  const yf = height(lane, Math.min(lane.length, s + 1.3)), yb = height(lane, Math.max(0, s - 1.3));
  out.pitch = yf !== null && yb !== null && d > 0.5 ? Math.atan2(yf - yb, d) : 0;
  if (car.change) {
    const change = car.change, u = change.progress, f = u * u * u * (10 + u * (-15 + 6 * u));
    const ratio = change.from.length / lane.length, sourceS = s * ratio;
    const from = pointAt(change.from.points, change.from.cum, sourceS);
    const df = 30 * u * u * (1 - u) * (1 - u) * change.rate;
    const vx = car.speed * (from.hx * ratio * (1 - f) + mid.hx * f) + (mid.x - from.x) * df;
    const vz = car.speed * (from.hz * ratio * (1 - f) + mid.hz * f) + (mid.z - from.z) * df;
    const v = Math.hypot(vx, vz);
    out.x = from.x + (mid.x - from.x) * f; out.z = from.z + (mid.z - from.z) * f;
    if (v > 0.05) { out.hx = vx / v; out.hz = vz / v; }
    const sourceY = height(change.from, sourceS);
    if (sourceY !== null && Number.isFinite(out.y)) out.y = sourceY + (out.y - sourceY) * f;
    out.speed = v;
  }
  return out;
}

/** Render one tick behind the clock, between two collision-checked poses. Never extrapolate into a blocked gap. */
export function interpolateFlowPose(car: FlowCar, fraction: number, height: LaneHeight, out: CarPose = blankPose()): CarPose {
  const a = sampleFlowPose({ ...car, ...car.previous }, 0, height), b = sampleFlowPose(car, 0, height);
  const f = Math.max(0, Math.min(1, fraction));
  out.x = a.x + (b.x - a.x) * f; out.z = a.z + (b.z - a.z) * f; out.y = a.y + (b.y - a.y) * f;
  const hx = a.hx + (b.hx - a.hx) * f, hz = a.hz + (b.hz - a.hz) * f, d = Math.hypot(hx, hz) || 1;
  out.hx = hx / d; out.hz = hz / d; out.pitch = a.pitch + (b.pitch - a.pitch) * f;
  out.speed = a.speed + (b.speed - a.speed) * f;
  return out;
}
