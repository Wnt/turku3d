// Deterministic ambient walkers. Each 25 m birth cell owns conveyor slots; slot k of a cell is reborn every `life`
// seconds (its cycle n) at a hashed point of the cell's FOOT pieces and walks a hashed route through the network.
// Existence, route and position are pure functions of (network, UTC ms): clients sharing the epoch clock and the
// resident tiles see the same walkers without any network traffic. Rendering (ring, caps, fades) is local.
import { HUMAN_MODELS } from './appearance';
import { CYCLE_EPOCH, cycleHour } from '../../core/dayCycle';
import { CELL_M, cellKey, cellPiece, hash32, PIECE_STRIDE, pointAt, unit, type Cell, type Gate, type WalkNetwork } from './network';
import { TRAFFIC } from '../../traffic/constants';
import { hash01, mod } from '../../traffic/hash';

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
  variants: HUMAN_MODELS.length,
  /** At a signalised crossing: the walk phase of the crossed road's approach axis (seconds of its 60 s plan, where
   *  drivers on that road are red from 21 s and their left arrows end at 24 s), the kerb distance from the
   *  carriageway, how far back a waiting walker may stand, and the share who cross against the light. */
  walkFrom: 25, walkUntil: 56, kerb: 0.5, queue: 1.2, jaywalk: 0.06,
  /** Share of lives that stand on the pavement for their whole life (waiting, chatting: GTA's dense stand and phone
   *  scenarios), the share of those who stand in a face-to-face pair, the pair's spacing and the standers' offset. */
  stand: 0.16, pair: 0.55, spacing: 0.85, standSide: [0.5, 1.0] as const,
  /** Seconds a route continues past the end of its life: a client keeps drawing a walker whose life ends in view
   *  until it leaves the view (rendering only; nothing shared depends on it). */
  linger: 30,
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
    const h1 = ACTIVITY[i][0], v1 = ACTIVITY[i][1], h0 = ACTIVITY[i - 1][0], v0 = ACTIVITY[i - 1][1];
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
  /** Lateral offset of an idle leg (waiting at a kerb keeps the walker's offset; other pauses stand on the line). */
  side?: number;
  /** Lateral offset tapers to zero at a decision node at the leg's start/end. */
  taper0: boolean; taper1: boolean;
}
/** Route-box prefilter of a scan: one point, or several (x, z pairs). */
type Near = { x: number; z: number; radius: number } | { spots: Float64Array; radius: number };
interface Route { legs: Leg[]; known: number; speed: number; life: number; offset: number; box: [number, number, number, number];
  /** People in this life: 1, or 2 for a standing pair (the second faces the first, `WALKER.spacing` along the way). */
  group: number }


export interface Walker {
  /** Identity of this person (its life's id for the first member of a group); the next life of the slot is another walker. */
  id: number;
  /** The life (slot and cycle) and this person's place in its group. */
  lifeId: number; member: number;
  slot: number;
  /** Stable catalogue identity; the renderer maps it to a shared wardrobe mesh and anatomical profile. */
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
  /** Walking speed of this life (m/s). */
  speed: number;
  /** Metres walked (walk phase) or seconds into the current pause (idle phase). */
  distance: number; idleTime: number;
  /** Lifecycle fade, shared by every client. */
  fade: number;
  age: number;
  /** Slot parameters, to re-evaluate the same life later (`Population.at`). */
  cell: Cell; life: number; born: number;
}

const tmp = { x: 0, z: 0, dx: 0, dz: 0, y: 0 };
const beforeTurn = { ...tmp }, afterTurn = { ...tmp };

export class Population {
  private routes = new Map<number, { route: Route; used: number }>();
  private generation = 0;
  /** Traffic clock seconds minus walker seconds: the drivers' signal plan runs on the traffic clock. Equal clocks
   *  (multiplayer, single player before any pause) give the epoch difference. */
  private signalOffset = (CYCLE_EPOCH - TRAFFIC.epochMs) / 1000;
  constructor(readonly network: WalkNetwork, private readonly clock: BirthClock = sharedHour) {}

  /** Follow a shifted traffic clock (a single-player pause): routes are planned again with the new signal phases. */
  setSignalOffset(seconds: number) {
    if (Math.abs(seconds - this.signalOffset) < 0.5) return;
    this.signalOffset = seconds; this.routes.clear();
  }

  /** Seconds to wait at a gate reached at walker time `t` (s since CYCLE_EPOCH) before crossing at `speed`. */
  private signalWait(gate: Gate, t: number, speed: number): number {
    const phase = mod(t + this.signalOffset + hash01('signal-clock', gate.signal) * 60 - gate.group * 30, 60);
    const cross = (2 * gate.half + 2 * WALKER.kerb) / speed;
    const until = Math.max(WALKER.walkFrom + 3, WALKER.walkUntil - cross);
    return phase >= WALKER.walkFrom && phase < until ? 0 : mod(WALKER.walkFrom - phase, 60);
  }

  /** Activity at the start of a birth's minute (the model samples it per minute, so a time edit never changes a
   *  life that began before it). */
  private birthActivity(bornSeconds: number): number {
    const minute = Math.floor(bornSeconds / 60);
    let activity = this.activities.get(minute);
    if (activity === undefined) { activity = activityAt(this.clock(CYCLE_EPOCH + minute * 60_000)); this.activities.set(minute, activity); }
    return activity;
  }
  /** Birth-minute activity within one scan (a scan's lives were born in a handful of minutes). */
  private activities = new Map<number, number>();
  /** Cells that can hold a life within `radius` of a spot, with those spots; rebuilt when the spots change. */
  private spotCells: { spots: readonly { x: number; z: number }[]; radius: number; cells: { cell: Cell; spots: Float64Array }[] } | null = null;

  /** Every walker whose route is known at `utcMs` within `radius` of any of `spots` (crossings: what drivers yield
   *  to). Scans only the cells a life near a spot can come from, not the spots' bounding disc. */
  around(spots: readonly { x: number; z: number }[], radius: number, utcMs: number, out: Walker[] = []): Walker[] {
    out.length = 0;
    if (!spots.length) return out;
    this.generation++; this.activities.clear();
    if (!this.spotCells || this.spotCells.spots !== spots || this.spotCells.radius !== radius) {
      const reach = radius + MAX_TRAVEL, byCell = new Map<number, { cell: Cell; spots: number[] }>();
      for (const p of spots) {
        for (let cx = Math.floor((p.x - reach) / CELL_M); cx <= Math.floor((p.x + reach) / CELL_M); cx++) {
          for (let cz = Math.floor((p.z - reach) / CELL_M); cz <= Math.floor((p.z + reach) / CELL_M); cz++) {
            const key = cellKey(cx, cz), cell = this.network.cells.get(key);
            if (!cell) continue;
            const dx = Math.max(cx * CELL_M - p.x, 0, p.x - (cx + 1) * CELL_M), dz = Math.max(cz * CELL_M - p.z, 0, p.z - (cz + 1) * CELL_M);
            if (dx * dx + dz * dz > reach * reach) continue;
            let entry = byCell.get(key);
            if (!entry) { entry = { cell, spots: [] }; byCell.set(key, entry); }
            entry.spots.push(p.x, p.z);
          }
        }
      }
      this.spotCells = { spots, radius, cells: [...byCell.values()].map(e => ({ cell: e.cell, spots: Float64Array.from(e.spots) })) };
    }
    const t = (utcMs - CYCLE_EPOCH) / 1000, r2 = radius * radius;
    for (const { cell, spots: near } of this.spotCells.cells) {
      this.cell(cell, t, w => {
        for (let i = 0; i < near.length; i += 2) if ((w.x - near[i]) ** 2 + (w.z - near[i + 1]) ** 2 < r2) { out.push(w); return; }
      }, { spots: near, radius });
    }
    this.prune();
    return out;
  }

  private prune() {
    if (this.routes.size > 4096) for (const [id, r] of this.routes) if (this.generation - r.used > 8) this.routes.delete(id);
  }

  /** Every walker whose route is known at `utcMs` within `radius` of (x, z). */
  near(x: number, z: number, radius: number, utcMs: number, out: Walker[] = []): Walker[] {
    out.length = 0;
    this.generation++; this.activities.clear();
    const reach = radius + MAX_TRAVEL;
    const t = (utcMs - CYCLE_EPOCH) / 1000, r2 = radius * radius, filter = { x, z, radius };
    const visit = (w: Walker) => { if ((w.x - x) ** 2 + (w.z - z) ** 2 <= r2) out.push(w); };
    for (let cx = Math.floor((x - reach) / CELL_M); cx <= Math.floor((x + reach) / CELL_M); cx++) {
      for (let cz = Math.floor((z - reach) / CELL_M); cz <= Math.floor((z + reach) / CELL_M); cz++) {
        const cell = this.network.cells.get(cellKey(cx, cz));
        if (!cell) continue;
        const dx = Math.max(cx * CELL_M - x, 0, x - (cx + 1) * CELL_M), dz = Math.max(cz * CELL_M - z, 0, z - (cz + 1) * CELL_M);
        if (dx * dx + dz * dz > reach * reach) continue;
        this.cell(cell, t, visit, filter);
      }
    }
    this.prune();
    return out;
  }

  /** The walkers alive in one cell at t (seconds since CYCLE_EPOCH); `near` skips routes that never come close. */
  cell(cell: Cell, t: number, visit: (walker: Walker) => void, near?: Near) {
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
      if (!walker) continue;
      visit(walker);
      const group = this.routes.get(id)?.route.group ?? 1;
      for (let member = 1; member < group; member++) {
        const other = this.walker(cell, slot, id, life, born, t, near, member);
        if (other) visit(other);
      }
    }
  }

  /** The same life at another time; null once it ended (with `linger`, up to WALKER.linger later: rendering only) or its
   *  route is unknown here. */
  at(walker: Walker, utcMs: number, linger = false, into?: Walker): Walker | null {
    return this.walker(walker.cell, walker.slot, walker.lifeId, walker.life, walker.born, (utcMs - CYCLE_EPOCH) / 1000, undefined, walker.member, linger, into);
  }

  private walker(cell: Cell, slot: number, id: number, life: number, born: number, t: number,
    near?: Near, member = 0, linger = false, into?: Walker): Walker | null {
    const age = t - born;
    if (age < 0 || age >= (linger ? life + WALKER.linger : life)) return null;
    let entry = this.routes.get(id);
    if (!entry) { entry = { route: this.route(cell, id, life, born), used: 0 }; this.routes.set(id, entry); }
    entry.used = this.generation;
    const route = entry.route;
    if (age > route.known || !route.legs.length) return null;
    if (near) {
      const box = route.box, x0 = box[0], z0 = box[1], x1 = box[2], z1 = box[3], r2 = near.radius * near.radius;
      if ('spots' in near) {
        let close = false;
        for (let i = 0; i < near.spots.length && !close; i += 2) {
          const dx = Math.max(x0 - near.spots[i], 0, near.spots[i] - x1), dz = Math.max(z0 - near.spots[i + 1], 0, near.spots[i + 1] - z1);
          close = dx * dx + dz * dz <= r2;
        }
        if (!close) return null;
      } else {
        const dx = Math.max(x0 - near.x, 0, near.x - x1), dz = Math.max(z0 - near.z, 0, near.z - z1);
        if (dx * dx + dz * dz > r2) return null;
      }
    }
    let lo = 0, hi = route.legs.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (route.legs[mid].t0 <= age) lo = mid; else hi = mid - 1; }
    const leg = route.legs[lo], chain = this.network.chains[leg.chain];
    const elapsed = Math.max(0, age - leg.t0);
    const walked = leg.idle ? 0 : Math.min(elapsed * route.speed, (leg.t1 - leg.t0) * route.speed);
    // The second of a standing pair stands further along the way, facing back.
    const s = leg.s0 + leg.dir * (walked + member * WALKER.spacing);
    pointAt(chain, s, tmp);
    // A segment normal jumps at each polyline corner. Average the tangent over an arc-length window
    // instead: the keep-right displacement remains continuous, shrinking naturally at a tight U-turn.
    const loS = Math.max(0, s - WALKER.taper / 2), hiS = Math.min(chain.length, s + WALKER.taper / 2);
    pointAt(chain, loS, beforeTurn); pointAt(chain, hiS, afterTurn);
    const span = Math.max(1e-6, hiS - loS);
    const fx = (afterTurn.x - beforeTurn.x) / span * leg.dir, fz = (afterTurn.z - beforeTurn.z) / span * leg.dir;
    let side = route.offset;
    if (!leg.idle) {
      const length = (leg.t1 - leg.t0) * route.speed;
      if (leg.taper0) side *= Math.min(1, walked / WALKER.taper);
      if (leg.taper1) side *= Math.min(1, (length - walked) / WALKER.taper);
    } else side = leg.side ?? 0;
    const person = member ? hash32(id, 17, member) : id, variant = hash32(person, 8) % WALKER.variants;
    // Re-evaluating a known life writes into the caller's object: the per-frame path allocates nothing.
    const out = into ?? ({} as Walker);
    out.id = person; out.lifeId = id; out.member = member; out.slot = slot; out.variant = variant;
    out.scale = HUMAN_MODELS[variant].height;
    out.x = tmp.x - fz * side; out.z = tmp.z + fx * side; out.y = tmp.y; out.yaw = Math.atan2(fx, fz) + (member ? Math.PI : 0);
    out.chain = leg.chain; out.s = s; out.moving = !leg.idle; out.speed = route.speed; out.distance = leg.d0 + walked;
    out.idleTime = leg.idle ? elapsed : 0;
    out.fade = Math.max(0, Math.min(1, age / WALKER.fade, ((linger ? life + WALKER.linger : life) - age) / WALKER.fade));
    out.age = age; out.cell = cell; out.life = life; out.born = born;
    return out;
  }

  /** Birth on a hashed point of the cell's pieces (weighted), then hashed turns at junctions until the life ends or
   *  the walker reaches a node whose tiles are not all resident (beyond it the route is unknown here). */
  private route(cell: Cell, id: number, life: number, born: number): Route {
    const { chains, nodes } = this.network;
    const speed = WALKER.speed[0] + (WALKER.speed[1] - WALKER.speed[0]) * unit(hash32(id, 6));
    const offset = WALKER.keepRight[0] + (WALKER.keepRight[1] - WALKER.keepRight[0]) * unit(hash32(id, 7));
    const legs: Leg[] = [];
    // Birth point: canonical piece order, offset from the piece's smaller quantised end.
    const count = cell.pieces.length / PIECE_STRIDE;
    let u = unit(hash32(id, 4)) * cell.weighted, k = count - 1;
    for (let j = 0; j < count; j++) {
      const w = cell.pieces[j * PIECE_STRIDE + 2] * cell.pieces[j * PIECE_STRIDE + 4];
      if (u < w) { k = j; break; }
      u -= w;
    }
    const piece = cellPiece(cell, k);
    const along = Math.min(piece.length, u / piece.weight), forward = piece.forward;
    let s = forward ? piece.s0 + along : piece.s0 + piece.length - along;
    const towardsLarger = (hash32(id, 5) & 1) === 1;
    let dir: 1 | -1 = towardsLarger === forward ? 1 : -1;
    let c = piece.chain, t = 0, d = 0, step = 0, taper0 = true;
    // A cell can outlive the network it was cut from (tiles streamed in or out): no walker on a vanished chain.
    if (!chains[c]) return { legs, known: 0, speed, life, offset, box: [0, 0, 0, 0], group: 1 };
    // A stander keeps its birth point for the whole life, a little further from the way's centre; never on a zebra.
    if (!chains[c].gates.length && unit(hash32(id, 16)) < WALKER.stand) {
      const group = unit(hash32(id, 19)) < WALKER.pair ? 2 : 1, reach = (group - 1) * WALKER.spacing;
      if ((dir > 0 ? chains[c].length - s : s) < reach) dir = dir > 0 ? -1 : 1;
      if ((dir > 0 ? chains[c].length - s : s) >= reach) {
        const side = WALKER.standSide[0] + (WALKER.standSide[1] - WALKER.standSide[0]) * unit(hash32(id, 18));
        legs.push({ chain: c, dir, s0: s, t0: 0, t1: life + WALKER.linger, d0: 0, idle: true, side, taper0: false, taper1: false });
        return { legs, known: life + WALKER.linger, speed, life, offset: side, box: this.box(legs, speed, reach), group };
      }
    }
    const pause = (duration: number) => { legs.push({ chain: c, dir, s0: s, t0: t, t1: t + duration, d0: d, idle: true, taper0: false, taper1: false }); t += duration; };
    pause(WALKER.birthIdle * unit(hash32(id, 10)));
    const horizon = life + WALKER.linger;
    for (let guard = 0; t < horizon && guard < 256; guard++) {
      if (!chains[c]) break;
      // Signalised crossings ahead on this chain: walk to the kerb, wait for the walk phase, then cross.
      const gates = chains[c].gates;
      for (let g = dir > 0 ? 0 : gates.length - 1; g >= 0 && g < gates.length && t < horizon; g += dir) {
        const gate = gates[g], back = WALKER.queue * unit(hash32(id, g, 14));
        const kerb = gate.s - dir * (gate.half + WALKER.kerb + back);
        if ((kerb - s) * dir < 0.2 || (gate.s - s) * dir <= 0) continue;
        const reach = Math.abs(kerb - s);
        legs.push({ chain: c, dir, s0: s, t0: t, t1: t + reach / speed, d0: d, idle: false, taper0, taper1: false });
        t += reach / speed; d += reach; s = kerb; taper0 = false;
        const wait = unit(hash32(id, g, 15)) < WALKER.jaywalk ? 0 : this.signalWait(gate, born + t, speed);
        if (wait > 0 && t < horizon) { legs.push({ chain: c, dir, s0: s, t0: t, t1: t + wait, d0: d, idle: true, side: offset, taper0: false, taper1: false }); t += wait; }
      }
      if (t >= horizon) break;
      const length = dir > 0 ? chains[c].length - s : s;
      const nodeIndex = dir > 0 ? chains[c].b : chains[c].a, node = nodes[nodeIndex];
      legs.push({ chain: c, dir, s0: s, t0: t, t1: t + length / speed, d0: d, idle: false, taper0, taper1: true });
      t += length / speed; d += length;
      if (t >= horizon) break;
      if (!node.complete) return { legs, known: t, speed, life, offset, box: this.box(legs, speed, 0, life), group: 1 };
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
      c = exit.chain; dir = exit.atStart ? 1 : -1; s = exit.atStart ? 0 : chains[c].length; taper0 = true;
    }
    return { legs, known: horizon, speed, life, offset, box: this.box(legs, speed, 0, life), group: 1 };
  }

  /** Bounds of the walked arcs, padded by the keep-right offset. */
  private box(legs: Leg[], speed: number, reach = 0, until = Infinity): [number, number, number, number] {
    const box: [number, number, number, number] = [Infinity, Infinity, -Infinity, -Infinity];
    for (const leg of legs) {
      if (leg.t0 >= until) break;
      const chain = this.network.chains[leg.chain], c = chain.cumulative, p = chain.points;
      const s1 = leg.s0 + leg.dir * (leg.idle ? reach : (leg.t1 - leg.t0) * speed);
      const lo = Math.min(leg.s0, s1), hi = Math.max(leg.s0, s1);
      const add = (x: number, z: number) => { box[0] = Math.min(box[0], x); box[1] = Math.min(box[1], z); box[2] = Math.max(box[2], x); box[3] = Math.max(box[3], z); };
      for (const s of [lo, hi]) { pointAt(chain, s, tmp); add(tmp.x, tmp.z); }
      for (let i = 0; i < c.length; i++) if (c[i] > lo && c[i] < hi) add(p[2 * i], p[2 * i + 1]);
    }
    const pad = Math.max(WALKER.keepRight[1], WALKER.standSide[1]);
    return [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad];
  }
}

/** Walkers within `radius` of (x, z) at `utcMs` (the street-richness scorecard reads pedestrians from tile data). */
export function walkersNear(network: WalkNetwork, x: number, z: number, radius: number, utcMs: number): Walker[] {
  return new Population(network).near(x, z, radius, utcMs);
}
