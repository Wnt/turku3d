// Car shows (core/city.ts `carShows`): a parking lot that displays one of every vehicle the game can spawn instead of
// its generated parked cars. The layout is a pure function of the city profile, the fleet catalog and the lot's served
// slots, so every client (and every multiplayer peer) stands the same vehicle on the same spot under the same id, and
// a model added to the catalog joins the show on its own.
import { FLEET, type FleetModel } from '../actors/fleet/catalog';
import { CITY, type CarShow } from '../core/city';
import { VISUAL_L0, enToEngine } from '../core/origin';
import type { ParkedVehicle } from './manifest';

/** The trophy truck (actors/trophy4400): parked records name it `truck`; its size is the generator's TRUCK_SIZE
 *  (pipeline/turku3d/coarse/parked.py, tyres, bumpers and roof lights included). */
const TROPHY = { id: 'truck', family: 'trophy', body: 'pickup', length: 5.6, width: 2.5, height: 2.5 } as const;
/** Display order by role: the long vehicles fill the first (deepest) row, then the trophy truck, vans, SUVs and
 *  pickups, sedans, hatchbacks and the microcar. */
const ROLES = ['bus', 'truck', 'fire', 'ambulance', 'trophy', 'van', 'suv', 'sedan', 'hatchback-sports', 'microcar'];
/** Clean display paints, alternating along a row (liveried roles keep their livery). */
export const SHOW_PAINTS = ['red', 'white', 'blue', 'black', 'silver', 'beige', 'charcoal', 'grey'] as const;
/** Least free space (m) between neighbours in a row (mirror to mirror) and between back-to-back rows. */
const SIDE_GAP = 0.5, BACK_GAP = 0.6;

export interface Exhibit { id: string; family: string; body: string; length: number; width: number; height: number }

/** Every vehicle the game can spawn: each fleet catalog model and the trophy truck, in display order. */
export function exhibits(fleet: readonly FleetModel[] = FLEET): Exhibit[] {
  const rank = (e: Exhibit) => { const i = ROLES.indexOf(e.family); return i < 0 ? ROLES.length : i; };
  return [...fleet.map(m => ({ id: m.id, family: m.family, body: m.body, length: m.length, width: m.width, height: m.height })), { ...TROPHY }]
    .sort((a, b) => rank(a) - rank(b) || b.length - a.length || (a.id < b.id ? -1 : 1));
}

/** One exhibit's stand: centre (engine x, z), heading `yaw` (forward = sin, cos) and its row. */
export interface Stand { exhibit: Exhibit; x: number; z: number; yaw: number; row: number; aisle: number }

/** Rows along the area's 0 -> 1 edge, stacked toward corner 3: row 2k faces aisle k ahead of it, row 2k+1 faces the
 *  same aisle from the other side and stands back to back with row 2k+2. A row takes exhibits in display order while
 *  they fit side by side; its depth is its longest exhibit, fronts aligned at the aisle so each drives straight out.
 *  The slack of a row is shared evenly between its exhibits and the rows are centred across the area. Exhibits that
 *  no longer fit stay out (`left`). */
export function layoutShow(show: CarShow, list: readonly Exhibit[] = exhibits()): { stands: Stand[]; left: Exhibit[] } {
  const [c0, c1, , c3] = show.area.map(([E, N]) => enToEngine(E, N));
  const along = { x: c1.x - c0.x, z: c1.z - c0.z }, across = { x: c3.x - c0.x, z: c3.z - c0.z };
  const length = Math.hypot(along.x, along.z), width = Math.hypot(across.x, across.z);
  const a = { x: along.x / length, z: along.z / length }, b = { x: across.x / width, z: across.z / width };
  // Rows first: members, and where each front line falls across the area (from the 0 -> 1 edge).
  const rows: { members: Exhibit[]; front: number; facesB: boolean }[] = [];
  let next = 0, edge = 0;
  while (next < list.length) {
    const members: Exhibit[] = [];
    let used = 0;
    // The slack is shared evenly (half a share at each end), so every exhibit brings a full gap.
    for (let i = next; i < list.length; i++) {
      const w = list[i].width + SIDE_GAP;
      if (used + w > length) break;
      members.push(list[i]); used += w;
    }
    if (!members.length) break;
    const depth = Math.max(...members.map(e => e.length)), facesB = rows.length % 2 === 0;
    // An even row's rear is at `edge` and its aisle lies ahead; an odd row's front is at that aisle's far side.
    const front = facesB ? edge + depth : edge, end = facesB ? front + show.aisle : edge + depth;
    if (end > width + 1e-6) break;
    rows.push({ members, front, facesB });
    next += members.length;
    edge = facesB ? end : end + BACK_GAP;
  }
  const offset = Math.max(0, (width - (rows.length % 2 ? edge : edge - BACK_GAP)) / 2);
  const stands: Stand[] = [];
  rows.forEach(({ members, front, facesB }, row) => {
    const slack = (length - members.reduce((s, e) => s + e.width, 0)) / members.length;
    const dir = facesB ? b : { x: -b.x, z: -b.z };
    let s = 0;
    for (const e of members) {
      const t = s + slack / 2 + e.width / 2, d = offset + (facesB ? front - e.length / 2 : front + e.length / 2);
      s += e.width + slack;
      stands.push({ exhibit: e, x: c0.x + a.x * t + b.x * d, z: c0.z + a.z * t + b.z * d, yaw: Math.atan2(dir.x, dir.z), row,
        aisle: Math.floor(row / 2) });
    }
  });
  return { stands, left: list.slice(next) };
}

/** The visual tile (`t0_i_j`) that carries a show: the one holding its area's centre. */
export function showTile(show: CarShow): string {
  const ps = show.area.map(([E, N]) => enToEngine(E, N));
  const x = ps.reduce((s, p) => s + p.x, 0) / ps.length, z = ps.reduce((s, p) => s + p.z, 0) / ps.length;
  return `t0_${Math.floor(x / VISUAL_L0)}_${Math.floor(z / VISUAL_L0)}`;
}

/** Ground plane y = c + gx x + gz z through the lot's own slots (their settled heights), least squares. */
function groundPlane(slots: readonly ParkedVehicle[]): ((x: number, z: number) => number) & { gx: number; gz: number } {
  const n = slots.length, mx = slots.reduce((s, v) => s + v.x, 0) / n, mz = slots.reduce((s, v) => s + v.z, 0) / n;
  const my = slots.reduce((s, v) => s + v.y, 0) / n;
  let sxx = 0, sxz = 0, szz = 0, sxy = 0, szy = 0;
  for (const v of slots) {
    const dx = v.x - mx, dz = v.z - mz, dy = v.y - my;
    sxx += dx * dx; sxz += dx * dz; szz += dz * dz; sxy += dx * dy; szy += dz * dy;
  }
  const det = sxx * szz - sxz * sxz;
  const gx = Math.abs(det) > 1e-9 ? (sxy * szz - szy * sxz) / det : 0, gz = Math.abs(det) > 1e-9 ? (szy * sxx - sxy * sxz) / det : 0;
  return Object.assign((x: number, z: number) => my + gx * (x - mx) + gz * (z - mz), { gx, gz });
}

/** Whether point (x, z) lies inside the area expanded by `margin` metres. */
function inArea(show: CarShow, x: number, z: number, margin: number): boolean {
  const ps = show.area.map(([E, N]) => enToEngine(E, N));
  for (let i = 0; i < ps.length; i++) {
    const p = ps[i], q = ps[(i + 1) % ps.length], ex = q.x - p.x, ez = q.z - p.z, l = Math.hypot(ex, ez);
    // Corners run clockwise or counter-clockwise; the inside is the side of the centre.
    const cx = ps.reduce((s, c) => s + c.x, 0) / ps.length, cz = ps.reduce((s, c) => s + c.z, 0) / ps.length;
    const side = Math.sign(ex * (cz - p.z) - ez * (cx - p.x));
    if (side * (ex * (z - p.z) - ez * (x - p.x)) / l < -margin) return false;
  }
  return true;
}

/** The show's records: one per stand, exact model, display paint, no occupancy draw (always there), resting on the
 *  lot's ground plane. Ids are `show:<lot>:<model>`, stable when other models join or leave. */
export function showRecords(show: CarShow, slots: readonly ParkedVehicle[], list: readonly Exhibit[] = exhibits()): ParkedVehicle[] {
  const ground = groundPlane(slots);
  return layoutShow(show, list).stands.map(({ exhibit: e, x, z, yaw }, i) => {
    const fx = Math.sin(yaw), fz = Math.cos(yaw), rx = -fz, rz = fx;
    return { object_id: `show:${show.lot}:${e.id}`, model: e.id, color: SHOW_PAINTS[i % SHOW_PAINTS.length], show: show.lot,
      x, y: ground(x, z), z, yaw, pitch: Math.atan(ground.gx * fx + ground.gz * fz), roll: Math.atan(-(ground.gx * rx + ground.gz * rz)),
      width: e.width, height: e.height, length: e.length, clear_front_m: show.aisle, clear_rear_m: BACK_GAP };
  });
}

/** A tile's parked records with its car shows: the generated cars of each show's lot, and any other record standing
 *  in its area, give way to the show. A tile without the lot's slots (a first pass) keeps its records. */
export function withCarShows(tile: string, records: readonly ParkedVehicle[], shows: readonly CarShow[] = CITY.carShows ?? []): ParkedVehicle[] {
  let out: ParkedVehicle[] | null = null;
  for (const show of shows) {
    if (showTile(show) !== tile) continue;
    const lot: ParkedVehicle[] = (out ?? records).filter(v => v.source === show.lot);
    if (!lot.length) continue;
    out = [...(out ?? records).filter(v => v.source !== show.lot && !inArea(show, v.x, v.z, Math.hypot(v.length, v.width) / 2)),
      ...showRecords(show, lot)];
  }
  return out ?? [...records];
}
