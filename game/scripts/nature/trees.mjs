// Procedural Turku vegetation: species skeletons (trunk, scaffold branches, branchlets), tube bark meshes with a
// column bark atlas, leaf/needle cards from the foliage atlas, grass clumps, ground rosettes and hedge blocks.
// Units are metres, +Y up, trunk base at the origin. Everything here is project-owned procedural code.
import { rng } from './image.mjs';
import { FOLIAGE_CELLS, BARK_SLOTS, BARK_SLOT, BARK_GUTTER } from './textures.mjs';

const V = {
  add: (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  mul: (a, k) => [a[0] * k, a[1] * k, a[2] * k],
  dot: (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
  cross: (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
  len: a => Math.hypot(a[0], a[1], a[2]),
  norm: a => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; },
  lerp: (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t],
  /** Rotate v around unit axis k by angle (Rodrigues). */
  rot: (v, k, angle) => {
    const c = Math.cos(angle), s = Math.sin(angle), d = V.dot(k, v), x = V.cross(k, v);
    return [v[0] * c + x[0] * s + k[0] * d * (1 - c), v[1] * c + x[1] * s + k[1] * d * (1 - c), v[2] * c + x[2] * s + k[2] * d * (1 - c)];
  },
};
const UP = [0, 1, 0];
const anyPerp = d => V.norm(Math.abs(d[1]) < 0.9 ? V.cross(d, UP) : V.cross(d, [1, 0, 0]));

export class MeshBuilder {
  constructor() { this.positions = []; this.normals = []; this.uvs = []; this.colors = []; this.wind = []; this.indices = []; }
  vertex(p, n, uv, color, wind) {
    this.positions.push(...p); this.normals.push(...n); this.uvs.push(...uv); this.colors.push(...color); this.wind.push(...wind);
    return this.positions.length / 3 - 1;
  }
  tri(a, b, c) { this.indices.push(a, b, c); }
  get vertexCount() { return this.positions.length / 3; }
  get triangles() { return this.indices.length / 3; }
  bounds() {
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < this.positions.length; i += 3) for (let k = 0; k < 3; k++) {
      min[k] = Math.min(min[k], this.positions[i + k]); max[k] = Math.max(max[k], this.positions[i + k]);
    }
    return { min, max };
  }
}

/** Foliage atlas cell rect in glTF UV space (v down). */
export function cellUV(name) {
  const i = FOLIAGE_CELLS.indexOf(name);
  if (i < 0) throw new Error(`no foliage cell ${name}`);
  const col = i % 4, row = Math.floor(i / 4), inset = 1 / 2048;
  return { u0: col / 4 + inset, u1: (col + 1) / 4 - inset, v0: row / 4 + inset, v1: (row + 1) / 4 - inset };
}
/** Bark column atlas U range for a slot (content without the wrap gutters). */
function barkU(slot) {
  const i = BARK_SLOTS.indexOf(slot), W = BARK_SLOT * BARK_SLOTS.length;
  return [(i * BARK_SLOT + BARK_GUTTER) / W, ((i + 1) * BARK_SLOT - BARK_GUTTER) / W];
}

// ---------------------------------------------------------------- skeletons

/** A branch: polyline points with radius, plus wind sway weight at each point. */
function branch(level, start, dir, length, r0, r1, segs, bend, r, sway0, phase) {
  const pts = [{ p: start, r: r0, sway: sway0 }];
  let p = start, d = V.norm(dir);
  const step = length / segs;
  for (let i = 1; i <= segs; i++) {
    const t = i / segs;
    d = V.norm(V.add(V.add(d, V.mul(UP, bend.up * step)), V.add(V.mul(UP, -bend.droop * step * t), V.mul([r() - 0.5, r() - 0.5, r() - 0.5], bend.noise))));
    p = V.add(p, V.mul(d, step));
    pts.push({ p, r: r0 + (r1 - r0) * Math.pow(t, 0.8), sway: sway0 + bend.sway * t * length });
  }
  return { level, pts, length, phase };
}

/** Point and direction at arc fraction t along a branch. */
function at(b, t) {
  const f = t * (b.pts.length - 1), i = Math.min(b.pts.length - 2, Math.floor(f)), k = f - i;
  const a = b.pts[i], c = b.pts[i + 1];
  return { p: V.lerp(a.p, c.p, k), r: a.r + (c.r - a.r) * k, sway: a.sway + (c.sway - a.sway) * k, dir: V.norm(V.sub(c.p, a.p)) };
}

/** Child direction: `angle` away from the parent around a roll that prefers up/outward. */
function childDir(parent, angle, roll, outward) {
  const side = V.rot(anyPerp(parent), parent, roll);
  let d = V.rot(parent, V.norm(V.cross(parent, side)), angle);
  if (outward) d = V.norm(V.add(d, V.mul(outward, 0.35)));
  return d;
}

export function deciduous(sp, seed) {
  const r = rng(seed), H = sp.height, R = sp.radius, y0 = sp.crownBase * H, top = H * sp.leader;
  const yc = y0 + (H - y0) * (sp.crownCentre ?? 0.45), hc = Math.max(yc - y0, H - yc) * 1.02;
  const env = y => R * Math.pow(Math.max(0, 1 - ((y - yc) / hc) ** 2), sp.shape ?? 0.5);
  const branches = [];
  const trunkPts = [];
  const lean = [r.range(-1, 1) * sp.lean * H, 0, r.range(-1, 1) * sp.lean * H];
  for (let i = 0; i <= 10; i++) {
    const t = i / 10, y = t * top;
    const wob = [Math.sin(t * 5 + seed) * 0.012 * H * t, 0, Math.cos(t * 4 + seed) * 0.012 * H * t];
    const flare = 1 + 0.45 * Math.exp(-y / 0.35);
    trunkPts.push({ p: V.add([lean[0] * t * t, y, lean[2] * t * t], wob), r: sp.trunk * (1 - 0.82 * Math.pow(t, 1.15)) * flare, sway: 0.25 * t * t });
  }
  const trunk = { level: 0, pts: trunkPts, length: top, phase: r() };
  branches.push(trunk);
  const trunkAt = y => at(trunk, Math.min(1, Math.max(0, y / top)));
  const n1 = sp.n1;
  for (let i = 0; i < n1; i++) {
    const f = (i + r.range(0, 0.8)) / n1, y = y0 + f * (top * 0.96 - y0);
    const az = i * 2.39996 + r.range(-0.3, 0.3);
    const e = (sp.elev[0] + (sp.elev[1] - sp.elev[0]) * f + r.range(-8, 8)) * Math.PI / 180;
    const dir = [Math.cos(e) * Math.cos(az), Math.sin(e), Math.cos(e) * Math.sin(az)];
    const reach = env(y + Math.sin(e) * env(y) * 0.6);
    const length = Math.max(0.5, reach / Math.max(0.35, Math.cos(e)) * r.range(0.82, 1.05));
    const base = trunkAt(y);
    const r0 = Math.max(0.02, base.r * sp.ratio * Math.min(1, Math.sqrt(length / R)));
    branches.push(branch(1, base.p, dir, length, r0, r0 * 0.22, 7, { up: sp.up1 ?? 0.05, droop: sp.droop1 ?? 0.05, noise: sp.noise ?? 0.12, sway: 0.09 }, r, base.sway, r()));
  }
  const level1 = branches.filter(b => b.level === 1);
  for (const b of level1) {
    const n2 = Math.max(2, Math.round(b.length * sp.d2));
    for (let k = 0; k < n2; k++) {
      const t = 0.22 + 0.74 * (k + r.range(0, 0.7)) / n2, q = at(b, t);
      const outward = V.norm([q.p[0], 0.25, q.p[2]]);
      const dir = childDir(q.dir, (sp.a2 + r.range(-12, 12)) * Math.PI / 180, (k % 2 ? 1 : -1) * r.range(1.0, 2.0), outward);
      const length = Math.min(sp.l2max, (b.length * (1 - t)) * sp.l2ratio + 0.35) * r.range(0.7, 1.1);
      branches.push(branch(2, q.p, dir, length, Math.max(0.008, q.r * 0.62), 0.006, 4, { up: sp.up2 ?? 0.08, droop: sp.droop2 ?? 0.15, noise: 0.18, sway: 0.13 }, r, q.sway, b.phase));
    }
  }
  const level2 = branches.filter(b => b.level === 2);
  for (const b of level2) {
    const n3 = Math.round(b.length * sp.d3);
    for (let k = 0; k < n3; k++) {
      const t = 0.3 + 0.65 * (k + r()) / Math.max(1, n3), q = at(b, t);
      const dir = childDir(q.dir, r.range(30, 55) * Math.PI / 180, r.range(0, Math.PI * 2), null);
      branches.push(branch(3, q.p, dir, r.range(0.25, 0.55) * (sp.l3 ?? 1), 0.006, 0.003, 2, { up: 0.05, droop: sp.droop3 ?? 0.2, noise: 0.2, sway: 0.2 }, r, q.sway, b.phase));
    }
  }
  // Leaf card anchors: along branchlets and twigs, denser towards the outer crown.
  const anchors = [];
  for (const b of branches.filter(b => b.level >= 2)) {
    const step = sp.cardSpacing * (b.level === 3 ? 1.2 : 1);
    for (let s = b.length * (b.level === 3 ? 0.35 : 0.3); s <= b.length + 1e-6; s += step) anchors.push({ ...at(b, Math.min(1, s / b.length)), phase: b.phase });
    anchors.push({ ...at(b, 1), phase: b.phase });
  }
  const centre = [0, yc, 0];
  return { branches, anchors: thin(anchors, sp.cards, r), centre, crown: { y0, yc, hc, R }, height: H, radius: R };
}

/** Keep at most n anchors, preferring the outer crown. */
/**
 * Keep at most n anchors: the same share of every 1/10 height band of the crown (no bare lower crown, no hollow
 * top), and within a band the ones further out from the trunk (the visible shell).
 */
function thin(anchors, n, r) {
  if (anchors.length <= n) return anchors;
  let lo = Infinity, hi = -Infinity;
  for (const a of anchors) { lo = Math.min(lo, a.p[1]); hi = Math.max(hi, a.p[1]); }
  const bands = Array.from({ length: 10 }, () => []);
  for (const a of anchors) bands[Math.min(9, Math.floor((a.p[1] - lo) / Math.max(1e-6, hi - lo) * 10))].push(a);
  const kept = [];
  for (const band of bands) {
    if (!band.length) continue;
    const reach = Math.max(...band.map(a => Math.hypot(a.p[0], a.p[2]))) || 1;
    const quota = Math.round(n * band.length / anchors.length);
    kept.push(...band.map(a => ({ a, s: r() + 0.45 * Math.hypot(a.p[0], a.p[2]) / reach })).sort((x, y) => y.s - x.s).slice(0, quota).map(x => x.a));
  }
  return kept;
}

export function conifer(sp, seed) {
  const r = rng(seed), H = sp.height, R = sp.radius, y0 = sp.crownBase * H;
  const branches = [];
  const trunkPts = [];
  for (let i = 0; i <= 12; i++) {
    const t = i / 12, y = t * H * 0.98;
    trunkPts.push({ p: [Math.sin(t * 3 + seed) * 0.01 * H * t, y, Math.cos(t * 2.5 + seed) * 0.01 * H * t], r: sp.trunk * (1 - 0.92 * t) * (1 + 0.4 * Math.exp(-y / 0.4)), sway: 0.25 * t * t });
  }
  const trunk = { level: 0, pts: trunkPts, length: H, phase: r() };
  branches.push(trunk);
  const env = sp.kind === 'pine'
    ? y => R * Math.pow(Math.max(0, Math.sin(Math.PI * Math.min(1, (y - y0) / (H - y0)) * 0.92 + 0.12)), 0.7)
    : y => R * Math.pow(Math.max(0, 1 - (y - y0) / (H * 1.02 - y0)), 0.95) + 0.15;
  for (let y = y0; y < H - 0.3; y += sp.whorl * r.range(0.8, 1.25)) {
    const n = r.int(sp.perWhorl[0], sp.perWhorl[1]), az0 = r() * Math.PI * 2, f = (y - y0) / (H - y0);
    for (let k = 0; k < n; k++) {
      const az = az0 + k / n * Math.PI * 2 + r.range(-0.3, 0.3);
      const e = (sp.elev[0] + (sp.elev[1] - sp.elev[0]) * f + r.range(-6, 6)) * Math.PI / 180;
      const length = Math.max(0.25, env(y) * r.range(0.85, 1.08) / Math.max(0.4, Math.cos(e)));
      const base = at(trunk, y / (H * 0.98));
      const r0 = Math.max(0.012, base.r * sp.ratio * Math.min(1, Math.sqrt(length / R)) + 0.01);
      branches.push(branch(1, base.p, [Math.cos(e) * Math.cos(az), Math.sin(e), Math.cos(e) * Math.sin(az)], length, r0, r0 * 0.3, 6,
        { up: sp.up1, droop: sp.droop1, noise: sp.noise ?? 0.08, sway: 0.1 }, r, base.sway, r()));
    }
  }
  for (const b of branches.filter(b => b.level === 1)) {
    const n2 = Math.round(b.length * sp.d2);
    for (let k = 0; k < n2; k++) {
      const t = 0.15 + 0.8 * (k + r()) / Math.max(1, n2), q = at(b, t);
      // Branchlets fan out in the branch's horizontal plane and hang slightly.
      const side = V.norm(V.cross(q.dir, UP)), sign = k % 2 ? 1 : -1;
      const dir = V.norm(V.add(V.add(V.mul(q.dir, 0.75), V.mul(side, sign * 0.75)), [0, -sp.hang, 0]));
      const length = Math.max(0.2, (b.length * (1 - t)) * sp.l2ratio + 0.25) * r.range(0.75, 1.1);
      branches.push(branch(2, q.p, dir, length, Math.max(0.006, q.r * 0.55), 0.004, 3, { up: 0.02, droop: sp.droop2, noise: 0.15, sway: 0.15 }, r, q.sway, b.phase));
    }
  }
  const anchors = [];
  const tipsOnly = sp.kind === 'pine';
  for (const b of branches.filter(b => b.level >= 1)) {
    if (tipsOnly) {
      for (const t of b.level === 1 ? [0.55, 0.7, 0.85, 1] : [0.35, 0.55, 0.8, 1]) for (let c = 0; c < (t === 1 ? 4 : 2); c++) anchors.push({ ...at(b, t), phase: b.phase, tuft: true });
      continue;
    }
    const step = sp.cardSpacing;
    for (let s = b.length * (b.level === 1 ? 0.25 : 0.1); s <= b.length + 1e-6; s += step) anchors.push({ ...at(b, Math.min(1, s / b.length)), phase: b.phase });
  }
  const yc = (y0 + H) / 2;
  return { branches, anchors: thin(anchors, sp.cards, r), centre: [0, yc, 0], crown: { y0, yc, hc: (H - y0) / 2, R }, height: H, radius: R, conifer: true };
}

export function shrub(sp, seed) {
  const r = rng(seed), H = sp.height, R = sp.radius, branches = [];
  for (let i = 0; i < sp.stems; i++) {
    const az = i * 2.39996 + r.range(-0.4, 0.4), a = r.range(0, sp.base);
    const start = [Math.cos(az) * a, 0, Math.sin(az) * a];
    const e = r.range(sp.elev[0], sp.elev[1]) * Math.PI / 180;
    const length = H / Math.sin(e) * r.range(0.8, 1.05);
    branches.push(branch(1, start, [Math.cos(e) * Math.cos(az), Math.sin(e), Math.cos(e) * Math.sin(az)], length, sp.stem, sp.stem * 0.3, 6,
      { up: sp.up ?? 0, droop: sp.droop ?? 0.2, noise: 0.15, sway: 0.25 }, r, 0, r()));
  }
  for (const b of branches.filter(b => b.level === 1)) {
    const n2 = Math.round(b.length * sp.d2);
    for (let k = 0; k < n2; k++) {
      const t = 0.35 + 0.6 * (k + r()) / Math.max(1, n2), q = at(b, t);
      const dir = childDir(q.dir, r.range(30, 60) * Math.PI / 180, r.range(0, Math.PI * 2), V.norm([q.p[0], 0.4, q.p[2]]));
      branches.push(branch(2, q.p, dir, r.range(0.25, 0.7) * sp.l2, Math.max(0.005, q.r * 0.6), 0.003, 3, { up: 0.05, droop: 0.2, noise: 0.2, sway: 0.3 }, r, q.sway, b.phase));
    }
  }
  const anchors = [];
  for (const b of branches) for (let s = b.length * sp.from; s <= b.length + 1e-6; s += sp.cardSpacing) anchors.push({ ...at(b, Math.min(1, s / b.length)), phase: b.phase });
  return { branches, anchors: thin(anchors, sp.cards, r), centre: [0, H * 0.55, 0], crown: { y0: 0, yc: H * 0.55, hc: H * 0.55, R }, height: H, radius: R };
}

// ---------------------------------------------------------------- meshing

function tube(mesh, b, segs, slot, tint, aoFn, vOffset, maxRings) {
  const [u0, u1] = barkU(slot);
  const circ = 2 * Math.PI * b.pts[0].r;
  const uSpan = Math.min(1, Math.max(0.3, circ));
  // Resample the skeleton to at most maxRings rings (cheaper LODs keep the same shape with fewer rings).
  const n = Math.min(b.pts.length, maxRings);
  const pts = Array.from({ length: n }, (_, i) => b.pts[Math.round(i * (b.pts.length - 1) / (n - 1))]);
  let frameN = anyPerp(V.norm(V.sub(pts[1].p, pts[0].p)));
  let v = vOffset, prev = null;
  const rings = [];
  for (let i = 0; i < pts.length; i++) {
    const pt = pts[i];
    const d = V.norm(V.sub(pts[Math.min(i + 1, pts.length - 1)].p, pts[Math.max(0, i - 1)].p));
    frameN = V.norm(V.sub(frameN, V.mul(d, V.dot(frameN, d))));
    const frameB = V.cross(d, frameN);
    if (prev) v += V.len(V.sub(pt.p, prev)) / 1.0;
    prev = pt.p;
    const ring = [];
    for (let j = 0; j <= segs; j++) {
      const a = j / segs * Math.PI * 2;
      const n = V.add(V.mul(frameN, Math.cos(a)), V.mul(frameB, Math.sin(a)));
      const p = V.add(pt.p, V.mul(n, pt.r));
      const ao = aoFn(p);
      const u = u0 + (u1 - u0) * (j / segs) * uSpan;
      const c = typeof tint === 'function' ? tint(p) : tint;
      ring.push(mesh.vertex(p, n, [u, v], [c[0] * ao, c[1] * ao, c[2] * ao], [Math.min(1, pt.sway), 0, b.phase, 0]));
    }
    rings.push(ring);
  }
  for (let i = 0; i < rings.length - 1; i++) for (let j = 0; j < segs; j++) {
    const a = rings[i][j], bb = rings[i][j + 1], c = rings[i + 1][j], d = rings[i + 1][j + 1];
    mesh.tri(a, c, bb); mesh.tri(bb, c, d);
  }
}

/**
 * One leaf card: pivot at its base, `up` the card axis, `normal` its facing; folded along its axis when `fold`.
 * Vertex normals bend towards the crown's radial direction so the canopy shades as a volume.
 */
function card(mesh, cell, pivot, up, facing, size, fold, radialAt, ao, phase, sway, aspect = 1) {
  const side = V.norm(V.cross(up, facing)), w = size * aspect, h = size;
  const normal = V.norm(V.cross(side, up));
  const uv = cellUV(cell);
  const pts = fold
    ? [[-0.5, 0], [0, 0], [0.5, 0], [-0.5, 1], [0, 1], [0.5, 1]]
    : [[-0.5, 0], [0.5, 0], [-0.5, 1], [0.5, 1]];
  const ids = pts.map(([x, y]) => {
    const bulge = fold && x === 0 ? 0.16 * w : 0;
    const p = V.add(V.add(V.add(pivot, V.mul(side, x * w)), V.mul(up, y * h)), V.mul(normal, bulge - (fold ? 0.05 * w : 0)));
    const n = V.norm(V.add(V.mul(normal, 0.2), V.mul(radialAt(p), 0.8)));
    const shade = ao(p);
    return mesh.vertex(p, n, [uv.u0 + (uv.u1 - uv.u0) * (x + 0.5), uv.v1 + (uv.v0 - uv.v1) * y], [shade, shade, shade], [Math.min(1, sway), y, phase, 1]);
  });
  if (fold) { mesh.tri(ids[0], ids[1], ids[3]); mesh.tri(ids[1], ids[4], ids[3]); mesh.tri(ids[1], ids[2], ids[4]); mesh.tri(ids[2], ids[5], ids[4]); }
  else { mesh.tri(ids[0], ids[1], ids[2]); mesh.tri(ids[1], ids[3], ids[2]); }
}

/** Mesh a skeleton at one LOD. */
export function meshTree(sk, sp, lod, seed) {
  const r = rng(seed + 1000 * lod), bark = new MeshBuilder(), leaves = new MeshBuilder();
  const { yc, hc, R } = sk.crown;
  const crownDepth = p => Math.min(1.4, Math.hypot(p[0] / R, (p[1] - yc) / hc, p[2] / R));
  const ao = p => (0.18 + 0.82 * Math.min(1, Math.pow(crownDepth(p), 2.2))) * (0.82 + 0.18 * Math.min(1, Math.max(0, (p[1] - sk.crown.y0) / (2 * hc))));
  const barkAo = p => p[1] < sk.crown.y0 ? 1 : 0.75 + 0.25 * Math.min(1, crownDepth(p));
  const radial = sk.conifer
    ? p => V.norm([p[0], (p[1] - yc) * 0.25 + 0.35 * R, p[2]])
    : p => V.norm([p[0], (p[1] - yc) * (R / hc), p[2]]);
  // Radial segments, rings and minimum base radius per branch level. Thin branchlets live in the card textures.
  const segs = lod === 0 ? sp.segs ?? [10, 6, 3, 0] : [6, 4, 3, 0];
  const rings = lod === 0 ? [11, 4, 3, 2] : [6, 3, 2, 2];
  const minR = lod === 0 ? [0, 0.014, 0.022, 1] : [0, 0.03, 0.045, 1];
  let v = 0;
  for (const b of sk.branches) {
    if (!segs[b.level] || b.pts[0].r < minR[b.level]) continue;
    tube(bark, b, segs[b.level], sp.bark, sp.barkTint ?? [1, 1, 1], barkAo, v, rings[b.level]);
    v += 0.37;
  }
  // The reduced LOD keeps the whole canopy as flat cards (2 triangles instead of 4) and saves on bark: the crown's
  // silhouette and density do not change at the switch. Trees with very many cards keep a large share at a larger size.
  const keep = lod === 0 ? 1 : sp.lod1Keep ?? 1;
  const anchors = keep === 1 ? sk.anchors : sk.anchors.filter(() => r() < keep);
  const grow = 1 / Math.sqrt(keep);
  for (const a of anchors) {
    const out = radial(a.p);
    let up;
    if (a.tuft) up = V.norm(V.add(V.add(a.dir, V.mul(out, 0.6)), [r.range(-0.6, 0.6), r.range(0.0, 0.8), r.range(-0.6, 0.6)]));
    else up = V.norm(V.add(V.add(V.mul(a.dir, sp.cardAlong ?? 0.6), V.mul(out, sp.cardOut ?? 0.6)), [r.range(-0.25, 0.25), (sp.cardUp ?? 0.2) + r.range(-0.2, 0.2), r.range(-0.25, 0.25)]));
    // Facing: outwards, tipped towards the sky in the upper crown (with a flatter card axis) so the canopy also reads
    // from above; then rolled randomly around the card axis.
    const sky = r.range(0, sp.cardSky ?? 1.2) * Math.min(1, Math.max(0, (a.p[1] - sk.crown.y0) / (2 * hc) + 0.2));
    if (!a.tuft && !sp.flatCards) up = V.norm(V.sub(up, V.mul(UP, V.dot(up, UP) * Math.min(0.8, sky * 0.6))));
    let facing = V.add(out, V.mul(UP, sky));
    facing = V.sub(facing, V.mul(up, V.dot(facing, up)));
    if (V.len(facing) < 0.1) facing = anyPerp(up);
    facing = V.rot(V.norm(facing), up, r.range(-1, 1) * (sp.cardRoll ?? 0.9));
    if (sp.flatCards) facing = V.norm(V.lerp(facing, UP, sp.flatCards));
    const size = sp.card * r.range(0.8, 1.15) * grow;
    card(leaves, sp.cell, a.p, up, facing, size, lod === 0 && sp.fold !== false, radial, ao, a.phase, a.sway + 0.12, sp.cardAspect ?? 1);
  }
  return { bark, leaves };
}

// ---------------------------------------------------------------- species

const tree = o => ({ kind: 'deciduous', segs: [10, 6, 3, 0], lod1Keep: 0.55, ...o });
export const SPECIES = {
  birch: tree({ cell: 'birch', bark: 'birch', height: 15, radius: 3.2, trunk: 0.17, crownBase: 0.28, leader: 0.95, crownCentre: 0.5, shape: 0.6, lean: 0.015,
    n1: 30, elev: [38, 62], ratio: 0.42, d2: 3.6, a2: 42, l2ratio: 0.55, l2max: 2.4, d3: 2.2, droop2: 0.55, droop3: 0.9, card: 1.0, cardSpacing: 0.26, cards: 1150, cardUp: -0.35, cardAlong: 0.9, cardOut: 0.35 }),
  linden: tree({ cell: 'linden', bark: 'linden', height: 16, radius: 4.6, trunk: 0.3, crownBase: 0.22, leader: 0.9, crownCentre: 0.45, shape: 0.55, lean: 0.01,
    n1: 30, elev: [28, 58], ratio: 0.5, d2: 3.2, a2: 40, l2ratio: 0.5, l2max: 2.6, d3: 2.0, droop2: 0.12, card: 1.2, cardSpacing: 0.26, cards: 1350 }),
  maple: tree({ cell: 'maple', bark: 'maple', height: 14, radius: 4.8, trunk: 0.28, crownBase: 0.22, leader: 0.82, crownCentre: 0.46, shape: 0.45, lean: 0.02,
    n1: 26, elev: [30, 55], ratio: 0.55, d2: 3.0, a2: 42, l2ratio: 0.55, l2max: 2.6, d3: 2.0, droop2: 0.1, card: 1.25, cardSpacing: 0.26, cards: 1250 }),
  oak: tree({ cell: 'oak', bark: 'oak', height: 14, radius: 5.6, trunk: 0.38, crownBase: 0.25, leader: 0.72, crownCentre: 0.42, shape: 0.4, lean: 0.03,
    n1: 22, elev: [12, 42], ratio: 0.62, d2: 3.0, a2: 50, l2ratio: 0.55, l2max: 2.8, d3: 2.2, noise: 0.35, droop1: 0.06, droop2: 0.08, card: 1.2, cardSpacing: 0.26, cards: 1350 }),
  rowan: tree({ cell: 'rowan', bark: 'smooth', height: 8, radius: 2.6, trunk: 0.13, crownBase: 0.3, leader: 0.88, crownCentre: 0.5, shape: 0.55, lean: 0.03,
    n1: 18, elev: [40, 68], ratio: 0.5, d2: 3.2, a2: 40, l2ratio: 0.5, l2max: 1.6, d3: 1.5, droop2: 0.15, card: 1.0, cardSpacing: 0.26, cards: 700 }),
  willow: tree({ cell: 'willow', bark: 'willow', height: 12, radius: 5.0, trunk: 0.38, crownBase: 0.22, leader: 0.7, crownCentre: 0.5, shape: 0.5, lean: 0.04,
    n1: 22, elev: [45, 70], ratio: 0.55, d2: 2.6, a2: 25, l2ratio: 0.85, l2max: 4.5, d3: 1.2, up1: 0.04, droop1: 0.22, droop2: 1.6, droop3: 1.4, card: 1.3, lod1Keep: 0.55, cardSpacing: 0.24, cards: 1150,
    cardUp: -1.4, cardAlong: 1.0, cardOut: 0.15, cardAspect: 0.95, cardRoll: 1.3 }),
  pine: { kind: 'pine', cell: 'pine', bark: 'pine', height: 17, radius: 3.6, trunk: 0.24, crownBase: 0.58, whorl: 0.75, perWhorl: [3, 5], elev: [8, 38], ratio: 0.4,
    up1: 0.12, droop1: 0.04, noise: 0.18, d2: 2.6, l2ratio: 0.45, hang: 0.0, droop2: 0.05, card: 1.35, cards: 680, lod1Keep: 0.6, segs: [10, 5, 3, 0],
    barkTint: p => p[1] > 7 ? [1.25, 0.86, 0.62] : [1, 1, 1] },
  spruce: { kind: 'spruce', cell: 'spruce', bark: 'spruce', height: 17, radius: 3.0, trunk: 0.25, crownBase: 0.03, lod1Keep: 0.6, whorl: 0.55, perWhorl: [4, 5], elev: [-22, 32], ratio: 0.35,
    up1: 0.18, droop1: 0.12, noise: 0.06, d2: 3.2, l2ratio: 0.45, hang: 0.55, droop2: 0.6, card: 1.3, cardSpacing: 0.3, cards: 1150, segs: [10, 5, 3, 0],
    cardAlong: 1.0, cardOut: 0.35, cardUp: -0.15, flatCards: 0.55, cardRoll: 0.5 },
  lilac: { kind: 'shrub', cell: 'lilac', bark: 'smooth', height: 3.4, radius: 1.8, stems: 8, base: 0.3, elev: [55, 82], stem: 0.035, droop: 0.12, d2: 2.4, l2: 1.0,
    from: 0.35, card: 0.55, cardSpacing: 0.26, cards: 520, segs: [6, 5, 3, 3] },
  rose: { kind: 'shrub', cell: 'rose', bark: 'smooth', height: 1.3, radius: 0.9, stems: 13, base: 0.2, elev: [45, 80], stem: 0.012, droop: 0.55, d2: 2.0, l2: 0.6,
    from: 0.2, card: 0.4, cardSpacing: 0.16, cards: 380, segs: [5, 4, 3, 3], barkTint: [0.75, 0.85, 0.6] },
  juniper: { kind: 'shrub', cell: 'juniper', bark: 'spruce', height: 3.0, radius: 0.6, stems: 5, base: 0.08, elev: [80, 88], stem: 0.05, up: 0.02, droop: 0, d2: 7, l2: 0.9,
    from: 0.05, card: 0.5, cardSpacing: 0.18, cards: 520, segs: [6, 5, 3, 3], cardUp: 0.9, cardOut: 0.5, cardAlong: 0.3 },
};
export const TREES = ['birch', 'linden', 'maple', 'oak', 'rowan', 'willow', 'pine', 'spruce'];
export const SHRUBS = ['lilac', 'rose', 'juniper'];

export function skeleton(name, seed) {
  const sp = SPECIES[name];
  if (sp.kind === 'deciduous') return deciduous(sp, seed);
  if (sp.kind === 'shrub') return shrub(sp, seed);
  return conifer(sp, seed);
}

// ---------------------------------------------------------------- hedge, grass, ground cover

/** 2 m hedge module (x -1..1), 1.2 m tall, 0.8 m deep: an opaque leafy core box plus outward cards. */
export function hedge(seed, lod) {
  const r = rng(seed), leaves = new MeshBuilder(), bark = new MeshBuilder();
  const L = 2.0, H = 1.2, D = 0.8, uv = cellUV('hedge');
  // Core box with UVs inside the dense lower-centre of the hedge card (opaque there), faces shaded darker.
  const core = { x: L / 2, y0: 0, y1: H - 0.16, z: D / 2 - 0.12 };
  const cu = [uv.u0 + (uv.u1 - uv.u0) * 0.38, uv.u0 + (uv.u1 - uv.u0) * 0.62], cv = [uv.v0 + (uv.v1 - uv.v0) * 0.78, uv.v0 + (uv.v1 - uv.v0) * 0.95];
  const face = (corners, n) => {
    const ids = corners.map((p, i) => leaves.vertex(p, n, [cu[i % 2], cv[i < 2 ? 1 : 0]], [0.55, 0.55, 0.55], [0.05, 0, 0, 1]));
    leaves.tri(ids[0], ids[1], ids[2]); leaves.tri(ids[1], ids[3], ids[2]);
  };
  const { x, y0, y1, z } = core;
  face([[-x, y0, z], [x, y0, z], [-x, y1, z], [x, y1, z]], [0, 0, 1]);
  face([[x, y0, -z], [-x, y0, -z], [x, y1, -z], [-x, y1, -z]], [0, 0, -1]);
  face([[-x, y1, z], [x, y1, z], [-x, y1, -z], [x, y1, -z]], [0, 1, 0]);
  const count = lod === 0 ? 300 : 110, size = lod === 0 ? 0.48 : 0.7;
  for (let i = 0; i < count; i++) {
    // Sample the rounded box surface: sides and top.
    const sideTop = r();
    let p, n;
    if (sideTop < 0.32) { p = [r.range(-L / 2, L / 2), H - 0.22 + r.range(-0.05, 0.05), r.range(-D / 2 + 0.1, D / 2 - 0.1)]; n = [r.range(-0.3, 0.3), 1, r.range(-0.3, 0.3)]; }
    else { const s = r() < 0.5 ? 1 : -1; p = [r.range(-L / 2, L / 2), r.range(0.05, H - 0.35), s * (D / 2 - 0.18)]; n = [r.range(-0.3, 0.3), r.range(-0.1, 0.5), s]; }
    n = V.norm(n);
    const up = V.norm(V.add(n, [r.range(-0.5, 0.5), 0.6, r.range(-0.5, 0.5)]));
    let facing = V.sub(n, V.mul(up, V.dot(n, up)));
    facing = V.len(facing) < 0.1 ? anyPerp(up) : V.norm(facing);
    const ao = 0.62 + 0.38 * Math.min(1, p[1] / H + 0.25);
    card(leaves, 'hedge', V.sub(p, V.mul(n, 0.05)), up, facing, size * r.range(0.8, 1.15), lod === 0, () => n, () => ao, r(), 0.15 + 0.2 * p[1] / H);
  }
  return { bark, leaves, height: H, radius: L / 2 };
}

/** Crossed-card clumps for grass and meadow, a flat rosette for ground cover. */
export function clump(kind, seed) {
  const r = rng(seed), leaves = new MeshBuilder();
  const spec = { grass: { cell: 'grass', w: 0.55, h: 0.42, planes: 3 }, lawn: { cell: 'lawn', w: 1.25, h: 0.36, planes: 3, crop: 0.36 },
    meadow: { cell: 'meadow', w: 0.7, h: 0.75, planes: 3 }, rosette: { cell: 'rosette', w: 0.42, flat: true } }[kind];
  const uv = cellUV(spec.cell);
  if (spec.flat) {
    const s = spec.w / 2, y = 0.02;
    const ids = [[-s, -s], [s, -s], [-s, s], [s, s]].map(([x, z], i) => leaves.vertex([x, y + (i === 3 ? 0.01 : 0), z], [0, 1, 0],
      [x < 0 ? uv.u0 : uv.u1, z < 0 ? uv.v0 : uv.v1], [1, 1, 1], [0.05, 0.3, r(), 1]));
    leaves.tri(ids[0], ids[2], ids[1]); leaves.tri(ids[1], ids[2], ids[3]);
    return { leaves, bark: new MeshBuilder(), height: 0.05, radius: s };
  }
  for (let k = 0; k < spec.planes; k++) {
    const a = k / spec.planes * Math.PI + r.range(-0.2, 0.2), dx = Math.cos(a) * spec.w / 2, dz = Math.sin(a) * spec.w / 2;
    const ids = [];
    for (const [sx, y] of [[-1, 0], [1, 0], [-1, 1], [1, 1]]) {
      const tilt = y ? r.range(-0.06, 0.06) : 0;
      const p = [sx * dx + tilt, y * spec.h - (y ? 0 : 0.03), sx * dz + tilt];
      // Grass lights mostly from above: bend normals up so both sides of the card read alike.
      const n = V.norm([Math.sin(a) * 0.25 * sx, 1, -Math.cos(a) * 0.25 * sx]);
      const shade = y ? 1 : 0.55;
      const top = uv.v1 + (uv.v0 - uv.v1) * (spec.crop ?? 1);
      ids.push(leaves.vertex(p, n, [sx < 0 ? uv.u0 : uv.u1, y ? top : uv.v1], [shade, shade, shade], [0.15 * y, y, r(), 1]));
    }
    leaves.tri(ids[0], ids[1], ids[2]); leaves.tri(ids[1], ids[3], ids[2]);
  }
  return { leaves, bark: new MeshBuilder(), height: spec.h, radius: spec.w / 2 };
}
