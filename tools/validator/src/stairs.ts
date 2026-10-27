// Stair acceptance (ARCHITECTURE 1.5): every layer-listed flight, or each
// connected stair_slope mesh component, is climbed up and down by the walker.
import { MAT } from '../../../game/src/shared/col/index.ts';
import type { Ctx } from './context.ts';

const r3 = (v: number) => Math.round(v * 1000) / 1000;

export function checkStairs(ctx: Ctx) {
  const flights: any[] = [];
  for (const f of ctx.o.metadata!.stairs) {
    const [bx, by, bz] = f.bottom, [tx, ty, tz] = f.top;
    if (!ctx.owns((bx + tx) / 2, (bz + tz) / 2) || !ctx.inside((bx + tx) / 2, (bz + tz) / 2)) continue;
    const length = Math.hypot(tx - bx, tz - bz);
    if (!length) { flights.push({ id: f.id, ok: false, reason: 'zero stair run' }); continue; }
    const dx = -(tz - bz) / length * f.width / 2, dz = (tx - bx) / length * f.width / 2;
    const a = [bx + dx, by, bz + dz], b = [bx - dx, by, bz - dz];
    const c = [tx - dx, ty, tz - dz], d = [tx + dx, ty, tz + dz];
    flights.push(testFlight(ctx, f.id, [[a, b, c], [a, c, d]]));
  }
  if (ctx.o.metadata!.stairs.length) return { status: flights.some(f => !f.ok) ? 'fail' : flights.length ? 'pass' : 'skipped', count: flights.length, failed: flights.filter(f => !f.ok).length, flights, mode: 'layer-list' };
  for (const { tile } of ctx.loaded) {
    const [ox, oy, oz] = tile.header.origin;
    const P = tile.positions, I = tile.indices;
    const tris: number[] = [];
    for (let t = 0; t < tile.header.triangleCount; t++) if (tile.materials[t] === MAT.stair_slope) tris.push(t);
    if (!tris.length) continue;
    // connected components by shared vertex
    const parent = new Map<number, number>();
    const find = (a: number): number => { while (parent.get(a)! !== a) { parent.set(a, parent.get(parent.get(a)!)!); a = parent.get(a)!; } return a; };
    for (const t of tris) for (let c = 0; c < 3; c++) if (!parent.has(I[t * 3 + c])) parent.set(I[t * 3 + c], I[t * 3 + c]);
    for (const t of tris) { const a = find(I[t * 3]); parent.set(find(I[t * 3 + 1]), a); parent.set(find(I[t * 3 + 2]), a); }
    const comps = new Map<number, number[]>();
    for (const t of tris) { const r = find(I[t * 3]); if (!comps.has(r)) comps.set(r, []); comps.get(r)!.push(t); }
    for (const ts of comps.values()) flights.push(testFlight(ctx, `${tile.id}#${flights.length}`, ts.map((t) => [0, 1, 2].map((c) => {
      const i = I[t * 3 + c];
      return [ox + P[i * 3], oy + P[i * 3 + 1], oz + P[i * 3 + 2]];
    }))));
  }
  if (!flights.length) return { status: 'skipped', reason: 'no stair_slope triangles', flights: [] };
  const owned = flights.filter(f => ctx.owns((f.bottom[0] + f.top[0]) / 2, (f.bottom[1] + f.top[1]) / 2));
  flights.splice(0, flights.length, ...owned);
  const bad = flights.filter((f) => !f.ok);
  return { status: bad.length ? 'fail' : 'pass', count: flights.length, failed: bad.length, flights };
}

function testFlight(ctx: Ctx, id: string, tris: number[][][]) {
  let Nx = 0, Ny = 0, Nz = 0;
  for (const [a, b, c] of tris) {
    const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2], vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
    Nx += uy * vz - uz * vy; Ny += uz * vx - ux * vz; Nz += ux * vy - uy * vx;
  }
  if (Ny < 0) { Nx = -Nx; Ny = -Ny; Nz = -Nz; }
  const L = Math.hypot(Nx, Ny, Nz);
  const slope = (Math.acos(Ny / L) * 180) / Math.PI;
  const hl = Math.hypot(Nx, Nz);
  const ux = -Nx / hl, uz = -Nz / hl; // uphill
  const wx = -uz, wz = ux;
  let smin = Infinity, smax = -Infinity, lmin = Infinity, lmax = -Infinity, ybot = Infinity, ytop = -Infinity;
  for (const t of tris) for (const p of t) {
    const s = p[0] * ux + p[2] * uz, l = p[0] * wx + p[2] * wz;
    smin = Math.min(smin, s); smax = Math.max(smax, s); lmin = Math.min(lmin, l); lmax = Math.max(lmax, l);
    ybot = Math.min(ybot, p[1]); ytop = Math.max(ytop, p[1]);
  }
  const lc = (lmin + lmax) / 2;
  const at = (s: number) => [s * ux + lc * wx, s * uz + lc * wz];
  const k = ctx.o.kcc;
  const speed = 1.5;
  const frames = Math.round(((smax - smin + 2.4) / speed) * 60 * 1.5 + 60);
  const run = (dir: 1 | -1) => {
    const [sx, sz] = at(dir > 0 ? smin - 1.2 : smax + 1.2);
    const g = ctx.surfaceY(sx, sz, ytop + 3);
    if (!Number.isFinite(g) || !ctx.capsuleFree(sx, g + k.halfHeight + k.radius + 0.05, sz)) return { result: 'no-start' };
    ctx.walker.place(sx, g, sz);
    let done = false;
    ctx.simulate(frames, () => [ux * speed * dir, uz * speed * dir], () => {
      const s = ctx.walker.x * ux + ctx.walker.z * uz;
      if (dir > 0 && s >= smax + 0.3 && Math.abs(ctx.walker.feetY - ytop) <= 0.1) { done = true; return true; }
      if (dir < 0 && s <= smin - 0.3 && Math.abs(ctx.walker.feetY - ybot) <= 0.1) { done = true; return true; }
    }, `stair-${id}`);
    const w = ctx.walker;
    return { result: done ? 'ok' : 'stuck', end: [r3(w.x), r3(w.feetY), r3(w.z)] };
  };
  const up = run(1), down = run(-1);
  const slopeFlag = slope > ctx.o.stairMaxSlopeDeg;
  return {
    id,
    triangles: tris.length,
    slopeDeg: r3(slope),
    slopeFlag,
    rise: r3(ytop - ybot),
    run: r3(smax - smin),
    width: r3(lmax - lmin),
    bottom: at(smin).map(r3),
    top: at(smax).map(r3),
    up,
    down,
    ok: up.result === 'ok' && down.result === 'ok' && !slopeFlag,
  };
}
