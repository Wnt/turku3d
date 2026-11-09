// Deterministic fixed-step traffic. Demand still comes from the street/area/hour slot model, but a slot
// creates a driver, not a moving timetable: drivers follow queues and reserve space before entering a junction.
// All resident lanes participate, independent of the camera ring, render cap or local frame rate.
import { MODEL_SIZE, MODEL_WEIGHTS, TRAFFIC, TRAFFIC_MODELS } from './constants';
import { hash01, hash32, pick } from './hash';
import { blankPose, entryTime, pointAtInto, type CarPose, type LaneHeight, type TrafficNetwork } from './network';
import { connector, cumulative, deadEnd, intoDeadEnd, smoothPath, type Connector, type Lane } from './tileNetwork';
import { LANE_CHANGE_SECONDS, LaneChangePlanner, type LaneChange, type LaneSignal } from './laneChanges';
import { greenWithin, signalState, protectedLeft } from './controls';
export { signalState } from './controls';
import { bodyOverlap, pedestrianStop, vehicleStop, type PedestrianFrame, type TrafficPedestrian, type VehicleFrame,
  type TrafficVehicle } from './pedestrians';
export { bodyOverlap } from './pedestrians';
import { slotShare } from './density';
import { PASS_CLEAR_LINE, planPass, unseen, watchDistance, type Obstacle, type PassPlan, type TrafficWatcher } from './driver';
export { unseen, type TrafficWatcher } from './driver';

export const FLOW_STEP = 0.1;
/** Clock jumps (ticks) of a live clock (the physics worker's TrafficSim): a move back of at most `hold` (a
 *  multiplayer server offset re-estimated on a welcome or a rounds message moves it by tens of milliseconds) holds
 *  the flow until the clock is there again, and a move forward of more than `catchUp` (a hidden multiplayer tab,
 *  which never pauses) is skipped. Replaying instead held one physics step for the whole session since the start-up
 *  epoch: 1.3 s after one minute of play, 11.6 s after fourteen (nine Turku centre tiles, node on the M1 Max), 3.7 s
 *  in a city.lab drive (framecheck mac-fix4-1). */
export const LIVE_CLOCK_JUMPS = { hold: 600, catchUp: 10 } as const;
const GAP = 2, BRAKE = 3.5, ACCEL = 1.6;
/** Going round a stopped player car (or a crashed one): the driver waits PASS_AFTER seconds (scaled by its honk
 *  patience), keeps PASS_MARGIN metres beside it, never leaves the lane by more than PASS_MAX metres, and needs the
 *  corridor clear for the pass plus PASS_LOOKAHEAD seconds of oncoming travel (pulling out and driving past from a
 *  standstill takes about eight). */
const PASS_AFTER = 4;
/** A footprint-locked car ahead counts as an obstacle after PASS_LOCKED seconds without headway; a standing player is
 *  a PERSON_BODY m square. */
const PASS_LOCKED = 20, PERSON_BODY = 0.8;
/** Nose up against an obstacle it can get round (at its stop point), a driver first backs up REVERSE_BACK m (more the
 *  more patient it is: 1.5-3 m) at REVERSE_SPEED m/s, at most REVERSE_MAX m in all until it moves on. */
const REVERSE_BACK = 1.5, REVERSE_SPEED = 2.5, REVERSE_MAX = 6;
/** GTA V blocked-lane experiments (docs/handover/gta-traffic-study.md 4.5): a front car facing a blockage it cannot get
 *  past reverses 3-8 m within 2-5 s of stopping (here BLOCKED_BACK 3-6 m after BLOCKED_AFTER 2-4 s), and only after a
 *  long wait turns round (24-114 s there; here UTURN_AFTER 30-60 s) when the opposite lane is clear. */
const BLOCKED_AFTER = 2, BLOCKED_BACK = 3, UTURN_AFTER = 30, UTURN_SECONDS = 6, UTURN_CLEAR = 15;
/** Backed off and still blocked, it rocks (GTA: about 1 m back and forth every 4.2 s): ROCK_STEP m forward for
 *  ROCK_HALF ticks, then back to where it held. */
const ROCK_STEP = 1, ROCK_HALF = 21;
/** A junction head held by a full exit for REROUTE_AFTER × its patience takes another movement with room; at most
 *  once per REROUTE_GAP ticks. */
const REROUTE_AFTER = 3, REROUTE_GAP = 300;
/** GTA drivers turn off at the last junction before a visible jam without stopping (38-45 m before the blocker): a car
 *  within REROUTE_AHEAD m of its line whose exit is jammed (its last car stuck JAM_AHEAD s, no room behind it) takes
 *  another movement with room. */
const REROUTE_AHEAD = 45, JAM_AHEAD = 10;
type Body = Obstacle<FlowCar>;
/** How far an oncoming driver squeezes right to let a passing car through (m). */
const PASS_SQUEEZE = 1.6;
/** Swept-footprint guard: the margin a moving car keeps from others, and the smaller contact margin it keeps from a
 *  car following it (the follower keeps its own gap; padding the leader against it let a leader's swinging rear stop
 *  both for good). */
const GUARD_MARGIN = 0.25, FOLLOWER_MARGIN = 0.05;
/** Surface height difference (m) at which two cars are on different levels (bridge over road). */
const LEVEL_APART = 2.5;
/** Two cars each stopped by the other's footprint this long: the one behind backs up (UNLOCK_SPEED m/s, at most
 *  UNLOCK_BACK m per lock) so the one ahead can pull away. Pure flow state, so every peer unlocks the same way. */
const UNLOCK_AFTER = 6, UNLOCK_SPEED = 0.5, UNLOCK_BACK = 1.5, UNLOCK_CHAIN = 4;
/** Last resort: cars that have waited on each other in a closed cycle (blockedBy) for CLEAR_AFTER seconds cannot all
 *  move again; the cycle's lowest key leaves the flow. Checked once per second; pure flow state, so MP-safe. */
const CLEAR_AFTER = 60, CLEAR_CYCLE = 12, STUCK_PROGRESS = 2;
/** Share of the normal lane-change rate a car keeps while held at its change's stop point. */
const CHANGE_CREEP = 0.3;
/** Seconds at the stop line after which a driver no longer yields to traffic that waited less (a give-way merge into
 *  a stream that shares its green could otherwise wait for good). */
const STARVE_AFTER = 15;
/** Reasons that keep a driver at its line until something outside the junction changes (not its turn to go). */
const HELD = /^(exit|storage|receiving|signal|no-exit)/;
/** Out-of-view recycling (docs/handover/gta-traffic-study.md): once a second, the front of a jammed queue that has
 *  stood RECYCLE_HEAD seconds while it had a way to take, or any car stuck for RECYCLE_ANY seconds, leaves the flow
 *  when no watcher can see it. A front at its line counts only the green: a queue at a red light waits, it is not
 *  jammed. Its slot's demand is not replayed: the sources keep their own timetable. */
const RECYCLE_HEAD = 20, RECYCLE_ANY = 180, RECYCLE_CHAIN = 40;
/** Beyond FAST_FAR m of every watcher, hidden jams go sooner (GTA: hidden stationary cars go within seconds there):
 *  a jammed queue's front after FAST_HEAD s, any car after FAST_ANY s. A/B against 30/15 s (2026-11-06, 30 min at
 *  16:00): east 107.6 vs 107.2, south 110.9 vs 107.5 exits/min, 0 locked and 0 recycled in view either way. */
const FAST_FAR = 150, FAST_HEAD = 5, FAST_ANY = 120;

/** Below this speed (m/s) a car stands (out-of-view recycling follows a queue through such cars). */
const STANDING = 0.15;
/** Reasons a driver gives at its stop line (mayEnter) other than a red light: its wait counts only the green. */
const AT_LINE = /^(queue-head|no-exit|stop-sign|turn|exit|storage|reservation|starved|give-way|right-hand)/;
const cmp = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;

export interface FlowCar {
  key: string; lane: Lane; route: Lane; n: number; s: number; speed: number; hops: number;
  length: number; width: number; model: number; born: number; demandDraw: number;
  /** Tick at which the car entered its lane's tile (spawned there or crossed a seam into it): a multiplayer mirror
   *  keeps a car that reached a mirrored tile after the owner's snapshot (TrafficFlow.mirror). */
  tileTick: number;
  /** The connector remains reserved until the rear clears the destination lane. */
  reservation: Lane | null;
  stopped: number; waiting: number;
  /** Metres reversed to break the current footprint deadlock (flow.unlock); 0 once the car moves on. */
  backed: number;
  /** Seconds since the car last made STUCK_PROGRESS metres of net headway, and the headway made since (m). */
  stuck: number; progress: number;
  /** The last car it waited on since it last made headway (deadlock cycles span red phases), or null. */
  waitsFor: string | null;
  /** Seconds held up below walking pace by a player (on foot or in a vehicle) in its path: the driver honks. */
  held: number;
  /** Metres still to back up before pulling out round an obstacle (0 when not reversing). */
  reverse: number;
  /** Tick of the last route change at a blocked exit (a driver changes its mind at most once per REROUTE_GAP). */
  rerouted: number;
  /** Turning round mid-block onto the opposite lane (a blockage it cannot get past), or null. */
  uturn: UTurn | null;
  /** Where a fully blocked front car rocks from (m along its lane; -1 when not rocking). */
  rock: number;
  /** Lateral offset from the lane centre (m, + right) while going round a stopped car, its target, its rate (m/s)
   *  the arc length at which the car steers back, and seconds a pass has stalled on something other than a player. */
  swerve: number; swerveTarget: number; swerveRate: number; swerveEnd: number; swerveStall: number;
  blockedBy: string | null;
  change: LaneChange | null; signal: LaneSignal | null; changeCooldown: number;
  previous: { route: Lane; s: number; speed: number; change: LaneChange | null; swerve: number; swerveRate: number; uturn: UTurn | null };
  pose: CarPose;
}



/** A U-turn from the car's lane position onto `to` at arc length `at`, `progress` 0..1 along a U-shaped path. */
export interface UTurn { to: Lane; at: number; progress: number; /** Forward reach of the U (m): the room ahead. */ reach: number }

export interface TrafficCheckpoint {
  version: number; roads: string; tick: number; epoch: number;
  suppressed: string[];
  pedestrians: PedestrianFrame | null;
  demand: [string, number][];
  cars: { key: string; lane: string; route: string; reservation: string | null; n: number; s: number; speed: number;
    born: number; stopped: number; waiting: number; backed: number; stuck: number; progress: number; waitsFor: string | null; held: number; reverse: number; rerouted: number; rock: number; swerve: number; swerveTarget: number; swerveRate: number;
    swerveEnd: number; swerveStall: number; changeCooldown: number; hops: number; demandDraw: number; tileTick?: number;
    change: (Omit<LaneChange, 'from'> & { from: string }) | null;
    signal: (Omit<LaneSignal, 'target'> & { target: string }) | null;
    uturn: (Omit<UTurn, 'to'> & { to: string }) | null;
    previous: { route: string; s: number; speed: number; change: (Omit<LaneChange, 'from'> & { from: string }) | null;
      swerve: number; swerveRate: number; uturn: (Omit<UTurn, 'to'> & { to: string }) | null } }[];
}

const saveChange = (change: LaneChange | null) => change ? { ...change, from: `${change.from.tile}:${change.from.id}` } : null;
const saveUTurn = (u: UTurn | null) => u ? { ...u, to: `${u.to.tile}:${u.to.id}` } : null;

type SavedCar = TrafficCheckpoint['cars'][number];
function saveCar(c: FlowCar): SavedCar {
  return { key: c.key, lane: `${c.lane.tile}:${c.lane.id}`, route: `${c.route.tile}:${c.route.id}`,
    reservation: c.reservation ? `${c.reservation.tile}:${c.reservation.id}` : null, n: c.n, s: c.s, speed: c.speed, born: c.born,
    stopped: c.stopped, waiting: c.waiting, backed: c.backed, stuck: c.stuck, progress: c.progress, waitsFor: c.waitsFor, held: c.held, reverse: c.reverse, rerouted: c.rerouted, rock: c.rock, swerve: c.swerve, swerveTarget: c.swerveTarget, swerveRate: c.swerveRate,
    swerveEnd: c.swerveEnd, swerveStall: c.swerveStall, hops: c.hops, demandDraw: c.demandDraw, tileTick: c.tileTick,
    change: saveChange(c.change), changeCooldown: c.changeCooldown,
    signal: c.signal ? { ...c.signal, target: `${c.signal.target.tile}:${c.signal.target.id}` } : null, uturn: saveUTurn(c.uturn),
    previous: { ...c.previous, route: `${c.previous.route.tile}:${c.previous.route.id}`, change: saveChange(c.previous.change), uturn: saveUTurn(c.previous.uturn) } };
}

/** One car of a multiplayer traffic snapshot, listed under the tile of its lane:
 *  [key, lane, movement, s, speed, bornAge, hops, demandDraw, tileAge, rest?]. A lane id of that tile is sent as
 *  `~id` (others as `tile:id`, as in checkpoints); the movement as `=` when it is the lane itself and `>next` when it
 *  continues the lane; the slot `n` is the key's last part; the ages are ticks before the snapshot; `rest` holds only
 *  the state that differs from a newly admitted car (MIRROR_FIELDS names). */
export type MirrorCar = [string, string, string, number, number, number, number, number, number, Record<string, unknown>?];
/** An owner's cars on its tiles at flow tick `tick` (TrafficFlow.snapshot), per tile; a receiver replaces its cars
 *  there with them (TrafficFlow.mirror). The room relays each receiver only the tiles it holds. */
export interface TrafficSnapshot { tick: number; tiles: Record<string, MirrorCar[]> }
/** TrafficFlow.probe. */
export interface TrafficProbe { tick: number; live: boolean; remote: string[]; tiles: Record<string, [string, string, number, number][]>;
  stats: Record<string, number> }
/** The state of a newly admitted car (spawn), left out of a MirrorCar's `rest`. */
const FRESH = { reservation: null, stopped: 0, waiting: 0, backed: 0, stuck: 0, progress: 0, waitsFor: null, held: 0, reverse: 0,
  rerouted: 0, rock: -1, swerve: 0, swerveTarget: 0, swerveRate: 0, swerveEnd: 0, swerveStall: 0, changeCooldown: 0,
  change: null, signal: null, uturn: null } as const;
type Fresh = { -readonly [K in keyof typeof FRESH]: SavedCar[K] };
/** Short names of the `rest` fields on the wire. */
const MIRROR_FIELDS: Record<keyof Fresh, string> = { reservation: 'r', stopped: 'st', waiting: 'w', backed: 'b', stuck: 'k', progress: 'p',
  waitsFor: 'f', held: 'h', reverse: 'v', rerouted: 'rr', rock: 'o', swerve: 's', swerveTarget: 'sT', swerveRate: 'sR', swerveEnd: 'sE',
  swerveStall: 'sS', changeCooldown: 'cc', change: 'c', signal: 'g', uturn: 'u' };
const FROM_WIRE = new Map(Object.entries(MIRROR_FIELDS).map(([name, short]) => [short, name as keyof Fresh]));
const round = (v: number, f: number) => Math.round(v * f) / f;

export function packCar(c: SavedCar, tile: string, tick: number): MirrorCar {
  const local = (id: string) => id.startsWith(`${tile}:`) ? `~${id.slice(tile.length + 1)}` : id;
  const route = c.route === c.lane ? '=' : c.route.startsWith(`${c.lane}>`) ? c.route.slice(c.lane.length) : local(c.route);
  const rest: Record<string, unknown> = {};
  for (const name of Object.keys(FRESH) as (keyof Fresh)[]) {
    const value = c[name];
    if (value !== FRESH[name]) rest[MIRROR_FIELDS[name]] = typeof value === 'number' ? round(value, 1000) : value;
  }
  const out: MirrorCar = [c.key, local(c.lane), route, round(c.s, 100), round(c.speed, 100), Math.round(tick - c.born / FLOW_STEP), c.hops,
    round(c.demandDraw, 1e5), tick - (c.tileTick ?? tick)];
  if (Object.keys(rest).length) out.push(rest);
  return out;
}

const idOK = (v: unknown): v is string => typeof v === 'string' && v.length > 0 && v.length <= 160;
const numOK = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
/** A received MirrorCar of `tile`'s list as a saved car, or null when it is malformed or not on that tile (another
 *  client's data: nothing in it is trusted). */
export function unpackCar(m: unknown, tile: string, tick: number): SavedCar | null {
  if (!Array.isArray(m) || m.length < 9 || m.length > 10) return null;
  const [key, laneId, routeId, s, speed, bornAge, hops, demandDraw, tileAge, rest] = m as unknown[];
  if (!idOK(key) || !idOK(laneId) || !idOK(routeId) || ![s, speed, bornAge, hops, demandDraw, tileAge].every(numOK)) return null;
  const n = Number(key.slice(key.lastIndexOf(':') + 1));
  if (!Number.isSafeInteger(n)) return null;
  const full = (id: string) => id.startsWith('~') ? `${tile}:${id.slice(1)}` : id;
  const lane = full(laneId), route = routeId === '=' ? lane : routeId.startsWith('>') ? lane + routeId : full(routeId);
  if (!lane.startsWith(`${tile}:`)) return null;
  const state: Fresh = { ...FRESH };
  if (rest !== undefined) {
    if (!rest || typeof rest !== 'object' || Array.isArray(rest)) return null;
    for (const [short, value] of Object.entries(rest)) {
      const name = FROM_WIRE.get(short);
      if (!name) return null;
      const fresh = FRESH[name];
      const ref = name === 'change' ? 'from' : name === 'uturn' ? 'to' : 'target';
      if (typeof fresh === 'number' ? !numOK(value) : value !== null && (name === 'reservation' || name === 'waitsFor' ? !idOK(value)
        : typeof value !== 'object' || Array.isArray(value) || !idOK((value as Record<string, unknown>)[ref]))) return null;
      (state as Record<string, unknown>)[name] = value;
    }
  }
  return { key, lane, route, n, s: s as number, speed: speed as number, born: (tick - (bornAge as number)) * FLOW_STEP, hops: hops as number,
    demandDraw: demandDraw as number, tileTick: tick - (tileAge as number), ...state,
    previous: { route, s: s as number, speed: speed as number, change: state.change, swerve: state.swerve, swerveRate: state.swerveRate, uturn: state.uturn } };
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
  private longest = new Map<Lane, FlowCar>();
  private pedestrians: PedestrianFrame | null = null;
  private players: PedestrianFrame | null = null;
  private playerVehicles: VehicleFrame | null = null;
  stats = { entered: 0, exited: 0, blocked: 0, resets: 0, ticks: 0, laneChanges: 0, unlocks: 0, cleared: 0, recycled: 0, escapes: 0, reverses: 0, rerouted: 0, uturns: 0, reroutedAhead: 0, mirrored: 0,
    mirrorAdded: 0, mirrorRemoved: 0, mirrorLane: 0, mirrorShift0: 0, mirrorShift1: 0, mirrorShift2: 0, mirrorShift3: 0 };
  /** Who can see cars, for out-of-view recycling; null (the default) recycles nothing. Every peer must pass the same
   *  list at the same tick (single player: the local camera; multiplayer: the server-stamped watch). */
  private watchers: TrafficWatcher[] | null = null;
  /** Recycled cars since the host last drained this list (diagnostics and tests). */
  readonly recycledLog: { key: string; x: number; z: number; t: number }[] = [];

  constructor(readonly network: TrafficNetwork, epoch?: number) { this.epoch = epoch ?? null; }

  /** Single player: the watchers from now on. */
  setWatchers(watchers: TrafficWatcher[] | null) { this.watchers = watchers; this.schedule = null; }
  /** Multiplayer: watchers for ticks [from, until) (a server-stamped list every peer schedules alike). Outside every
   *  window there are none, so a peer that missed a list recycles nothing rather than something else. */
  scheduleWatch(from: number, until: number, watchers: TrafficWatcher[]) {
    (this.schedule ??= []).push({ from, until, watchers });
    this.schedule.sort((a, b) => a.from - b.from);
    while (this.schedule.length > 8) this.schedule.shift();
  }
  private schedule: { from: number; until: number; watchers: TrafficWatcher[] }[] | null = null;

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
      cars: [...this.cars.values()].map(c => saveCar(c)) };
  }

  restore(state: TrafficCheckpoint) {
    if (state.version !== TRAFFIC.version || state.roads !== this.roadIdentity()) throw new Error('Traffic checkpoint road generation mismatch');
    this.epoch = state.epoch; this.reset(state.tick * FLOW_STEP);
    const { laneById: lanes } = this.ids!;
    this.suppressed = new Set(state.suppressed);
    this.pedestrians = state.pedestrians ? { time: state.pedestrians.time, people: state.pedestrians.people.map(p => ({ ...p })) } : null;
    this.demand = new Map(state.demand.map(([id, n]) => [lanes.get(id)!, n]));
    for (const saved of state.cars) {
      const car = this.loadCar(saved, true);
      if (!car) throw new Error('Traffic checkpoint contains an unknown movement');
      this.cars.set(car.key, car);
    }
  }

  /** A saved car on this flow's graph, or null when its lane or movement is not here. `strict`: a lane change,
   *  signal or U-turn naming a missing lane fails too (a checkpoint); otherwise that manoeuvre is dropped (a mirror). */
  private loadCar(saved: SavedCar, strict = false): FlowCar | null {
    const { laneById: lanes, routeById: routes } = this.ids!;
    const lane = lanes.get(saved.lane), route = routes.get(saved.route), previous = routes.get(saved.previous.route);
    if (!lane || !route || !previous) return null;
    let missing = false;
    const laneOf = (id: string) => { const l = lanes.get(id); if (!l) missing = true; return l; };
    const loadChange = (change: SavedCar['change']): LaneChange | null => {
      const from = change && laneOf(change.from);
      return change && from ? { ...change, from } : null;
    };
    const loadUTurn = (u: SavedCar['uturn']): UTurn | null => { const to = u && laneOf(u.to); return u && to ? { ...u, to } : null; };
    const target = saved.signal && laneOf(saved.signal.target);
    const model = pick(hash01('model', saved.key), MODEL_WEIGHTS), [width, , length] = MODEL_SIZE[TRAFFIC_MODELS[model]];
    const car: FlowCar = { ...saved, tileTick: saved.tileTick ?? 0, lane, route, model, width, length, blockedBy: null, pose: blankPose(),
      change: loadChange(saved.change), signal: saved.signal && target ? { ...saved.signal, target } : null,
      reservation: saved.reservation ? routes.get(saved.reservation) ?? null : null, uturn: loadUTurn(saved.uturn),
      previous: { ...saved.previous, route: previous, change: loadChange(saved.previous.change), uturn: loadUTurn(saved.previous.uturn) } };
    if (strict && (missing || (saved.reservation && !car.reservation))) return null;
    sampleFlowPose(car, 0, () => 0, car.pose);
    return car;
  }

  /** Multiplayer traffic authority (server/PROTOCOL.md `traffic`): tiles another client owns. Their sources admit no cars
   *  here (their demand counters still run, so an ownership hand-back resumes at the current slot); their cars come
   *  from the owner's snapshots (mirror). */
  setRemote(tiles: Iterable<string>) { this.remote = new Set(tiles); }
  private remote = new Set<string>();

  /** Developer probe: the flow tick, the tiles running remote (mirrored) and every car per tile of its lane as
   *  [key, lane id, s, speed] (multiplayer traffic checks compare clients with it; game/scripts or bots read it through
   *  window.__game.physics.trafficProbe()). */
  probe(): TrafficProbe {
    const tiles: Record<string, [string, string, number, number][]> = {};
    for (const c of this.cars.values()) (tiles[c.lane.tile] ??= []).push([c.key, c.lane.id, round(c.s, 100), round(c.speed, 100)]);
    return { tick: this.tick, live: this.live, remote: [...this.remote].sort(cmp), tiles, stats: { ...this.stats } };
  }

  /** The cars on `tiles` as their owner sends them to the other clients holding those tiles (claimed ones left out). */
  snapshot(tiles: Iterable<string>): TrafficSnapshot {
    const out: Record<string, MirrorCar[]> = {};
    for (const tile of tiles) out[tile] = [];
    for (const c of this.cars.values()) {
      const list = out[c.lane.tile];
      if (list && !this.suppressed.has(c.key)) list.push(packCar(saveCar(c), c.lane.tile, this.tick));
    }
    return { tick: this.tick, tiles: out };
  }

  /** Takes an owner's snapshot of its tiles: this flow's cars on them are replaced by the owner's, each carried on
   *  along its movement at its speed for the ticks since the snapshot (never past the movement's end; the next step
   *  continues it). Kept: a car that reached a snapshot tile after the snapshot was taken (the owner had not seen it
   *  there yet). A car of the snapshot that has since left those tiles here is left alone (it already drives on).
   *  Returns the cars taken over. Nothing happens while the flow warms up or before its graph exists. */
  mirror(snapshot: TrafficSnapshot): number {
    if (!this.ready || this.warming || !this.ids || !numOK(snapshot.tick) || snapshot.tick > this.tick + 50) return 0;
    const ticks = Math.max(0, Math.min(20, this.tick - snapshot.tick));
    const tiles = new Set<string>(), incoming = new Map<string, SavedCar>();
    for (const [tile, list] of Object.entries(snapshot.tiles)) {
      if (!this.network.isEnabled(tile) || !Array.isArray(list)) continue;
      tiles.add(tile);
      for (const packed of list) { const saved = unpackCar(packed, tile, snapshot.tick); if (saved) incoming.set(saved.key, saved); }
    }
    const replaced = new Map<string, FlowCar>();
    for (const car of [...this.cars.values()]) {
      if (!tiles.has(car.lane.tile) || (car.tileTick > snapshot.tick && !incoming.has(car.key))) continue;
      this.unoccupy(car); this.cars.delete(car.key); replaced.set(car.key, car);
    }
    let taken = 0, kept = 0;
    for (const saved of incoming.values()) {
      if (this.suppressed.has(saved.key) || this.cars.has(saved.key)) continue;
      const car = this.loadCar(saved);
      if (!car) continue;
      const route = car.route, end = route.length + (route.connector && route.next ? route.connector.length : 0);
      if (!car.uturn && car.s < end) car.s = Math.min(end - 0.05, car.s + car.speed * ticks * FLOW_STEP);
      // The pose between now and the next step blends from where this client last drew the car.
      const before = replaced.get(car.key);
      // How far the owner's car is from this client's (the correction a viewer sees), by size: < 0.25, 1, 3 m, more.
      if (before) {
        kept++;
        if (before.lane !== car.lane) this.stats.mirrorLane++;
        else { const d = Math.abs(before.s - car.s); this.stats[d < 0.25 ? 'mirrorShift0' : d < 1 ? 'mirrorShift1' : d < 3 ? 'mirrorShift2' : 'mirrorShift3']++; }
      } else this.stats.mirrorAdded++;
      car.previous = before && before.route === car.route ? { ...before.previous }
        : { ...car.previous, s: Math.max(0, car.s - car.speed * FLOW_STEP) };
      sampleFlowPose(car, 0, () => 0, car.pose);
      this.cars.set(car.key, car);
      taken++;
    }
    this.stats.mirrored += taken;
    this.stats.mirrorRemoved += replaced.size - kept;
    return taken;
  }

  suppress(key: string) {
    this.suppressed.add(key);
    const car = this.cars.get(key);
    if (car) { this.unoccupy(car); this.cars.delete(key); }
  }

  /** Clock jumps (ticks) this flow absorbs: the defaults replay every tick exactly and rebuild from the epoch on any
   *  move back (tests, previews); the physics worker sets LIVE_CLOCK_JUMPS. */
  clockJumps: { hold: number; catchUp: number } = { hold: 0, catchUp: Infinity };

  /** Advances the fixed ticks to `t`. The first update warms up from the epoch (a minute boundary 90-150 s before
   *  `t` unless the constructor pinned one); no random wall-time seed. A generation-pinned multiplayer world can call
   *  reset(epoch) once and advance the same fixed ticks. */
  update(t: number) {
    const target = Math.floor((t + 1e-7) / FLOW_STEP);
    if (this.ready && (this.job || this.revision !== this.network.revision) && target >= this.tick) this.advanceRebuild();
    if (this.ready && target < this.tick) {
      if (target >= this.tick - this.clockJumps.hold) return;
      // A live clock moved back further than any correction: warm up again just before it, not from the first epoch.
      if (this.clockJumps.hold > 0) this.epoch = Math.floor(t / 60) * 60 - 90;
      this.ready = false;
    }
    if (!this.ready) {
      this.epoch ??= Math.floor(t / 60) * 60 - 90;
      this.job = null;
      this.reset(this.epoch);
      this.warming = true;
    } else if (!this.warming && target - this.tick > this.clockJumps.catchUp) this.skip(target - 1);
    if (this.warming) this.warmUp(target, this.warmUpBudgetMs);
    else while (this.tick < target) this.step();
  }

  /** Wall time (ms) one update may spend on the warm-up replay from the epoch (at least one tick); the cars are
   *  not drawn until it reaches the clock (`warmingUp`). Infinity replays within the update that starts it. The
   *  physics worker sets a few ms: replaying 90-150 s at once held its first step 3.5 s on the M1 Air. */
  warmUpBudgetMs = Infinity;
  private warming = false;
  /** The warm-up replay has not reached the clock yet: the flow's cars are behind it. */
  get warmingUp() { return this.warming; }
  /** Warmed up to the clock: its cars are the ones drawn. */
  get live() { return this.ready && !this.warming; }

  /** Idle worker time (loading, the menu): goes on with a warm-up already started, for at most `budgetMs`. */
  idle(t: number, budgetMs: number) {
    if (this.warming && this.ready) this.warmUp(Math.floor((t + 1e-7) / FLOW_STEP), budgetMs);
  }

  private warmUp(target: number, budgetMs: number) {
    const end = performance.now() + budgetMs;
    do { if (this.tick < target) this.step(); } while (this.tick < target && performance.now() < end);
    if (this.tick >= target) this.warming = false;
  }

  /** Moves the clock to `tick` without simulating the gap: the cars go on from where they are, and each source
   *  resumes at its current slot rather than admitting every car of the gap at once. */
  private skip(tick: number) {
    this.tick = tick;
    const t = tick * FLOW_STEP;
    for (const lane of this.sources) this.demand.set(lane, Math.max(this.demand.get(lane) ?? 0, Math.ceil((t - lane.phase) / TRAFFIC.headway)));
  }

  /** Wall time (ms) one update may spend rebuilding the graph after a tile change; the live graph keeps running
   *  until the new one is complete (the physics worker sets a few ms). Infinity rebuilds within the update that
   *  sees the change. */
  rebuildBudgetMs = Infinity;
  private job: { revision: number; gen: Generator<void, () => void> } | null = null;
  private advanceRebuild() {
    // A tile change during a build does not restart it (streaming could starve it): the next build picks it up.
    this.job ??= { revision: this.network.revision, gen: this.buildGraph() };
    const end = performance.now() + this.rebuildBudgetMs;
    for (;;) {
      const r = this.job.gen.next();
      if (r.done) { const revision = this.job.revision; this.job = null; this.refreshGraph(r.value, revision); return; }
      if (performance.now() >= end) return;
    }
  }

  /** Streaming is a tick-stamped graph event. Keep existing drivers instead of replaying an ever-growing
   * session every time one neighbour loads. A shared-world host publishes the same event/checkpoint to peers. */
  private refreshGraph(commit: () => void, revision: number) {
    const existing = [...this.cars.values()], demand = new Map([...this.demand].map(([l, n]) => [`${l.tile}:${l.id}`, n]));
    commit();
    this.revision = revision;
    this.clearState();
    const { laneById: lanes, routeById: routes } = this.ids!;
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
      if (car.uturn) { const to = lanes.get(`${car.uturn.to.tile}:${car.uturn.to.id}`); car.uturn = to ? { ...car.uturn, to } : null; }
      sampleFlowPose(car, 0, () => 0, car.pose); this.cars.set(car.key, car);
    }
    const t = this.tick * FLOW_STEP;
    for (const lane of this.sources) this.demand.set(lane, demand.get(`${lane.tile}:${lane.id}`) ?? Math.ceil((t - lane.phase) / TRAFFIC.headway));
  }

  private clearState() {
    this.cars.clear(); this.demand.clear();
    this.stats = { entered: 0, exited: 0, blocked: 0, resets: this.stats.resets + 1, ticks: 0, laneChanges: 0, unlocks: 0, cleared: 0, recycled: 0, escapes: 0, reverses: 0, rerouted: 0, uturns: 0, reroutedAhead: 0, mirrored: 0,
    mirrorAdded: 0, mirrorRemoved: 0, mirrorLane: 0, mirrorShift0: 0, mirrorShift1: 0, mirrorShift2: 0, mirrorShift3: 0 };
  }

  reset(t: number) {
    this.epoch ??= t;
    const rebuild = this.revision !== this.network.revision;
    this.tick = Math.floor(t / FLOW_STEP); this.ready = true; this.revision = this.network.revision;
    this.clearState();
    if (!rebuild) {
      for (const lane of this.sources) this.demand.set(lane, Math.ceil((t - lane.phase) / TRAFFIC.headway));
      return;
    }
    const job = this.buildGraph();
    let r = job.next();
    while (!r.done) r = job.next();
    r.value();
    for (const lane of this.sources) this.demand.set(lane, Math.ceil((t - lane.phase) / TRAFFIC.headway));
  }

  /** The graph for the network's current tiles, built in slices (each `yield` is a safe pause) without touching the
   *  live graph; the returned commit installs it. */
  private *buildGraph(): Generator<void, () => void> {
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
    const graph = new Map<Lane, Lane[]>(), baseOf = new Map<Lane, Lane>();
    const lanes = enabled.flatMap(tile => {
      let sorted = this.sortedLanes.get(tile);
      if (!sorted) { sorted = [...tile.lanes].sort((a, b) => cmp(a.id, b.id)); this.sortedLanes.set(tile, sorted); }
      return sorted;
    });
    // Lanes leaving each junction node, in canonical lane order (one pass instead of a scan per lane).
    const startsAt = new Map<string, Lane[]>();
    for (const b of lanes) {
      if (b.start.kind !== 'junction') continue;
      const key = `${b.tile}|${b.start.node}`, list = startsAt.get(key);
      if (list) list.push(b); else startsAt.set(key, [b]);
    }
    const movements: Lane[] = [];
    const kept = new Set<Lane>();
    let work = 0;
    for (const lane of lanes) {
      if ((++work & 31) === 0) yield;
      const reuse = keep(lane) ? previousMovements.get(lane) : undefined;
      if (reuse) {
        if (reuse[0] !== lane) for (const route of reuse) { movements.push(route); baseOf.set(route, lane); kept.add(route); }
        graph.set(lane, reuse);
        if (this.seamBound.has(lane)) seamBound.add(lane);
        continue;
      }
      if (lane.end.kind === 'seam') seamBound.add(lane);
      const seamNext = lane.end.kind === 'seam' ? this.network.successor(lane) : null;
      if (lane.end.kind === 'seam' && (!seamNext || seamNext.length >= 12)) { graph.set(lane, [lane]); continue; }
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
          // Straightest on, but not into a cul-de-sac while the junction has another way.
          const onward = candidates.filter(b => !visited.has(b)).sort((a, b) => {
            const ca = Math.abs(this.link(next, a).turn), cb = Math.abs(this.link(next, b).turn);
            return Number(deadEnd(a)) - Number(deadEnd(b)) || ca - cb || Math.abs(a.k - next.k) - Math.abs(b.k - next.k) || cmp(a.id, b.id);
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
        // A longer fold keeps its path, cleaned like a lane (its links' kinks set the 1.5 m/s floor speed before).
        else if (visited.size > 1) curve = { ...smoothPath(curve.points, Math.min(lane.speed, next.speed, Math.abs(curve.turn) > 0.5 ? 7 : Infinity)), turn: curve.turn };
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
      for (const route of unique) { movements.push(route); baseOf.set(route, lane); }
      graph.set(lane, unique.length ? unique : [lane]);
    }
    // Conflicts depend only on the two movements' geometry. A rebuild only indexes the movements (a grid of their
    // bounds); each movement's set is built when a car first needs it at a junction (conflictsOf), from last build's
    // answer for two kept movements or a pair cache kept across rebuilds (keyed by both ends' tile road hashes). A
    // tile install therefore costs nothing here, and the sets are the same whenever they are built.
    const keyOf = new Map<Lane, string>(), grid = new Map<number, number[]>(), order = new Map<Lane, number>();
    const hashOf = (tile: string) => this.network.tiles.get(tile)?.hash ?? '';
    for (let i = 0; i < movements.length; i++) {
      const m = movements[i];
      if ((i & 63) === 63) yield;
      if (!m.connector) continue;
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
    }
    yield;
    const changer = new LaneChangePlanner(lanes, graph);
    yield;
    // Demand enters at directed graph boundaries. Conveyor matching cycles are not additional portals:
    // injecting cars on every such loop accumulated traffic in cul-de-sacs and invented mid-block sources.
    const inbound = new Set<Lane>();
    for (const [lane, routes] of graph) for (const route of routes) {
      const next = route === lane && lane.end.kind === 'seam' ? this.network.successor(lane) : route.next;
      if (next) inbound.add(next);
    }
    const sources = lanes.filter(l => l.length >= 12 && !inbound.has(l));
    yield;
    const laneById = new Map<string, Lane>(), routeById = new Map<string, Lane>();
    for (let i = 0; i < lanes.length; i++) { if ((i & 255) === 255) yield; const l = lanes[i]; laneById.set(`${l.tile}:${l.id}`, l); }
    let n = 0;
    for (const routes of graph.values()) { if ((++n & 255) === 0) yield; for (const r of routes) routeById.set(`${r.tile}:${r.id}`, r); }
    return () => {
      this.ids = { laneById, routeById }; this.fronts.clear();
      if (this.conflictCache.size > 400_000) this.conflictCache.clear();
      this.movements = graph; this.conflicts = new Map(); this.baseOf = baseOf; this.builtTiles = tileKeys; this.lanes = lanes;
      this.index = { movements, order, grid, keyOf, kept, previous: previousConflicts, samples: new Map(), seen: new Uint32Array(movements.length), stamp: 0 };
      this.seamBound = seamBound; this.changer = changer; this.sources = sources;
    };
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
        swerve: car.swerve, swerveRate: car.swerveRate, uturn: car.uturn ? { ...car.uturn } : null };
      sampleFlowPose(car, 0, () => 0, car.pose); this.occupy(car);
    }
    // Demand is tested on its original shared slot hour. A blocked source queues outside the resident area;
    // the next free slot admits a car, rather than placing another body inside an existing queue.
    for (const lane of this.sources) {
      let n = this.demand.get(lane)!;
      while (entryTime(lane, n) <= t + 1e-7) {
        if (!this.remote.has(lane.tile) && this.network.present(lane, n)) this.spawn(lane, n, t);
        n++;
      }
      this.demand.set(lane, n);
    }
    const heads = new Map<Lane, FlowCar>();
    for (const [lane, list] of this.byLane) {
      const front = list.find(c => c.s <= lane.length);
      if (front) heads.set(lane, front);
    }
    // Per receiving lane, the head that has waited longest (over STARVE_AFTER) for it: it gets the next space there.
    this.longest.clear();
    for (const head of heads.values()) {
      const next = head.route.next;
      if (!next || head.waiting <= STARVE_AFTER) continue;
      const best = this.longest.get(next);
      if (!best || head.waiting > best.waiting || head.waiting === best.waiting && head.key < best.key) this.longest.set(next, head);
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
        const turnSpeed = lane.connector!.speed, limits = lane.connector!.limits;
        if (limits && car.s > lane.length) desired = Math.min(desired, limits[Math.min(limits.length - 1, Math.floor(car.s - lane.length))]);
        if (!limits || car.s <= lane.length) desired = Math.min(desired, Math.sqrt(turnSpeed * turnSpeed + 2 * BRAKE * Math.max(0, remaining - car.length / 2)));
        if (!car.change && !car.reservation && remaining < REROUTE_AHEAD && remaining > 0.5) this.reroute(car, t, true);
        if (!car.change && !car.reservation && remaining < Math.max(30, car.speed * car.speed / (2 * BRAKE) + 8)) {
          if (this.mayEnter(car, heads, reservations, t)) {
            // Evaluate the gap while braking, but only lock the box when the nose is about to enter it.
            // Reserving thirty metres upstream blocked nearby junctions for the whole approach.
            if (remaining <= car.length / 2 + 1.2 + car.speed * FLOW_STEP) {
              car.reservation = lane; reservations.push(car);
            }
          } else {
            stop = Math.min(lane.length - 0.8, this.safeFront(car.lane)) - car.length / 2;
            this.reroute(car, t);
          }
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
        .filter(c => c !== car && position(c) > car.s && (c.route === car.route || position(c) < lane.length + c.length / 2 + GAP)
          // Going round it: a car the swerve already clears sideways is not followed (the guard still checks contact).
          && !(car.swerveTarget && c.s < car.swerveEnd && Math.abs(car.swerve - c.swerve) >= (car.width + c.width) / 2 + 0.3))
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
      // Held up long enough behind an obstacle: go round it when the corridor beside it is clear.
      const patience = PASS_AFTER * (0.8 + 0.8 * hash01('honk-patience', car.key));
      let rocking = false;
      if (!car.change && !car.swerveTarget && !car.swerve && !car.uturn) {
        let obstacles: Body[] | null = null;
        const parked = byPlayer && blocker && Math.hypot(blocker.vx, blocker.vz) < 0.5;
        if (parked && (car.speed > 1 || car.held > patience)) {
          // A stopped player's vehicle: moving drivers go round it at once when they can (no stop first).
          obstacles = [blocker!];
        } else if (byPlayer && car.held > patience) {
          // A player standing in the lane (a walker that does not move on).
          if (!blocker && car.held > 1.5 * patience) obstacles = this.standingPlayers(car, lane);
        } else if (leader && car.stopped > patience && leader.stopped > patience && car.s < lane.length - PASS_CLEAR_LINE) {
          // A queue that is not a queue: the car ahead stands mid-block because a player's car or a footprint lock
          // holds it. Go round the car and what holds it.
          obstacles = this.stalled(leader, lane);
        }
        if (obstacles?.length && !car.reverse) {
          // Out of a blocked lane into the free one beside it first; else round the obstacle in this lane.
          const escape = this.changer?.escape(car, this.tick, this.byLane) ?? null;
          const plan = escape ? null : this.passPlan(car, lane, obstacles);
          const patient = hash01('honk-patience', car.key);
          if ((escape instanceof Object || plan) && stop - car.s < 0.5 && car.backed < REVERSE_MAX && this.roomBehind(car)) {
            // Too close to pull out: back up a little first (guarded, as an unlock).
            car.reverse = Math.min(REVERSE_MAX - car.backed, REVERSE_BACK * (1 + patient)); this.stats.reverses++;
          } else if (escape instanceof Object) {
            this.beginChange(car, escape); car.change!.escape = true; this.stats.escapes++;
          } else if (plan) { car.swerveTarget = plan.offset; car.swerveEnd = plan.end; car.swerveStall = 0; }
          // Indicating: wait where it is rather than creeping back up to the obstacle.
          else if (escape === 'signal') stop = Math.min(stop, car.s);
          else if (!escape) {
            // No way past: the front car backs off once and stays there; after a long wait it turns round if it can.
            const wait = byPlayer ? car.held : car.stopped;
            if (stop - car.s < 0.5 && wait > BLOCKED_AFTER * (1 + patient) && car.backed < 0.5 && this.roomBehind(car)) {
              car.reverse = Math.min(REVERSE_MAX, BLOCKED_BACK * (1 + patient)); this.stats.reverses++;
            } else if (wait > UTURN_AFTER * (1 + patient)) this.beginUTurn(car, Math.max(0, stop - car.s) + 1.5);
            if (car.backed > 0.5 && !car.uturn) {
              rocking = true;
              if (car.rock < 0 && !car.reverse) car.rock = car.s;
              if (car.rock >= 0) {
                const phase = Math.floor((this.tick + hash32('rock', car.key) % (2 * ROCK_HALF)) / ROCK_HALF) % 2;
                if (phase === 0) stop = Math.min(stop, car.rock + ROCK_STEP);
                else { stop = Math.min(stop, car.s); if (car.s > car.rock + 0.05 && !car.reverse) car.reverse = car.s - car.rock; }
              }
            }
          }
        }
      }
      if (!rocking) car.rock = -1;
      if (car.reverse || car.uturn) stop = car.s;
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
        const progress = car.change.progress, rate0 = car.change.rate;
        // Held at the change's stop point (8 m before the line) with the move unfinished: finish it at a creep.
        // Progress tied to speed alone never completed there, and the car waited for good.
        const held = car.s > lane.length - 9 || !!car.change.escape;
        car.change.rate = Math.max(Math.min(1, car.speed / 3), held ? CHANGE_CREEP : 0) / car.change.duration;
        car.change.progress = Math.min(1, progress + FLOW_STEP * car.change.rate);
        // Stalled half-way by another car's body for UNLOCK_AFTER s: give the change up and ease back into the old lane
        // (two drivers aiming for the same gap otherwise hold each other for good).
        if (car.blockedBy?.startsWith('footprint:') && car.stopped > UNLOCK_AFTER && progress < 0.5) {
          car.change.progress = Math.max(0, progress - FLOW_STEP * CHANGE_CREEP / car.change.duration);
          if (car.change.progress === 0) this.abandonChange(car);
        }
        // A blocked move keeps last tick's progress and rate (the rate turns the nose), i.e. last tick's checked pose.
        const changed = sampleFlowPose(car, 0, () => 0);
        if (this.obstructed(car, changed) || this.touchesPlayer(car, changed)) { car.change.progress = progress; car.change.rate = rate0; }
      }
      if (car.uturn) this.advanceUTurn(car);
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
      if (car.reverse > 0) this.backUp(car);
      // The pose this car ends the tick in must not touch another body (a heading that changes with speed, e.g. a
      // pass stopped short beside oncoming traffic, can turn corners the guard did not see): keep last tick's state.
      const startLane = car.previous.route === car.route || car.previous.route.next === car.route.next;
      const ends = sampleFlowPose(car, 0, () => 0);
      if (startLane && !car.uturn?.progress && (this.touches(car, ends) || this.touchesPlayer(car, ends))) {
        const p = car.previous;
        // Exactly last tick's pose (others checked against it); speed and swerve rate set the nose, so they stay too.
        car.s = p.s; car.speed = p.speed; car.swerve = p.swerve; car.swerveRate = p.swerveRate;
        if (car.change && p.change) { car.change.progress = p.change.progress; car.change.rate = p.change.rate; }
        step = 0;
      }
      if (car.uturn?.progress === 1) this.finishUTurn(car);
      if (step < 0.01 && car.blockedBy?.startsWith('footprint:')) {
        const other = this.cars.get(car.blockedBy.slice(10));
        this.makeRoom(car, lane, other);
        if (other) this.unlock(car, other);
      }
      if (step > 0.01 && !car.reverse && car.rock < 0) car.backed = 0;
      car.progress += Math.max(0, step);
      if (car.progress >= STUCK_PROGRESS) { car.progress = 0; car.stuck = 0; car.waitsFor = null; } else car.stuck += FLOW_STEP;
      const named = car.blockedBy ? car.blockedBy.indexOf(':') : -1;
      if (named >= 0) car.waitsFor = car.blockedBy!.slice(named + 1);
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
          if (next.tile !== car.lane.tile) car.tileTick = this.tick;
          car.s -= end; car.lane = next; car.hops++; car.route = this.chooseRoute(next, car.key, car.hops);
          const list = this.byLane.get(next);
          if (list) { list.push(car); list.sort((a, b) => b.s - a.s); } else this.byLane.set(next, [car]);
        }
      }
      this.unoccupy(car);
      sampleFlowPose(car, 0, () => 0, car.pose);
      if (this.cars.has(car.key)) this.occupy(car);
    }
    if (this.tick % 10 === 0) this.clearDeadlocks();
    if (this.tick % 10 === 5) {
      if (this.schedule) this.watchers = this.schedule.filter(w => w.from <= this.tick && this.tick < w.until).at(-1)?.watchers ?? null;
      this.recycle(t);
    }
  }

  /** Out-of-view recycling (RECYCLE_HEAD / RECYCLE_ANY): the front of every jammed queue, then any long-stuck car,
   *  leaves the flow when no watcher sees it. Canonical key order and pure flow state plus the watcher list, so peers
   *  that share the list agree. */
  private recycle(t: number) {
    const watchers = this.watchers;
    if (!watchers) return;
    const far = (c: FlowCar) => watchDistance(watchers, c.pose.x, c.pose.z) > FAST_FAR;
    // Seconds a car has stood while it had a way to take: held at its line, only while its light was green.
    const jammed = (c: FlowCar) => c.blockedBy && AT_LINE.test(c.blockedBy) ? greenWithin(c.route, t, c.stuck) : c.stuck;
    // A queue's front holds it: it names what stops it (not a red light), or it has backed off an obstacle or a player
    // holds it up (it stands clear of what blocks it, so it names nothing).
    const holds = (c: FlowCar) => c.blockedBy ? c.blockedBy !== 'signal' : c.backed > 0 || c.reverse > 0 || c.held > 0;
    const stuck = [...this.cars.values()].filter(c => c.speed < STANDING && c.stuck >= Math.min(FAST_HEAD, RECYCLE_HEAD))
      .sort((a, b) => cmp(a.key, b.key));
    const heads = new Set<FlowCar>();
    for (const car of stuck) {
      // Up the standing queue (what each car waits on now) to its front. A queue that moves anywhere, or whose front
      // does not hold it (free to go, or waiting for green), is not jammed.
      let at: FlowCar | null = car;
      const seen = new Set<FlowCar>([car]);
      for (let hop = 0; at && hop < RECYCLE_CHAIN; hop++) {
        const next = this.blocker(at);
        if (!next || seen.has(next)) break;
        if (next.speed >= STANDING) at = null;
        else { seen.add(next); at = next; }
      }
      if (at && holds(at) && jammed(at) >= (far(at) ? FAST_HEAD : RECYCLE_HEAD)) heads.add(at);
    }
    for (const car of stuck) if (car.stuck >= (far(car) ? FAST_ANY : RECYCLE_ANY)) heads.add(car);
    for (const car of [...heads].sort((a, b) => cmp(a.key, b.key))) {
      if (!this.cars.has(car.key) || !unseen(watchers, car.pose.x, car.pose.z)) continue;
      this.unoccupy(car); this.cars.delete(car.key); this.stats.recycled++;
      if (this.recycledLog.length < 10_000) this.recycledLog.push({ key: car.key, x: car.pose.x, z: car.pose.z, t });
    }
  }


  /** Per approach lane: how far along it (m) a waiting car's front may stand without reaching into the path of any
   *  movement from another approach through the junction (STOP_CLEAR). Cleared with each graph build. */
  private fronts = new Map<Lane, number>();
  /** Where a car waiting at this approach's line may stand (its front, m along the lane): clear of crossing paths. */
  stopFront(approach: Lane): number { return this.safeFront(approach); }
  private safeFront(approach: Lane): number {
    let front = this.fronts.get(approach);
    if (front !== undefined) return front;
    const line = approach.length - 0.8, others: PathSamples[] = [];
    const y = dataHeight(approach, approach.length);
    for (const route of this.movements.get(approach) ?? []) {
      if (!route.connector) continue;
      for (const m of this.conflictsOf(route)) {
        const base = this.baseOf.get(m) ?? m;
        if (base === approach || base.tile === approach.tile && base.end.node === approach.end.node) continue;
        if (Math.abs(dataHeight(m, m.length) - y) > LEVEL_APART) continue;
        let samples = this.index?.samples.get(m);
        if (!samples) { samples = pathSamples(m); this.index?.samples.set(m, samples); }
        others.push(samples);
      }
    }
    front = line;
    for (let back = 0; back <= STOP_CLEAR_MAX && others.length; back += 0.5) {
      const p = routePoint(approach, line - back - CONFLICT_BODY.length / 2);
      const body = { x: p.x, z: p.z, hx: p.hx, hz: p.hz };
      // Only the other movement's part inside the junction (its own approach is not a crossing path).
      const hit = others.some(o => { for (let k = 6; k < o.x.length; k++) {
        poseB.x = o.x[k]; poseB.z = o.z[k]; poseB.hx = o.hx[k]; poseB.hz = o.hz[k];
        if (bodyOverlap(body, CONFLICT_BODY, poseB, CONFLICT_BODY, STOP_CLEAR)) return true;
      } return false; });
      if (!hit) { front = line - back; break; }
      if (back + 0.5 > STOP_CLEAR_MAX) front = line;
    }
    this.fronts.set(approach, front);
    return front;
  }
  /** The current graph's lanes and movements by `tile:id` (checkpoints, graph refreshes). */
  private ids: { laneById: Map<string, Lane>; routeById: Map<string, Lane> } | null = null;

  /** The key a car waits on (any blockedBy that names one), for deadlock cycles. */
  private waitsOn(car: FlowCar) { return car.waitsFor ? this.cars.get(car.waitsFor) ?? null : null; }
  /** The car a car waits on this tick (a blockedBy that names one), or null. */
  private blocker(car: FlowCar) {
    const by = car.blockedBy, at = by ? by.indexOf(':') : -1;
    return at < 0 ? null : this.cars.get(by!.slice(at + 1)) ?? null;
  }
  private clearDeadlocks() {
    const stuck = [...this.cars.values()].filter(c => c.stuck >= CLEAR_AFTER).sort((a, b) => cmp(a.key, b.key));
    for (const car of stuck) {
      if (!this.cars.has(car.key)) continue;
      let at = this.waitsOn(car), lowest = car.key;
      for (let hop = 0; at && at !== car && hop < CLEAR_CYCLE; hop++) {
        if (at.stuck < CLEAR_AFTER) { at = null; break; }
        if (at.key < lowest) lowest = at.key;
        at = this.waitsOn(at);
      }
      if (at !== car || lowest !== car.key) continue;
      this.unoccupy(car); this.cars.delete(car.key); this.stats.cleared++;
    }
  }

  /** Lateral offset that clears a stopped vehicle, and the arc length to steer back at; null when the pass would
   *  leave the lane by more than PASS_MAX, run into the junction, or meet another car in the corridor. Prefers the
   *  left (towards oncoming traffic) on a tie. */
  private passPlan(car: FlowCar, lane: Lane, obstacles: readonly Body[]): PassPlan | null {
    return planPass({ s: car.s, length: car.length, width: car.width }, s => routePoint(lane, s), lane.length, obstacles,
      this.cars.values(), c => c === car);
  }

  /** The opposite lane of the same street (same tile, ends swapped), innermost first, or null. */
  private opposite(lane: Lane): Lane | null {
    return this.lanes.filter(l => l.tile === lane.tile && l.start.node === lane.end.node && l.end.node === lane.start.node)
      .sort((a, b) => a.k - b.k || cmp(a.id, b.id))[0] ?? null;
  }

  /** Starts a U-turn onto the opposite lane at the mirrored point when that lane is clear around it (no car within
   *  UTURN_CLEAR m, and nothing arriving within UTURN_SECONDS). */
  private beginUTurn(car: FlowCar, room: number) {
    if (car.uturn || car.change || car.swerve || car.reservation) return;
    const to = this.opposite(car.lane);
    if (!to) return;
    const at = to.length - car.s / car.lane.length * to.length;
    if (at < 8 || at > to.length - 15) return;
    for (const other of this.byLane.get(to) ?? []) {
      const ahead = other.s - at, approaching = at - other.s;
      if (Math.abs(ahead) < UTURN_CLEAR || approaching > 0 && approaching < UTURN_CLEAR + other.speed * UTURN_SECONDS) return;
    }
    car.uturn = { to, at, progress: 0, reach: Math.max(1.5, Math.min(6, room)) }; car.signal = null;
  }

  /** Moves a U-turn on (guarded; a blocked step waits) and, once done, puts the car on the opposite lane. */
  private advanceUTurn(car: FlowCar) {
    const u = car.uturn!, before = u.progress;
    u.progress = Math.min(1, before + FLOW_STEP / UTURN_SECONDS);
    const pose = sampleFlowPose(car, 0, () => 0);
    if (this.obstructed(car, pose) || this.touchesPlayer(car, pose)) { u.progress = before; return; }
    car.speed = 0;
  }

  /** A finished U-turn: the car continues on the opposite lane. */
  private finishUTurn(car: FlowCar) {
    const u = car.uturn!;
    const list = this.byLane.get(car.lane);
    if (list) { const i = list.indexOf(car); if (i >= 0) list.splice(i, 1); }
    if (u.to.tile !== car.lane.tile) car.tileTick = this.tick;
    car.lane = u.to; car.s = u.at; car.hops++; car.route = this.chooseRoute(u.to, car.key, car.hops);
    car.uturn = null; car.held = 0; car.backed = 0; car.changeCooldown = this.tick + 100; this.stats.uturns++;
    const to = this.byLane.get(u.to);
    if (to) { to.push(car); to.sort((a, b) => b.s - a.s); } else this.byLane.set(u.to, [car]);
  }

  /** Real contact (no margin) with another car's body on the same level. */
  private touches(car: FlowCar, pose: CarPose) {
    const x = Math.floor(pose.x / 8), z = Math.floor(pose.z / 8);
    for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) {
      for (const other of this.occupancy.get(`${x + i},${z + j}`) ?? []) {
        if (car !== other && bodyOverlap(pose, car, other.pose, other, 0) && !this.levelApart(car, other)) return true;
      }
    }
    return false;
  }

  /** Whether the car can back up a little (its reversing guard is clear 0.3 m back). */
  private roomBehind(car: FlowCar) {
    if (car.s < 0.3 || car.change) return false;
    const s = car.s, blocked = car.blockedBy;
    car.s = s - 0.3;
    const clear = !this.reverseObstructed(car, sampleFlowPose(car, 0, () => 0));
    car.s = s; car.blockedBy = blocked;
    return clear;
  }

  /** A lane change given up at its start: the car is back on the lane it came from. */
  private abandonChange(car: FlowCar) {
    const change = car.change!, from = change.from, target = car.lane;
    const list = this.byLane.get(target);
    if (list) { const i = list.indexOf(car); if (i >= 0) list.splice(i, 1); }
    const departing = this.departing.get(from);
    if (departing) { const i = departing.indexOf(car); if (i >= 0) departing.splice(i, 1); }
    if (from.tile !== car.lane.tile) car.tileTick = this.tick;
    car.s = car.s / target.length * from.length; car.lane = from; car.route = this.chooseRoute(from, car.key, car.hops);
    car.change = null; car.signal = null; car.changeCooldown = this.tick + 100;
    const back = this.byLane.get(from);
    if (back) { back.push(car); back.sort((a, b) => b.s - a.s); } else this.byLane.set(from, [car]);
  }

  /** Backs up along the route at REVERSE_SPEED (the reversing guard: never into a body); stops when done or blocked. */
  private backUp(car: FlowCar) {
    const back = Math.min(REVERSE_SPEED * FLOW_STEP, car.reverse, car.s), s = car.s;
    car.s = s - back;
    const pose = sampleFlowPose(car, 0, () => 0);
    if (back <= 0 || this.reverseObstructed(car, pose)) { car.s = s; car.reverse = 0; return; }
    car.reverse -= back; car.backed += back; car.progress -= back; car.speed = 0;
    if (car.reverse < 1e-6) car.reverse = 0;
  }

  /** A junction head kept at its line by a full exit (exit / storage / receiving) changes its mind: another movement
   *  this lane may take whose receiving lane has room for it. Same lane geometry up to the line, so nothing jumps. */
  private reroute(car: FlowCar, t: number, ahead = false) {
    const by = car.blockedBy ?? '';
    if (car.reservation || car.change || car.s > car.lane.length - 0.5 || this.tick - car.rerouted < REROUTE_GAP) return;
    const room = (next: Lane) => {
      const tail = this.byLane.get(next)?.at(-1);
      return tail ? tail.s - tail.length / 2 : next.length;
    };
    const need = car.length + 2 * GAP + 2;
    if (ahead) {
      // Seen ahead: the exit's last car has stood still a while and there is no room behind it.
      const next = car.route.next, tail = next ? this.byLane.get(next)?.at(-1) : undefined;
      if (!next || !tail || tail.stuck < JAM_AHEAD || tail.speed > 0.5 || room(next) >= need) return;
    } else {
      if (!/^(exit|storage|receiving)/.test(by)) return;
      if (car.waiting < REROUTE_AFTER * PASS_AFTER * (0.8 + 0.8 * hash01('honk-patience', car.key))) return;
    }
    const options = (this.movements.get(car.lane) ?? []).filter(m => m !== car.route && m.connector && m.next
      && room(m.next) >= need && signalState(m, t) !== 'red')
      .sort((a, b) => Number(intoDeadEnd(a)) - Number(intoDeadEnd(b)) || room(b.next!) - room(a.next!) || cmp(a.id, b.id));
    if (!options.length) return;
    car.route = options[0]; car.rerouted = this.tick; this.stats.rerouted++; if (ahead) this.stats.reroutedAhead++;
  }

  /** Players on foot standing in the car's path just ahead (they do not move on), as small bodies. */
  private standingPlayers(car: FlowCar, lane: Lane): Body[] {
    const out: Body[] = [], here = routePoint(lane, car.s);
    for (const p of this.players?.people ?? []) {
      if (Math.hypot(p.vx, p.vz) > 0.3) continue;
      const dx = p.x - here.x, dz = p.z - here.z, along = dx * here.hx + dz * here.hz, across = -dx * here.hz + dz * here.hx;
      if (along > 0 && along < car.length / 2 + 12 && Math.abs(across) < car.width / 2 + 1.2)
        out.push({ x: p.x, z: p.z, hx: here.hx, hz: here.hz, length: PERSON_BODY, width: PERSON_BODY });
    }
    return out;
  }

  /** The stopped car ahead and what holds it mid-block (a player's vehicle or person in its path, or the car whose
   *  footprint locks it), or null when it waits in an ordinary queue or at a junction. */
  private stalled(leader: FlowCar, lane: Lane): Body[] | null {
    const by = leader.blockedBy;
    if (leader.lane !== lane || leader.s > lane.length - PASS_CLEAR_LINE || !by) return null;
    const body = (c: FlowCar): Body => ({ x: c.pose.x, z: c.pose.z, hx: c.pose.hx, hz: c.pose.hz, length: c.length, width: c.width, car: c });
    if (by === 'player') {
      const ahead = routePoint(lane, leader.s), out = [body(leader)];
      for (const v of this.playerVehicles?.vehicles ?? []) {
        const along = (v.x - ahead.x) * ahead.hx + (v.z - ahead.z) * ahead.hz;
        if (along > 0 && along < leader.length / 2 + v.length / 2 + 6 && Math.hypot(v.vx, v.vz) < 0.5) out.push(v);
      }
      out.push(...this.standingPlayers(leader, lane));
      return out.length > 1 ? out : null;
    }
    if (by.startsWith('footprint:') && leader.stuck > PASS_LOCKED) {
      const other = this.cars.get(by.slice(10));
      return other ? [body(leader), body(other)] : null;
    }
    return null;
  }

  private spawn(lane: Lane, n: number, t: number) {
    const key = `${lane.tile}:${lane.id}:${n}`;
    if (this.suppressed.has(key)) return;
    const model = pick(hash01('model', key), MODEL_WEIGHTS);
    const [width, , length] = MODEL_SIZE[TRAFFIC_MODELS[model]];
    const tail = this.byLane.get(lane)?.at(-1);
    if (tail && tail.s < (length + tail.length) / 2 + GAP) { this.stats.blocked++; return; }
    const route = this.chooseRoute(lane, key);
    const car: FlowCar = { key, lane, route, n, model, width, length, born: t, s: 0, speed: 0, hops: 0, tileTick: this.tick,
      demandDraw: this.network.demandDraw(lane, n),
      previous: { route, s: 0, speed: 0, change: null, swerve: 0, swerveRate: 0, uturn: null }, change: null, uturn: null, signal: null, changeCooldown: 0,
      reservation: null, stopped: 0, waiting: 0, backed: 0, stuck: 0, progress: 0, waitsFor: null, held: 0, reverse: 0, rerouted: 0, rock: -1, swerve: 0, swerveTarget: 0, swerveRate: 0, swerveEnd: 0, swerveStall: 0,
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
    const base = routes.map(r => (!r.connector || Math.abs(r.connector.turn) < 0.5 ? 0.65 : r.connector.turn > 0 ? 0.22 : 0.13)
      * Math.min(1, (r.next?.flow ?? lane.flow) / lane.flow));
    // Never into a cul-de-sac while another way exists.
    const open = base.map((w, i) => intoDeadEnd(routes[i]) ? 0 : w);
    const weights = open.some(w => w > 0) ? open : base;
    let value = hash01('route', seed, lane.tile, lane.id) * weights.reduce((a, b) => a + b, 0);
    for (let i = 0; i < routes.length; i++) { value -= weights[i]; if (value <= 0) return routes[i]; }
    return routes[routes.length - 1];
  }

  private beginChange(car: FlowCar, target: Lane) {
    const from = car.lane, list = this.byLane.get(from), direction = target.k > from.k ? 1 : -1;
    if (list) { const i = list.indexOf(car); if (i >= 0) list.splice(i, 1); }
    car.change = { from, direction, progress: 0, duration: LANE_CHANGE_SECONDS, rate: 0 };
    if (target.tile !== car.lane.tile) car.tileTick = this.tick;
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
        if (bodyOverlap(pose, car, other.pose, other, this.follows(other, car, pose) ? FOLLOWER_MARGIN : GUARD_MARGIN)
          && !this.levelApart(car, other)) {
          car.blockedBy = `footprint:${other.key}`; return true;
        }
      }
    }
    return false;
  }

  /** On different levels (a bridge or ramp over a road, by the data's surface heights): bodies never meet. */
  levelApart(a: FlowCar, b: FlowCar) {
    const ya = dataHeight(a.route, a.s), yb = dataHeight(b.route, b.s);
    return Math.abs(ya - yb) > LEVEL_APART;
  }

  /** Whether `other` drives behind `car` in the same direction: its centre behind car's rear and headings within 60°. */
  private follows(other: FlowCar, car: FlowCar, pose: CarPose) {
    const o = other.pose;
    if (o.hx * pose.hx + o.hz * pose.hz < 0.5) return false;
    return (o.x - pose.x) * pose.hx + (o.z - pose.z) * pose.hz < -car.length / 2;
  }

  /** A deadlock of two footprints (each car stopped by the other for UNLOCK_AFTER seconds): the car behind, or the
   *  lower key head-on, reverses along its route. Stopped cars right behind it that keep it from reversing reverse
   *  with it (up to UNLOCK_CHAIN cars, tail first), so a tight queue makes room too. Every pose is guard-checked:
   *  nothing reverses into a body, and nothing depends on the camera or a client's clock. */
  private unlock(car: FlowCar, other: FlowCar) {
    if (car.stopped < UNLOCK_AFTER || other.stopped < UNLOCK_AFTER || other.blockedBy !== `footprint:${car.key}`) return;
    const ahead = (other.pose.x - car.pose.x) * car.pose.hx + (other.pose.z - car.pose.z) * car.pose.hz;
    const behind = (car.pose.x - other.pose.x) * other.pose.hx + (car.pose.z - other.pose.z) * other.pose.hz;
    if (!(ahead > 0 && behind > 0 ? car.key < other.key : ahead > behind)) return;
    const back = Math.min(UNLOCK_SPEED * FLOW_STEP, UNLOCK_BACK - car.backed);
    const can = (c: FlowCar) => !c.change && c.s >= back && c.speed < 0.15 && !c.swerve;
    if (back <= 0 || !can(car)) return;
    const chain = [car], label = car.blockedBy;
    for (;;) {
      const last = chain[chain.length - 1], s = last.s;
      last.s = s - back;
      const clear = !this.reverseObstructed(last, sampleFlowPose(last, 0, () => 0));
      last.s = s;
      if (clear) break;
      const next = this.cars.get(last.blockedBy?.startsWith('footprint:') ? last.blockedBy.slice(10) : '');
      if (!next || chain.includes(next) || chain.length >= UNLOCK_CHAIN || !can(next)
        || (next.pose.x - last.pose.x) * last.pose.hx + (next.pose.z - last.pose.z) * last.pose.hz >= 0) { car.blockedBy = label; return; }
      chain.push(next);
    }
    for (let i = chain.length - 1; i >= 0; i--) {
      const c = chain[i], s = c.s, blocked = c.blockedBy;
      c.s = s - back;
      const pose = sampleFlowPose(c, 0, () => 0);
      if (this.reverseObstructed(c, pose)) { c.s = s; c.blockedBy = blocked; break; }
      this.unoccupy(c); c.pose = pose; this.occupy(c); c.backed += back; c.progress -= back; c.blockedBy = blocked;
    }
    car.blockedBy = label; this.stats.unlocks++;
  }

  /** The guard for reversing: a pose inside another car's margin is still allowed when it moves away from that car
   *  (clearance does not shrink) and keeps FOLLOWER_MARGIN of real clearance. */
  private reverseObstructed(car: FlowCar, pose: CarPose) {
    const x = Math.floor(pose.x / 8), z = Math.floor(pose.z / 8);
    for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) {
      for (const other of this.occupancy.get(`${x + i},${z + j}`) ?? []) {
        if (car === other || !bodyOverlap(pose, car, other.pose, other, GUARD_MARGIN) || this.levelApart(car, other)) continue;
        const after = separation(pose, car, other.pose, other);
        if (after < FOLLOWER_MARGIN || after < separation(car.pose, car, other.pose, other) - 1e-6) {
          car.blockedBy = `footprint:${other.key}`; return true;
        }
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
    // Fair turns for a receiving lane that fills from several approaches: a driver that has waited longer for it (and
    // has a green) takes the next space, rather than whichever approach happens to see the space first.
    const turn = this.longest.get(next);
    if (turn && turn !== car && turn.waiting > car.waiting + 1 && signalState(turn.route, t) === 'green') return blocked(`turn:${turn.key}`);
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
      // Never yield to a car that cannot go itself (its exit is full, it waits for a reservation or a signal): a
      // yield to a held car never ends. Its own yields to others still count, so priority chains stay intact.
      if (other.waiting > 2 && other.speed < 0.2 && other.blockedBy !== null && HELD.test(other.blockedBy)) continue;
      // A driver kept at its line for STARVE_AFTER seconds by a stream with priority takes its turn: drivers
      // that waited longest are evaluated first, so it reserves first and the stream stops for its reservation.
      // Only traffic that can still stop before its line, and never while a player holds either car up.
      if (car.waiting > STARVE_AFTER && !car.held && other.waiting < car.waiting
        && distance > other.speed * other.speed / (2 * BRAKE) + 1) continue;
      if (other.waiting > STARVE_AFTER && !other.held && other.waiting > car.waiting
        && lane.length - car.s - car.length / 2 > car.speed * car.speed / (2 * BRAKE) + 1) return blocked(`starved:${other.key}`);
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

/** Separating-axis clearance between two car bodies (m; negative when they overlap). */
function separation(a: CarPose, sa: { length: number; width: number }, b: CarPose, sb: { length: number; width: number }) {
  const dx = b.x - a.x, dz = b.z - a.z;
  let best = -Infinity;
  for (let k = 0; k < 4; k++) {
    const x = k === 0 ? a.hx : k === 1 ? -a.hz : k === 2 ? b.hx : -b.hz, z = k === 0 ? a.hz : k === 1 ? a.hx : k === 2 ? b.hz : b.hx;
    const ra = Math.abs(a.hx * x + a.hz * z) * sa.length / 2 + Math.abs(-a.hz * x + a.hx * z) * sa.width / 2;
    const rb = Math.abs(b.hx * x + b.hz * z) * sb.length / 2 + Math.abs(-b.hz * x + b.hx * z) * sb.width / 2;
    best = Math.max(best, Math.abs(dx * x + dz * z) - ra - rb);
  }
  return best;
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
/** A waiting car's body keeps this clearance (m) from crossing movements' paths, moving its stop point back by at most
 *  STOP_CLEAR_MAX m (geometry that needs more keeps the drawn line). */
const STOP_CLEAR = 0.3, STOP_CLEAR_MAX = 6;
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
  // Past a seam lane's end the path continues on the next tile's lane (the guard checks the pose a car hops to).
  if (s > lane.length && !lane.connector && lane.seamNext) return pointAtInto(lane.seamNext.points, lane.seamNext.cum, s - lane.length, out);
  if (s <= lane.length || !lane.connector) return pointAtInto(lane.points, lane.cum, s, out);
  if (s <= lane.length + lane.connector.length || !lane.next) return pointAtInto(lane.connector.points, lane.connector.cum, s - lane.length, out);
  return pointAtInto(lane.next.points, lane.next.cum, s - lane.length - lane.connector.length, out);
}
const midPoint: Point = { x: 0, z: 0, hx: 1, hz: 0 }, frontPoint: Point = { ...midPoint }, backPoint: Point = { ...midPoint }, fromPoint: Point = { ...midPoint };
const scratchA = blankPose(), scratchB = blankPose();

/** What a pose depends on: a car now, or the same fields one tick earlier (`FlowCar.previous`). */
export type PoseState = Pick<FlowCar, 'route' | 's' | 'speed' | 'change' | 'swerve' | 'swerveRate'> & { uturn?: UTurn | null };

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
    // The nose follows the lateral motion as a rolling car's would: below 3 m/s as if at 3 m/s (a car at a
    // standstill cannot turn sideways; the heading also no longer changes when the guard trims the speed).
    const fwd = Math.max(3, car.speed);
    const vx = fwd * (from.hx * ratio * (1 - f) + mid.hx * f) + (mid.x - from.x) * df;
    const vz = fwd * (from.hz * ratio * (1 - f) + mid.hz * f) + (mid.z - from.z) * df;
    const v = Math.sqrt(vx * vx + vz * vz);
    out.x = from.x + (mid.x - from.x) * f; out.z = from.z + (mid.z - from.z) * f;
    if (v > 0.05) { out.hx = vx / v; out.hz = vz / v; }
    const sourceY = height(change.from, sourceS);
    if (sourceY !== null && Number.isFinite(out.y)) out.y = sourceY + (out.y - sourceY) * f;
    out.speed = v * car.speed / fwd;
  }
  if (car.uturn) {
    // A U-shaped cubic from here onto the opposite lane, the nose along the path (a smoothstep in time).
    const u = car.uturn, t = u.progress, f = t * t * (3 - 2 * t);
    const p0 = { x: mid.x, z: mid.z, hx: mid.hx, hz: mid.hz }, p1 = routePointInto(u.to, u.at, frontPoint);
    const k = u.reach;
    const c0x = p0.x + p0.hx * k, c0z = p0.z + p0.hz * k, c1x = p1.x - p1.hx * k, c1z = p1.z - p1.hz * k;
    const g = 1 - f;
    out.x = g * g * g * p0.x + 3 * g * g * f * c0x + 3 * g * f * f * c1x + f * f * f * p1.x;
    out.z = g * g * g * p0.z + 3 * g * g * f * c0z + 3 * g * f * f * c1z + f * f * f * p1.z;
    const tx = 3 * g * g * (c0x - p0.x) + 6 * g * f * (c1x - c0x) + 3 * f * f * (p1.x - c1x);
    const tz = 3 * g * g * (c0z - p0.z) + 6 * g * f * (c1z - c0z) + 3 * f * f * (p1.z - c1z), tl = Math.hypot(tx, tz);
    if (tl > 1e-6) { out.hx = tx / tl; out.hz = tz / tl; }
    out.speed = tl * 6 * t * (1 - t) / UTURN_SECONDS;
    return out;
  }
  if (car.swerve) {
    // Going round a stopped car: shifted off the lane centre, nose turned along the lateral motion.
    out.x -= out.hz * car.swerve; out.z += out.hx * car.swerve;
    // The nose follows the lateral motion only as fast as the car rolls: a car at standstill cannot yaw, and
    // swinging its corners while edging aside locked a passing car and the oncoming one squeezing for it.
    const yaw = car.swerveRate * Math.min(1, out.speed / 3);
    const forward = Math.max(2, out.speed), hx = out.hx * forward - out.hz * yaw, hz = out.hz * forward + out.hx * yaw;
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
