// Deterministic fixed-step traffic. Demand still comes from the street/area/hour slot model, but a slot
// creates a driver, not a moving timetable: drivers follow queues and reserve space before entering a junction.
// All resident lanes participate, independent of the camera ring, render cap or local frame rate.
import { MODEL_SIZE, MODEL_WEIGHTS, TRAFFIC, TRAFFIC_MODELS } from './constants';
import { hash01, pick } from './hash';
import { blankPose, entryTime, pointAtInto, type CarPose, type LaneHeight, type TrafficNetwork } from './network';
import { connector, cumulative, type Connector, type Lane } from './tileNetwork';
import { LANE_CHANGE_SECONDS, LaneChangePlanner, type LaneChange, type LaneSignal } from './laneChanges';
import { signalState, protectedLeft } from './controls';
export { signalState } from './controls';
import { bodyOverlap, pedestrianStop, vehicleStop, type PedestrianFrame, type TrafficPedestrian, type VehicleFrame,
  type TrafficVehicle } from './pedestrians';
export { bodyOverlap } from './pedestrians';
import { slotShare } from './density';

export const FLOW_STEP = 0.1;
const GAP = 2, BRAKE = 3.5, ACCEL = 1.6;
/** Going round a stopped player car (or a crashed one): the driver waits PASS_AFTER seconds (scaled by its honk
 *  patience), keeps PASS_MARGIN metres beside it, never leaves the lane by more than PASS_MAX metres, and needs the
 *  corridor clear for the pass plus PASS_LOOKAHEAD seconds of oncoming travel (pulling out and driving past from a
 *  standstill takes about eight). */
const PASS_AFTER = 4, PASS_MARGIN = 0.6, PASS_MAX = 3.8, PASS_LOOKAHEAD = 9;
/** How far an oncoming driver squeezes right to let a passing car through (m). */
const PASS_SQUEEZE = 1.6;
const cmp = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;

export interface FlowCar {
  key: string; lane: Lane; route: Lane; n: number; s: number; speed: number; hops: number;
  length: number; width: number; model: number; born: number; demandDraw: number;
  /** The connector remains reserved until the rear clears the destination lane. */
  reservation: Lane | null;
  stopped: number; waiting: number;
  /** Seconds held up below walking pace by a player (on foot or in a vehicle) in its path: the driver honks. */
  held: number;
  /** Lateral offset from the lane centre (m, + right) while going round a stopped car, its target, its rate (m/s)
   *  the arc length at which the car steers back, and seconds a pass has stalled on something other than a player. */
  swerve: number; swerveTarget: number; swerveRate: number; swerveEnd: number; swerveStall: number;
  blockedBy: string | null;
  change: LaneChange | null; signal: LaneSignal | null; changeCooldown: number;
  previous: { route: Lane; s: number; speed: number; change: LaneChange | null; swerve: number; swerveRate: number };
  pose: CarPose;
}

export interface TrafficCheckpoint {
  version: number; roads: string; tick: number; epoch: number;
  suppressed: string[];
  pedestrians: PedestrianFrame | null;
  demand: [string, number][];
  cars: { key: string; lane: string; route: string; reservation: string | null; n: number; s: number; speed: number;
    born: number; stopped: number; waiting: number; held: number; swerve: number; swerveTarget: number; swerveRate: number;
    swerveEnd: number; swerveStall: number; changeCooldown: number; hops: number; demandDraw: number;
    change: (Omit<LaneChange, 'from'> & { from: string }) | null;
    signal: (Omit<LaneSignal, 'target'> & { target: string }) | null;
    previous: { route: string; s: number; speed: number; change: (Omit<LaneChange, 'from'> & { from: string }) | null;
      swerve: number; swerveRate: number } }[];
}

const saveChange = (change: LaneChange | null) => change ? { ...change, from: `${change.from.tile}:${change.from.id}` } : null;

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
  /** The current build's movement index for conflict sets (built lazily by conflictsOf). */
  private index: { movements: Lane[]; order: Map<Lane, number>; grid: Map<number, number[]>; keyOf: Map<Lane, string>;
    kept: Set<Lane>; previous: Map<Lane, Set<Lane>>; samples: Map<Lane, PathSamples>; seen: Uint32Array; stamp: number } | null = null;

  /** Movements whose paths conflict with `a` at its junction, built on first use and kept for this build. */
  conflictsOf(a: Lane): Set<Lane> {
    let set = this.conflicts.get(a);
    if (set) return set;
    set = new Set<Lane>();
    this.conflicts.set(a, set);
    const ix = this.index, i = ix?.order.get(a);
    if (!ix || i === undefined || !a.connector) return set;
    const near: number[] = [], stamp = ++ix.stamp;
    for (let gx = Math.floor(a.bbox[0] / CONFLICT_CELL); gx <= Math.floor(a.bbox[2] / CONFLICT_CELL); gx++) {
      for (let gz = Math.floor(a.bbox[1] / CONFLICT_CELL); gz <= Math.floor(a.bbox[3] / CONFLICT_CELL); gz++) {
        for (const j of ix.grid.get(conflictCell(gx, gz)) ?? []) if (ix.seen[j] !== stamp) { ix.seen[j] = stamp; near.push(j); }
      }
    }
    near.sort((p, q) => p - q);
    const before = ix.kept.has(a) ? ix.previous.get(a) : undefined;
    const sample = (m: Lane) => { let s = ix.samples.get(m); if (!s) { s = pathSamples(m); ix.samples.set(m, s); } return s; };
    for (const j of near) {
      const b = ix.movements[j];
      if (a === b || a.bbox[0] > b.bbox[2] + 8 || a.bbox[2] < b.bbox[0] - 8
        || a.bbox[1] > b.bbox[3] + 8 || a.bbox[3] < b.bbox[1] - 8) continue;
      // Two kept movements: last build's answer stands.
      if (before && ix.kept.has(b)) { if (before.has(b)) set.add(b); continue; }
      if (a.next === b.next) { set.add(b); continue; }
      const key = `${ix.keyOf.get(a)}|${ix.keyOf.get(b)}`;
      let conflict = this.conflictCache.get(key);
      if (conflict === undefined) {
        // One sweep answers both directions (the swept-body margin pads the first path only).
        const [ab, ba] = pathsConflict(sample(a), sample(b));
        this.conflictCache.set(key, conflict = ab); this.conflictCache.set(`${ix.keyOf.get(b)}|${ix.keyOf.get(a)}`, ba);
      }
      if (conflict) set.add(b);
    }
    return set;
  }

  /** The movements a car on `lane` may take at its end (the autopilot's lane router reads them; empty before the
   *  first update or reset builds the graph). */
  routesOf(lane: Lane): readonly Lane[] { return this.movements.get(lane) ?? []; }

  /** Every movement's conflict set (QA and tests; play builds them as cars reach junctions). */
  allConflicts(): Map<Lane, Set<Lane>> {
    for (const m of this.index?.movements ?? []) if (m.connector) this.conflictsOf(m);
    return this.conflicts;
  }

  /** Connector curves by lane pair (pure geometry of the two lanes), and each tile's lanes in canonical order: kept
   *  across graph rebuilds, so a rebuild only builds curves for lanes it has not seen. */
  private connectors = new WeakMap<Lane, Map<Lane, Connector>>();
  private sortedLanes = new WeakMap<object, Lane[]>();
  /** Conflict-cache key of a movement (its tiles' road hashes and ids), per movement object. */
  private movementKeys = new WeakMap<Lane, string>();
  private link(a: Lane, b: Lane): Connector {
    let byB = this.connectors.get(a);
    if (!byB) { byB = new Map(); this.connectors.set(a, byB); }
    let curve = byB.get(b);
    if (!curve) { curve = connector(a, b); byB.set(b, curve); }
    return curve;
  }
  /** Lanes whose movements cross or reach a tile seam (they depend on the neighbour tile). */
  private seamBound = new Set<Lane>();
  /** Road hash of every enabled tile at the last movement build. */
  private builtTiles = new Map<string, string>();
  /** Directed movement pair → conflict, kept across graph rebuilds (keys carry the tiles' road hashes). */
  private conflictCache = new Map<string, boolean>();
  private movements = new Map<Lane, Lane[]>();
  private baseOf = new Map<Lane, Lane>();
  private demand = new Map<Lane, number>();
  private occupancy = new Map<string, Set<FlowCar>>();
  private suppressed = new Set<string>();
  private changer: LaneChangePlanner | null = null;
  private departing = new Map<Lane, FlowCar[]>();
  private pedestrians: PedestrianFrame | null = null;
  private players: PedestrianFrame | null = null;
  private playerVehicles: VehicleFrame | null = null;
  stats = { entered: 0, exited: 0, blocked: 0, resets: 0, ticks: 0, laneChanges: 0 };

  constructor(readonly network: TrafficNetwork, epoch?: number) { this.epoch = epoch ?? null; }

  setPedestrians(people: TrafficPedestrian[], time: number) { this.pedestrians = { time, people }; }
  /** Every player this client knows of: on foot (the local character, remote walkers) and their vehicles (the
   *  local driven car, remote players' cars). Drivers stop for them and honk when held up. */
  setPlayers(people: TrafficPedestrian[], vehicles: TrafficVehicle[], time: number) {
    this.players = people.length ? { time, people } : null;
    this.playerVehicles = vehicles.length ? { time, vehicles } : null;
  }

  desired(car: FlowCar, time: number) {
    const density = this.network.density;
    return !density || car.demandDraw < slotShare(car.lane.flow, density.hourAt(time), density.multiplier);
  }

  private roadIdentity() { return [...this.network.tiles.values()].filter(t => this.network.isEnabled(t.tile))
    .map(t => `${t.tile}:${t.hash}`).sort(cmp).join('|'); }

  /** Shared-world contract: a pinned road generation plus this tick-stamped state is sufficient for a late
   * joining peer. Camera residency and the wall time at which a client starts are not part of a checkpoint. */
  checkpoint(): TrafficCheckpoint {
    return { version: TRAFFIC.version, roads: this.roadIdentity(), tick: this.tick, epoch: this.epoch!,
      suppressed: [...this.suppressed].sort(cmp), pedestrians: this.pedestrians
        ? { time: this.pedestrians.time, people: this.pedestrians.people.map(p => ({ ...p })) } : null,
      demand: [...this.demand].map(([lane, n]) => [`${lane.tile}:${lane.id}`, n]),
      cars: [...this.cars.values()].map(c => ({ key: c.key, lane: `${c.lane.tile}:${c.lane.id}`, route: `${c.route.tile}:${c.route.id}`,
        reservation: c.reservation ? `${c.reservation.tile}:${c.reservation.id}` : null, n: c.n, s: c.s, speed: c.speed, born: c.born,
        stopped: c.stopped, waiting: c.waiting, held: c.held, swerve: c.swerve, swerveTarget: c.swerveTarget, swerveRate: c.swerveRate,
        swerveEnd: c.swerveEnd, swerveStall: c.swerveStall, hops: c.hops, demandDraw: c.demandDraw,
        change: saveChange(c.change), changeCooldown: c.changeCooldown,
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
    this.pedestrians = state.pedestrians ? { time: state.pedestrians.time, people: state.pedestrians.people.map(p => ({ ...p })) } : null;
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
    // Incremental rebuild: a lane's movements depend on its own tile and, through seams, its neighbours'. Lanes
    // whose tile and neighbours are unchanged keep their movement objects; conflicts between kept movements too.
    const enabled = [...this.network.tiles.values()].filter(tile => this.network.isEnabled(tile.tile)).sort((a, b) => cmp(a.tile, b.tile));
    const tileKeys = new Map(enabled.map(tile => [tile.tile, tile.hash]));
    const affected = new Set<string>();
    for (const [tile, hash] of tileKeys) if (this.builtTiles.get(tile) !== hash) affected.add(tile);
    for (const tile of this.builtTiles.keys()) if (!tileKeys.has(tile)) affected.add(tile);
    const neighbours = (tile: string) => {
      const m = /^t0_(-?\d+)_(-?\d+)$/.exec(tile);
      return m ? [-1, 0, 1].flatMap(i => [-1, 0, 1].map(j => `t0_${+m[1] + i}_${+m[2] + j}`)) : null;
    };
    let full = false;
    const stale = new Set<string>();
    for (const tile of affected) { const n = neighbours(tile); if (!n) full = true; else n.forEach(t => stale.add(t)); }
    // A neighbour of a changed tile keeps the lanes whose movements never reached a seam.
    const keep = (lane: Lane) => !full && !affected.has(lane.tile) && (!stale.has(lane.tile) || !this.seamBound.has(lane));
    const seamBound = new Set<Lane>();
    const previousMovements = this.movements, previousConflicts = this.conflicts;
    this.movements = new Map(); this.conflicts = new Map(); this.baseOf.clear();
    this.index = null;
    this.builtTiles = tileKeys;
    this.lanes = enabled.flatMap(tile => {
      let sorted = this.sortedLanes.get(tile);
      if (!sorted) { sorted = [...tile.lanes].sort((a, b) => cmp(a.id, b.id)); this.sortedLanes.set(tile, sorted); }
      return sorted;
    });
    // Lanes leaving each junction node, in canonical lane order (one pass instead of a scan per lane).
    const startsAt = new Map<string, Lane[]>();
    for (const b of this.lanes) {
      if (b.start.kind !== 'junction') continue;
      const key = `${b.tile}|${b.start.node}`, list = startsAt.get(key);
      if (list) list.push(b); else startsAt.set(key, [b]);
    }
    const movements: Lane[] = [];
    const kept = new Set<Lane>();
    for (const lane of this.lanes) {
      const reuse = keep(lane) ? previousMovements.get(lane) : undefined;
      if (reuse) {
        if (reuse[0] !== lane) for (const route of reuse) { movements.push(route); this.baseOf.set(route, lane); kept.add(route); }
        this.movements.set(lane, reuse);
        if (this.seamBound.has(lane)) seamBound.add(lane);
        continue;
      }
      if (lane.end.kind === 'seam') seamBound.add(lane);
      const seamNext = lane.end.kind === 'seam' ? this.network.successor(lane) : null;
      if (lane.end.kind === 'seam' && (!seamNext || seamNext.length >= 12)) { this.movements.set(lane, [lane]); continue; }
      const outgoing = seamNext ? [seamNext]
        : startsAt.get(`${lane.tile}|${lane.end.node}`) ?? [];
      const routes: Lane[] = [];
      const fallback: Lane[] = [];
      for (const first of outgoing) {
        let next = first, curve = this.link(lane, next);
        let signal = lane.signal, priority = lane.priority;
        const visited = new Set<Lane>([lane]);
        // Crossings and signal heads split OSM roads into 2–5 m links. Those links cannot store a car.
        // Treat them as part of one junction movement and reserve through to a usable receiving lane.
        for (let guard = 0; next.length < 12 && guard < 12 && !visited.has(next); guard++) {
          visited.add(next);
          if (next.end.kind === 'seam') seamBound.add(lane);
          signal ??= next.signal;
          if (next.priority !== 'normal') priority = next.priority;
          const candidates = next.end.kind === 'seam' ? [this.network.successor(next)].filter((l): l is Lane => !!l)
            : startsAt.get(`${next.tile}|${next.end.node}`) ?? [];
          const onward = candidates.filter(b => !visited.has(b)).sort((a, b) => {
            const ca = Math.abs(this.link(next, a).turn), cb = Math.abs(this.link(next, b).turn);
            return ca - cb || Math.abs(a.k - next.k) - Math.abs(b.k - next.k) || cmp(a.id, b.id);
          })[0];
          if (!onward || visited.has(onward)) break;
          const bend = this.link(next, onward);
          const points = Float64Array.from([...curve.points, ...next.points.slice(2), ...bend.points.slice(2)]);
          const cum = cumulative(points);
          curve = { points, cum, length: cum.at(-1)!, speed: Math.min(curve.speed, bend.speed), turn: curve.turn + bend.turn };
          next = onward;
        }
        // One physical junction gets one tangent arc; chaining stop-line splinters produced tight S bends.
        if (visited.size > 1 && curve.length < 45) curve = this.link(lane, next);
        const turn = Math.atan2(Math.sin(curve.turn), Math.cos(curve.turn));
        if (curve.turn !== turn) curve = { ...curve, turn }; // cached connectors are shared, never mutated
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
      // Short-link folds can reach the same movement twice. Following, reservations and checkpoints
      // must all refer to one canonical object for that movement ID.
      const unique = [...new Map(routes.map(route => [route.id, route])).values()];
      for (const route of unique) { movements.push(route); this.baseOf.set(route, lane); }
      this.movements.set(lane, unique.length ? unique : [lane]);
    }
    // Conflicts depend only on the two movements' geometry. A rebuild only indexes the movements (a grid of their
    // bounds); each movement's set is built when a car first needs it at a junction (conflictsOf), from last build's
    // answer for two kept movements or a pair cache kept across rebuilds (keyed by both ends' tile road hashes). A
    // tile install therefore costs nothing here, and the sets are the same whenever they are built.
    const keyOf = new Map<Lane, string>(), grid = new Map<number, number[]>(), order = new Map<Lane, number>();
    const hashOf = (tile: string) => this.network.tiles.get(tile)?.hash ?? '';
    movements.forEach((m, i) => {
      if (!m.connector) return;
      order.set(m, i);
      let key = this.movementKeys.get(m);
      if (!key) { key = `${m.tile}#${hashOf(m.tile)}:${m.id}:${m.next ? `${m.next.tile}#${hashOf(m.next.tile)}` : ''}`; this.movementKeys.set(m, key); }
      keyOf.set(m, key);
      for (let gx = Math.floor((m.bbox[0] - 8) / CONFLICT_CELL); gx <= Math.floor((m.bbox[2] + 8) / CONFLICT_CELL); gx++) {
        for (let gz = Math.floor((m.bbox[1] - 8) / CONFLICT_CELL); gz <= Math.floor((m.bbox[3] + 8) / CONFLICT_CELL); gz++) {
          const key = conflictCell(gx, gz), list = grid.get(key);
          if (list) list.push(i); else grid.set(key, [i]);
        }
      }
    });
    if (this.conflictCache.size > 400_000) this.conflictCache.clear();
    this.index = { movements, order, grid, keyOf, kept, previous: previousConflicts, samples: new Map(), seen: new Uint32Array(movements.length), stamp: 0 };
    this.seamBound = seamBound;
    this.changer = new LaneChangePlanner(this.lanes, this.movements);
    // Demand enters at directed graph boundaries. Conveyor matching cycles are not additional portals:
    // injecting cars on every such loop accumulated traffic in cul-de-sacs and invented mid-block sources.
    const inbound = new Set<Lane>();
    for (const [lane, routes] of this.movements) for (const route of routes) {
      const next = route === lane && lane.end.kind === 'seam' ? this.network.successor(lane) : route.next;
      if (next) inbound.add(next);
    }
    this.sources = this.lanes.filter(l => l.length >= 12 && !inbound.has(l));
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
      car.previous = { route: car.route, s: car.s, speed: car.speed, change: car.change ? { ...car.change } : null,
        swerve: car.swerve, swerveRate: car.swerveRate };
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
      const target = car.swerve || car.swerveTarget ? null : this.changer?.target(car, this.tick, this.byLane);
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
      if (car.change) {
        stop = lane.length - 8;
        // Slow early enough to finish the lateral move before the junction buffer. Braking only
        // against that buffer could strand a fast entrant with a few percent still to complete.
        const timeLeft = car.change.duration * (1 - car.change.progress);
        desired = Math.min(desired, Math.max(3, (stop - car.s - 2) / Math.max(FLOW_STEP, timeLeft)));
      }
      let playerStop = Infinity, blocker: TrafficVehicle | null = null;
      if (this.pedestrians?.people.length || this.players || this.playerVehicles) {
        const roadY = dataHeight(lane, car.s);
        // The path ahead eases from the current lateral offset to the target over eight metres.
        const path = (s: number) => car.change ? sampleFlowPose({ ...car, s }, 0, () => 0)
          : shifted(routePoint(lane, s), car.swerve + (car.swerveTarget - car.swerve) * Math.max(0, Math.min(1, (s - car.s) / 8)));
        stop = Math.min(stop, pedestrianStop(car.s, car.speed, car.length, car.width, roadY, t, this.pedestrians, path, lane.crossings, lane.roadWidth));
        const vehicle = vehicleStop(car.s, car.speed, car.length, car.width, roadY, t, this.playerVehicles, path);
        playerStop = Math.min(pedestrianStop(car.s, car.speed, car.length, car.width, roadY, t, this.players, path, lane.crossings, lane.roadWidth),
          vehicle.stop);
        if (vehicle.stop === playerStop && vehicle.id !== null) blocker = this.playerVehicles!.vehicles.find(v => v.id === vehicle.id) ?? null;
      }
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
      const byPlayer = playerStop < stop;
      stop = Math.min(stop, playerStop);
      if (byPlayer && stop - car.s < 0.5) car.blockedBy = 'player';
      // Held up long enough behind a stopped car: go round it when the corridor beside it is clear.
      if (byPlayer && blocker && !car.change && !car.swerveTarget && Math.hypot(blocker.vx, blocker.vz) < 0.5
        && car.held > PASS_AFTER * (0.8 + 0.8 * hash01('honk-patience', car.key))) {
        const plan = this.passPlan(car, lane, blocker);
        if (plan) { car.swerveTarget = plan.offset; car.swerveEnd = plan.end; car.swerveStall = 0; }
      }
      const gap = Math.max(0, stop - car.s);
      desired = Math.min(desired, Math.sqrt(2 * BRAKE * gap));
      // A speed-dependent following headway avoids robotic bumper-to-bumper pursuit.
      if (leader) desired = Math.min(desired, Math.max(0, (position(leader) - car.s - (car.length + leader.length) / 2 - GAP) / 1.25));
      const dv = desired - car.speed;
      car.speed = Math.max(0, car.speed + Math.max(-BRAKE * FLOW_STEP, Math.min(ACCEL * FLOW_STEP, dv)));
      let step = Math.min(car.speed * FLOW_STEP, gap);
      const originalS = car.s;
      if (car.swerve || car.swerveTarget) {
        // Steer back once past; a pass that stalls beside oncoming traffic pulls back in and waits again.
        car.swerveStall = car.swerveTarget && !byPlayer && car.stopped > 0 ? car.swerveStall + FLOW_STEP : 0;
        if (car.swerveTarget && (car.s >= car.swerveEnd || car.swerveStall > 3)) { car.swerveTarget = 0; car.held = 0; }
        const before = car.swerve, rate = Math.max(0.7, Math.min(1.4, car.speed * 0.3));
        car.swerve += Math.max(-rate * FLOW_STEP, Math.min(rate * FLOW_STEP, car.swerveTarget - car.swerve));
        if (Math.abs(car.swerve) < 1e-3 && !car.swerveTarget) car.swerve = 0;
        const moved = sampleFlowPose(car, 0, () => 0);
        if (this.obstructed(car, moved) || this.touchesPlayer(car, moved)) car.swerve = before;
        car.swerveRate = (car.swerve - before) / FLOW_STEP;
      } else car.swerveRate = 0;
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
      if (step < 0.01 && car.blockedBy?.startsWith('footprint:')) this.makeRoom(car, lane, this.cars.get(car.blockedBy.slice(10)));
      car.stopped = car.speed < 0.15 ? car.stopped + FLOW_STEP : 0;
      car.held = byPlayer && car.speed < 1 ? car.held + FLOW_STEP : byPlayer ? car.held : 0;
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

  /** Lateral offset that clears a stopped vehicle, and the arc length to steer back at; null when the pass would
   *  leave the lane by more than PASS_MAX, run into the junction, or meet another car in the corridor. Prefers the
   *  left (towards oncoming traffic) on a tie. */
  private passPlan(car: FlowCar, lane: Lane, v: TrafficVehicle): { offset: number; end: number } | null {
    const here = routePoint(lane, car.s);
    const sv = car.s + (v.x - here.x) * here.hx + (v.z - here.z) * here.hz, at = routePoint(lane, sv);
    let lo = Infinity, hi = -Infinity;
    for (const a of [-0.5, 0.5]) for (const b of [-0.5, 0.5]) {
      const x = v.x + v.hx * a * v.length - v.hz * b * v.width, z = v.z + v.hz * a * v.length + v.hx * b * v.width;
      const across = -(x - at.x) * at.hz + (z - at.z) * at.hx;
      lo = Math.min(lo, across); hi = Math.max(hi, across);
    }
    const left = lo - car.width / 2 - PASS_MARGIN, right = hi + car.width / 2 + PASS_MARGIN;
    const offset = Math.abs(left) <= Math.abs(right) ? left : right;
    const end = sv + v.length / 2 + car.length / 2 + 3;
    if (Math.abs(offset) > PASS_MAX || end + 15 > lane.length) return null;
    for (const other of this.cars.values()) {
      if (other === car) continue;
      const dx = other.pose.x - here.x, dz = other.pose.z - here.z;
      const along = dx * here.hx + dz * here.hz, across = -dx * here.hz + dz * here.hx;
      const oncoming = Math.max(0, -(other.pose.hx * here.hx + other.pose.hz * here.hz) * other.speed);
      if (along < -car.length || along > end - car.s + 10 + oncoming * PASS_LOOKAHEAD) continue;
      if (Math.abs(across - offset) < (car.width + other.width) / 2 + 0.8) return null;
    }
    return { offset, end };
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
      demandDraw: this.network.demandDraw(lane, n),
      previous: { route, s: 0, speed: 0, change: null, swerve: 0, swerveRate: 0 }, change: null, signal: null, changeCooldown: 0,
      reservation: null, stopped: 0, waiting: 0, held: 0, swerve: 0, swerveTarget: 0, swerveRate: 0, swerveEnd: 0, swerveStall: 0,
      blockedBy: null, pose: blankPose() };
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
    car.change = { from, direction, progress: 0, duration: LANE_CHANGE_SECONDS, rate: 0 };
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

  /** Blocked head-on by a car going round a stopped one: squeeze up to PASS_SQUEEZE metres to the right until past. */
  private makeRoom(car: FlowCar, lane: Lane, other: FlowCar | undefined) {
    if (!other || !other.swerve || car.swerveTarget || car.change) return;
    const here = routePoint(lane, car.s);
    if (other.pose.hx * here.hx + other.pose.hz * here.hz > -0.5) return;
    const dx = other.pose.x - here.x, dz = other.pose.z - here.z;
    const along = dx * here.hx + dz * here.hz, across = -dx * here.hz + dz * here.hx;
    const need = across + (car.width + other.width) / 2 + 0.4;
    if (need <= 0 || need > PASS_SQUEEZE || car.s + along + 15 > lane.length) return;
    car.swerveTarget = need; car.swerveEnd = car.s + Math.max(0, along) + (car.length + other.length) / 2 + 2; car.swerveStall = 0;
  }

  /** A pose overlapping a player's vehicle (a car steering back in beside the one it is passing). */
  private touchesPlayer(car: FlowCar, pose: CarPose) {
    return !!this.playerVehicles?.vehicles.some(v => bodyOverlap(pose, car, v, v, 0.25));
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
    // A car still on a connector already owns space in its receiving lane. The lane's current tail
    // does not include it yet; admitting another car against that same gap can leave its rear in the box.
    // A clipped boundary with no resident successor drains out of the simulation. It is not a
    // stop line: requiring a whole car to fit in its final few metres would permanently close it.
    const capacity = next.end.kind === 'seam' && !this.network.successor(next) ? Infinity : next.length;
    let storage = Math.min(capacity, tail && tail !== car ? tail.s - tail.length / 2 : capacity);
    for (const active of reservations) {
      if (active === car || !active.reservation || active.reservation.next !== next || active.lane === next) continue;
      storage -= active.length + GAP;
      if (storage < car.length + 2 * GAP + 0.2) return blocked(`storage:${active.key}`);
    }
    if (storage < car.length + 2 * GAP + 0.2) return blocked('storage:lane-end');
    const conflicts = this.conflictsOf(lane);
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

function dataHeight(lane: Lane, s: number) {
  if (!lane.ys) return NaN;
  if (s > lane.length && lane.next && lane.connector) {
    const a = lane.ys.at(-1)!, b = lane.next.ys?.[0] ?? a;
    return a + (b - a) * Math.min(1, (s - lane.length) / lane.connector.length);
  }
  let i = 1;
  while (i < lane.cum.length - 1 && lane.cum[i] < s) i++;
  const f = Math.max(0, Math.min(1, (s - lane.cum[i - 1]) / Math.max(0.01, lane.cum[i] - lane.cum[i - 1])));
  return lane.ys[i - 1] + (lane.ys[i] - lane.ys[i - 1]) * f;
}

/** Grid cell for conflict candidate pairs (m), and its small-integer key. */
const CONFLICT_CELL = 32;
const conflictCell = (gx: number, gz: number) => ((gx & 0x7fff) << 15) | (gz & 0x7fff);
/** A movement's path every metre from 6 m before its stop line to 6 m into its receiving lane. */
interface PathSamples { x: Float64Array; z: Float64Array; hx: Float64Array; hz: Float64Array; box: [number, number, number, number] }
function pathSamples(lane: Lane): PathSamples {
  const length = lane.connector!.length, n = Math.floor(length + 12 + 1e-9) + 1;
  const out: PathSamples = { x: new Float64Array(n), z: new Float64Array(n), hx: new Float64Array(n), hz: new Float64Array(n),
    box: [Infinity, Infinity, -Infinity, -Infinity] };
  for (let k = 0; k < n; k++) {
    const p = routePoint(lane, -6 + k + lane.length);
    out.x[k] = p.x; out.z[k] = p.z; out.hx[k] = p.hx; out.hz[k] = p.hz;
    out.box[0] = Math.min(out.box[0], p.x); out.box[1] = Math.min(out.box[1], p.z); out.box[2] = Math.max(out.box[2], p.x); out.box[3] = Math.max(out.box[3], p.z);
  }
  return out;
}
const CONFLICT_BODY = { length: 5, width: 2 }, CONFLICT_MARGIN = 0.6;
/** Bodies further apart than this cannot overlap (sum of the padded and unpadded half diagonals). */
const CONFLICT_REACH = Math.sqrt((2.5 + CONFLICT_MARGIN) ** 2 + (1 + CONFLICT_MARGIN) ** 2) + Math.sqrt(2.5 ** 2 + 1 ** 2);
const poseA = { x: 0, z: 0, hx: 1, hz: 0 }, poseB = { x: 0, z: 0, hx: 1, hz: 0 };
/** Whether a's padded swept body meets b's path, and b's padded body meets a's path. */
function pathsConflict(a: PathSamples, b: PathSamples): [boolean, boolean] {
  // Centreline separation includes both half-widths plus a swept-body margin on corners. Build-time only.
  const r2 = CONFLICT_REACH * CONFLICT_REACH, r = CONFLICT_REACH;
  // Sample centres further apart than the reach everywhere: no body can touch the other path.
  if (a.box[0] > b.box[2] + r || b.box[0] > a.box[2] + r || a.box[1] > b.box[3] + r || b.box[1] > a.box[3] + r) return [false, false];
  let ab = false, ba = false;
  for (let i = 0; i < a.x.length && !(ab && ba); i++) {
    const ax = a.x[i], az = a.z[i];
    for (let j = 0; j < b.x.length; j++) {
      const dx = b.x[j] - ax, dz = b.z[j] - az;
      if (dx * dx + dz * dz > r2) continue;
      poseA.x = ax; poseA.z = az; poseA.hx = a.hx[i]; poseA.hz = a.hz[i];
      poseB.x = b.x[j]; poseB.z = b.z[j]; poseB.hx = b.hx[j]; poseB.hz = b.hz[j];
      ab ||= bodyOverlap(poseA, CONFLICT_BODY, poseB, CONFLICT_BODY, CONFLICT_MARGIN);
      ba ||= bodyOverlap(poseB, CONFLICT_BODY, poseA, CONFLICT_BODY, CONFLICT_MARGIN);
      if (ab && ba) break;
    }
  }
  return [ab, ba];
}

/** A path point moved `offset` metres along its right-hand normal. */
function shifted(p: { x: number; z: number; hx: number; hz: number }, offset: number) {
  return offset ? { x: p.x - p.hz * offset, z: p.z + p.hx * offset, hx: p.hx, hz: p.hz } : p;
}

function routePoint(lane: Lane, s: number) {
  return routePointInto(lane, s, { x: 0, z: 0, hx: 1, hz: 0 });
}
type Point = { x: number; z: number; hx: number; hz: number };
function routePointInto(lane: Lane, s: number, out: Point): Point {
  if (s <= lane.length || !lane.connector) return pointAtInto(lane.points, lane.cum, s, out);
  if (s <= lane.length + lane.connector.length || !lane.next) return pointAtInto(lane.connector.points, lane.connector.cum, s - lane.length, out);
  return pointAtInto(lane.next.points, lane.next.cum, s - lane.length - lane.connector.length, out);
}
const midPoint: Point = { x: 0, z: 0, hx: 1, hz: 0 }, frontPoint: Point = { ...midPoint }, backPoint: Point = { ...midPoint }, fromPoint: Point = { ...midPoint };
const scratchA = blankPose(), scratchB = blankPose();

/** What a pose depends on: a car now, or the same fields one tick earlier (`FlowCar.previous`). */
export type PoseState = Pick<FlowCar, 'route' | 's' | 'speed' | 'change' | 'swerve' | 'swerveRate'>;

export function sampleFlowPose(car: PoseState, _dt: number, height: LaneHeight, out: CarPose = blankPose()): CarPose {
  const lane = car.route, s = car.s;
  const mid = routePointInto(lane, s, midPoint), front = routePointInto(lane, s + 1.3, frontPoint), back = routePointInto(lane, s - 1.3, backPoint);
  const dx = front.x - back.x, dz = front.z - back.z, d = Math.sqrt(dx * dx + dz * dz);
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
    const from = pointAtInto(change.from.points, change.from.cum, sourceS, fromPoint);
    const df = 30 * u * u * (1 - u) * (1 - u) * change.rate;
    const vx = car.speed * (from.hx * ratio * (1 - f) + mid.hx * f) + (mid.x - from.x) * df;
    const vz = car.speed * (from.hz * ratio * (1 - f) + mid.hz * f) + (mid.z - from.z) * df;
    const v = Math.sqrt(vx * vx + vz * vz);
    out.x = from.x + (mid.x - from.x) * f; out.z = from.z + (mid.z - from.z) * f;
    if (v > 0.05) { out.hx = vx / v; out.hz = vz / v; }
    const sourceY = height(change.from, sourceS);
    if (sourceY !== null && Number.isFinite(out.y)) out.y = sourceY + (out.y - sourceY) * f;
    out.speed = v;
  }
  if (car.swerve) {
    // Going round a stopped car: shifted off the lane centre, nose turned along the lateral motion.
    out.x -= out.hz * car.swerve; out.z += out.hx * car.swerve;
    const forward = Math.max(2, out.speed), hx = out.hx * forward - out.hz * car.swerveRate, hz = out.hz * forward + out.hx * car.swerveRate;
    const d = Math.sqrt(hx * hx + hz * hz);
    out.hx = hx / d; out.hz = hz / d;
  }
  return out;
}

/** Render one tick behind the clock, between two collision-checked poses. Never extrapolate into a blocked gap. */
export function interpolateFlowPose(car: FlowCar, fraction: number, height: LaneHeight, out: CarPose = blankPose()): CarPose {
  const a = sampleFlowPose(car.previous, 0, height, scratchA), b = sampleFlowPose(car, 0, height, scratchB);
  const f = Math.max(0, Math.min(1, fraction));
  out.x = a.x + (b.x - a.x) * f; out.z = a.z + (b.z - a.z) * f; out.y = a.y + (b.y - a.y) * f;
  const hx = a.hx + (b.hx - a.hx) * f, hz = a.hz + (b.hz - a.hz) * f, d = Math.sqrt(hx * hx + hz * hz) || 1;
  out.hx = hx / d; out.hz = hz / d; out.pitch = a.pitch + (b.pitch - a.pitch) * f;
  out.speed = a.speed + (b.speed - a.speed) * f;
  return out;
}
