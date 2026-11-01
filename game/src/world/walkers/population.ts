// Deterministic ambient walkers. Each 25 m birth cell owns conveyor slots; slot k of a cell is reborn every `life`
// seconds (its cycle n) at a hashed point of the cell's FOOT pieces and walks a hashed route through the network.
// Existence, route and position are pure functions of (network, UTC ms): clients sharing the epoch clock and the
// resident tiles see the same walkers without any network traffic. Rendering (ring, caps, fades) is local.
import { CYCLE_EPOCH, cycleHour } from '../../core/dayCycle';
import { CELL_M, cellKey, hash32, pointAt, unit, type Cell, type WalkNetwork } from './network';

export const WALKER = {
  /** Walkers per weighted metre of FOOT way at the busiest hour. */
  density: 0.03,
  speed: [1.2, 1.5] as const,
  /** Seconds a slot's walker lives before the slot is reborn elsewhere in its cell. */
  life: [70, 110] as const,
  /** Seconds of lifecycle fade at birth and at the end of life. */
  fade: 0.8,
  /** Idle at birth, and the chance and length of a pause at a junction. */
  birthIdle: 2.5, junctionPause: 0.12, pause: [2, 6] as const,
  /** Keep-right offset from the way's centreline, tapered to zero at junctions and dead ends. */
  keepRight: [0.35, 0.7] as const, taper: 1.5,
  variants: 3,
};
/** The farthest a walker can be from its birth point. */
export const MAX_TRAVEL = WALKER.life[1] * WALKER.speed[1];

/** Relative pedestrian activity by local hour (the day cycle's clock); births sample it, lives never end early. */
const ACTIVITY: [number, number][] = [[0, 0.12], [6, 0.15], [8, 0.55], [9, 0.75], [11.5, 1], [14, 1], [16, 0.85], [18, 0.9],
  [20, 0.6], [22, 0.35], [24, 0.12]];
/** Activity at a clock hour, read at the hour's whole minute. */
export function activityAt(clockHour: number): number {
  const hour = Math.floor((((clockHour % 24) + 24) % 24) * 60) / 60;
  for (let i = 1; i < ACTIVITY.length; i++) {
    const [h1, v1] = ACTIVITY[i], [h0, v0] = ACTIVITY[i - 1];
    if (hour <= h1) return v0 + (v1 - v0) * (hour - h0) / (h1 - h0);
  }
  return ACTIVITY[0][1];
}

/** The clock hour births sample at a shared UTC instant: the shared cycle by default (multiplayer, the scorecard);
 *  single player passes its view clock (DayClock.hourAt: the developer clock, ?time=, ?cycle=). */
export type BirthClock = (utcMs: number) => number;
const sharedHour: BirthClock = utcMs => cycleHour(utcMs).hour;

interface Leg {
  chain: number; dir: 1 | -1;
  /** Arc length at the start (along the chain) and seconds since birth at both ends. */
  s0: number; t0: number; t1: number;
  /** Metres walked before this leg. */
  d0: number;
  idle: boolean;
  /** Lateral offset tapers to zero at a decision node at the leg's start/end. */
  taper0: boolean; taper1: boolean;
}
interface Route { legs: Leg[]; known: number; speed: number; life: number; offset: number; box: [number, number, number, number] }


export interface Walker {
  /** Identity of this life (slot and cycle); the next life of the slot is another walker. */
  id: number;
  slot: number;
  variant: number;
  /** Height scale; tint indices are derived from `id` by the renderer. */
  scale: number;
  x: number; z: number;
  /** Surveyed deck height when the chain carries one, else NaN (the renderer samples the ground). */
  y: number;
  /** Facing, radians: atan2(dx, dz), the model faces +Z. */
  yaw: number;
  chain: number; s: number;
  moving: boolean;
  /** Metres walked (walk phase) or seconds into the current pause (idle phase). */
  distance: number; idleTime: number;
  /** Lifecycle fade, shared by every client. */
  fade: number;
  age: number;
  /** Slot parameters, to re-evaluate the same life later (`Population.at`). */
  cell: Cell; life: number; born: number;
}

const tmp = { x: 0, z: 0, dx: 0, dz: 0, y: 0 };

export class Population {
  private routes = new Map<number, { route: Route; used: number }>();
  private generation = 0;
  constructor(readonly network: WalkNetwork, private readonly clock: BirthClock = sharedHour) {}

  /** Activity at the start of a birth's minute (the model samples it per minute, so a time edit never changes a
   *  life that began before it). */
  private birthActivity(bornSeconds: number): number {
    return activityAt(this.clock(CYCLE_EPOCH + Math.floor(bornSeconds / 60) * 60_000));
  }

  /** Every walker whose route is known at `utcMs` within `radius` of (x, z). */
  near(x: number, z: number, radius: number, utcMs: number, out: Walker[] = []): Walker[] {
    out.length = 0;
    this.generation++;
    const reach = radius + MAX_TRAVEL;
    const t = (utcMs - CYCLE_EPOCH) / 1000;
    for (let cx = Math.floor((x - reach) / CELL_M); cx <= Math.floor((x + reach) / CELL_M); cx++) {
      for (let cz = Math.floor((z - reach) / CELL_M); cz <= Math.floor((z + reach) / CELL_M); cz++) {
        const cell = this.network.cells.get(cellKey(cx, cz));
        if (!cell) continue;
        const dx = Math.max(cx * CELL_M - x, 0, x - (cx + 1) * CELL_M), dz = Math.max(cz * CELL_M - z, 0, z - (cz + 1) * CELL_M);
        if (dx * dx + dz * dz > reach * reach) continue;
        this.cell(cell, t, w => { if ((w.x - x) ** 2 + (w.z - z) ** 2 <= radius * radius) out.push(w); }, { x, z, radius });
      }
    }
    if (this.routes.size > 4096) for (const [id, r] of this.routes) if (this.generation - r.used > 8) this.routes.delete(id);
    return out;
  }

  /** The walkers alive in one cell at t (seconds since CYCLE_EPOCH); `near` skips routes that never come close. */
  cell(cell: Cell, t: number, visit: (walker: Walker) => void, near?: { x: number; z: number; radius: number }) {
    const expected = WALKER.density * cell.weighted;
    for (let k = 0; k < Math.ceil(expected); k++) {
      const slot = hash32(cell.cx, cell.cz, k);
      const life = WALKER.life[0] + (WALKER.life[1] - WALKER.life[0]) * unit(hash32(slot, 1));
      const phase = life * unit(hash32(slot, 2));
      const cycle = Math.floor((t - phase) / life);
      const born = cycle * life + phase;
      const id = hash32(slot, cycle);
      if (k + unit(hash32(id, 3)) >= expected * this.birthActivity(born)) continue;
      const walker = this.walker(cell, slot, id, life, born, t, near);
      if (walker) visit(walker);
    }
  }

  /** The same life at another time; null once it ended or its route is unknown here. */
  at(walker: Walker, utcMs: number): Walker | null {
    return this.walker(walker.cell, walker.slot, walker.id, walker.life, walker.born, (utcMs - CYCLE_EPOCH) / 1000);
  }

  private walker(cell: Cell, slot: number, id: number, life: number, born: number, t: number,
    near?: { x: number; z: number; radius: number }): Walker | null {
    const age = t - born;
    if (age < 0 || age >= life) return null;
    let entry = this.routes.get(id);
    if (!entry) { entry = { route: this.route(cell, id, life), used: 0 }; this.routes.set(id, entry); }
    entry.used = this.generation;
    const route = entry.route;
    if (age > route.known || !route.legs.length) return null;
    if (near) {
      const [x0, z0, x1, z1] = route.box;
      const dx = Math.max(x0 - near.x, 0, near.x - x1), dz = Math.max(z0 - near.z, 0, near.z - z1);
      if (dx * dx + dz * dz > near.radius * near.radius) return null;
    }
    let lo = 0, hi = route.legs.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (route.legs[mid].t0 <= age) lo = mid; else hi = mid - 1; }
    const leg = route.legs[lo], chain = this.network.chains[leg.chain];
    const elapsed = Math.max(0, age - leg.t0);
    const walked = leg.idle ? 0 : Math.min(elapsed * route.speed, (leg.t1 - leg.t0) * route.speed);
    const s = leg.s0 + leg.dir * walked;
    pointAt(chain, s, tmp);
    const fx = tmp.dx * leg.dir, fz = tmp.dz * leg.dir;
    let side = route.offset;
    if (!leg.idle) {
      const length = (leg.t1 - leg.t0) * route.speed;
      if (leg.taper0) side *= Math.min(1, walked / WALKER.taper);
      if (leg.taper1) side *= Math.min(1, (length - walked) / WALKER.taper);
    } else side = 0;
    return {
      id, slot, variant: hash32(id, 8) % WALKER.variants, scale: 0.94 + 0.12 * unit(hash32(id, 9)),
      x: tmp.x - fz * side, z: tmp.z + fx * side, y: tmp.y, yaw: Math.atan2(fx, fz), chain: leg.chain, s,
      moving: !leg.idle, distance: leg.d0 + walked, idleTime: leg.idle ? elapsed : 0,
      fade: Math.max(0, Math.min(1, age / WALKER.fade, (life - age) / WALKER.fade)), age, cell, life, born,
    };
  }

  /** Birth on a hashed point of the cell's pieces (weighted), then hashed turns at junctions until the life ends or
   *  the walker reaches a node whose tiles are not all resident (beyond it the route is unknown here). */
  private route(cell: Cell, id: number, life: number): Route {
    const { chains, nodes } = this.network;
    const speed = WALKER.speed[0] + (WALKER.speed[1] - WALKER.speed[0]) * unit(hash32(id, 6));
    const offset = WALKER.keepRight[0] + (WALKER.keepRight[1] - WALKER.keepRight[0]) * unit(hash32(id, 7));
    const legs: Leg[] = [];
    // Birth point: canonical piece order, offset from the piece's smaller quantised end.
    let u = unit(hash32(id, 4)) * cell.weighted, piece = cell.pieces[cell.pieces.length - 1];
    for (const p of cell.pieces) { const w = p.length * p.weight; if (u < w) { piece = p; break; } u -= w; }
    const along = Math.min(piece.length, u / piece.weight), forward = piece.forward;
    let s = forward ? piece.s0 + along : piece.s0 + piece.length - along;
    const towardsLarger = (hash32(id, 5) & 1) === 1;
    let dir: 1 | -1 = towardsLarger === forward ? 1 : -1;
    let c = piece.chain, t = 0, d = 0, step = 0, taper0 = false;
    const pause = (duration: number) => { legs.push({ chain: c, dir, s0: s, t0: t, t1: t + duration, d0: d, idle: true, taper0: false, taper1: false }); t += duration; };
    pause(WALKER.birthIdle * unit(hash32(id, 10)));
    for (let guard = 0; t < life && guard < 256; guard++) {
      const length = dir > 0 ? chains[c].length - s : s;
      const nodeIndex = dir > 0 ? chains[c].b : chains[c].a, node = nodes[nodeIndex];
      const decision = !node.complete || node.exits.length !== 2;
      legs.push({ chain: c, dir, s0: s, t0: t, t1: t + length / speed, d0: d, idle: false, taper0, taper1: decision });
      t += length / speed; d += length;
      if (t >= life) break;
      if (!node.complete) return { legs, known: t, speed, life, offset, box: this.box(legs, speed) };
      const arriving = node.exits.findIndex(e => e.chain === c && e.atStart === (dir < 0));
      let exit = node.exits[arriving];
      if (node.exits.length === 2) exit = node.exits[1 - arriving];
      else if (node.exits.length > 2) {
        step++;
        const options = node.exits.filter((_, i) => i !== arriving);
        exit = options[hash32(id, node.key, step) % options.length];
        if (unit(hash32(id, node.key, step, 11)) < WALKER.junctionPause) {
          s = dir > 0 ? chains[c].length : 0;
          pause(WALKER.pause[0] + (WALKER.pause[1] - WALKER.pause[0]) * unit(hash32(id, node.key, step, 12)));
        }
      }
      c = exit.chain; dir = exit.atStart ? 1 : -1; s = exit.atStart ? 0 : chains[c].length; taper0 = decision;
    }
    return { legs, known: life, speed, life, offset, box: this.box(legs, speed) };
  }

  /** Bounds of the walked arcs, padded by the keep-right offset. */
  private box(legs: Leg[], speed: number): [number, number, number, number] {
    const box: [number, number, number, number] = [Infinity, Infinity, -Infinity, -Infinity];
    for (const leg of legs) {
      const chain = this.network.chains[leg.chain], c = chain.cumulative, p = chain.points;
      const s1 = leg.s0 + leg.dir * (leg.idle ? 0 : (leg.t1 - leg.t0) * speed);
      const lo = Math.min(leg.s0, s1), hi = Math.max(leg.s0, s1);
      const add = (x: number, z: number) => { box[0] = Math.min(box[0], x); box[1] = Math.min(box[1], z); box[2] = Math.max(box[2], x); box[3] = Math.max(box[3], z); };
      for (const s of [lo, hi]) { pointAt(chain, s, tmp); add(tmp.x, tmp.z); }
      for (let i = 0; i < c.length; i++) if (c[i] > lo && c[i] < hi) add(p[2 * i], p[2 * i + 1]);
    }
    const pad = WALKER.keepRight[1];
    return [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad];
  }
}

/** Walkers within `radius` of (x, z) at `utcMs` (the street-richness scorecard reads pedestrians from tile data). */
export function walkersNear(network: WalkNetwork, x: number, z: number, radius: number, utcMs: number): Walker[] {
  return new Population(network).near(x, z, radius, utcMs);
}
