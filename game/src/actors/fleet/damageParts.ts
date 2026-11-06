import * as THREE from 'three/webgpu';
import { PART_COUNT, PART_STATE, PARTS, WINDOWS, type DamageLayout, type DamageState, type PartName, type WindowName } from '../../vehicle/damage';

// The split is the same rule for every fleet model and is checked per model (`splitReport`): a part that comes out
// implausible stays on the body, so that model's damage keeps its deformation and wear there (vehicle/README.md
// "Body damage across the fleet").

// Loose and detached body parts (vehicle/README.md "Loose and detached parts"). Each damage-capable body is split by
// region, once per geometry, into the parts of vehicle/damage.ts PARTS: the same rule for every fleet model, from its
// damage box. A `damagePart` vertex attribute (x: part index + 1, 0 the body) picks the part's rigid transform in the
// damage pool (damageRig.ts) before the cage blend: identity while attached, a swing about its remaining mount while
// loose, collapsed to a point once detached (debris.ts then draws a copy of it as a physics body).

/** Rest-frame bounds of a part, and the triangles (index triples into its geometry) it owns. */
export interface PartInfo { readonly name: PartName; readonly min: THREE.Vector3; readonly max: THREE.Vector3; readonly triangles: Uint32Array }
/** Per PARTS index: the part, or null when the model has none there (a body style without a boot lid, no mirrors). */
export type ModelParts = readonly (PartInfo | null)[];
const byGeometry = new WeakMap<THREE.BufferGeometry, ModelParts>();
/** The parts of a body geometry split by `splitParts` (shared by every clone of it). */
export const partsOf = (geometry: THREE.BufferGeometry): ModelParts | undefined => byGeometry.get(geometry);
/** `to` draws `from`'s split attributes with an index of its own (a subset): the same parts. */
export function shareParts(from: THREE.BufferGeometry, to: THREE.BufferGeometry) { const parts = byGeometry.get(from); if (parts) byGeometry.set(to, parts); }

/** Regions as fractions of the damage box: `f` along the length from the rear, `h` up the height, `a` out from the
 *  middle to the side. Bumpers: the lower 45 % of the end 17 % of the length. Bonnet: the up-facing panel ahead of
 *  the windscreen base (the front 30 %) inside the wings. Boot lid: the rear panel between the bumper line and the
 *  rear window (hatchback tailgates keep their glazed upper half on the body). Mirrors: small pieces sticking out past
 *  93 % of the half-width near the A-pillars (the box spans the mirrors). */
const REGION = { bumperH: .45, bumperF: .17, bonnetF: .7, bonnetUp: .3, panelA: .78, bootF: .16, bootH: [.42, .72],
  mirrorA: .93, mirrorF: [.5, .8], mirrorH: [.5, .85], mirrorSize: .45 } as const;
/** A connected piece goes to a part when this share of its area lies in that part's region and its bounds stay within
 *  the region and `MARGIN` (m): authored panels stay whole, and pieces reaching past (wheel-arch liners, the side
 *  shell, the scuttle) stay on the body. Only a fused shell spanning `SHELL` of the length is cut by region,
 *  triangle by triangle. */
const MAJORITY = .7, MARGIN = .1, SHELL = .8;
const PART_ID = Object.fromEntries(PARTS.map((name, i) => [name, i + 1])) as Record<PartName, number>;

function region(layout: DamageLayout, x: number, y: number, z: number, ny: number): number {
  const f = (z - layout.min[2]) / layout.length, h = (y - layout.min[1]) / layout.height;
  const a = Math.abs(x - (layout.min[0] + layout.max[0]) / 2) / (layout.width / 2);
  if (h < REGION.bumperH && f > 1 - REGION.bumperF) return PART_ID.frontBumper;
  if (h < REGION.bumperH && f < REGION.bumperF) return PART_ID.rearBumper;
  if (f > REGION.bonnetF && h >= REGION.bumperH - .03 && a < REGION.panelA && ny > REGION.bonnetUp) return PART_ID.bonnet;
  if (f < REGION.bootF && h >= REGION.bootH[0] && h < REGION.bootH[1] && a < REGION.panelA) return PART_ID.bootLid;
  return 0;
}
/** Whether a piece's bounds stay inside a part's region (plus MARGIN). */
function contained(layout: DamageLayout, part: number, box: THREE.Box3): boolean {
  const { min, max, length, width, height } = layout, cx = (min[0] + max[0]) / 2, side = Math.max(box.max.x - cx, cx - box.min.x);
  const below = (y: number, f: number) => y <= min[1] + f * height + MARGIN, above = (y: number, f: number) => y >= min[1] + f * height - MARGIN;
  if (part === PART_ID.frontBumper) return box.min.z >= max[2] - REGION.bumperF * length - MARGIN && below(box.max.y, REGION.bumperH);
  if (part === PART_ID.rearBumper) return box.max.z <= min[2] + REGION.bumperF * length + MARGIN && below(box.max.y, REGION.bumperH);
  if (part === PART_ID.bonnet) return box.min.z >= min[2] + REGION.bonnetF * length - MARGIN && above(box.min.y, REGION.bumperH - .03)
    && side <= REGION.panelA * width / 2 + MARGIN;
  if (part === PART_ID.bootLid) return box.max.z <= min[2] + REGION.bootF * length + MARGIN && above(box.min.y, REGION.bootH[0])
    && below(box.max.y, REGION.bootH[1]) && side <= REGION.panelA * width / 2 + MARGIN;
  return false;
}

/** Connected pieces of a geometry: by shared triangle corners, and optionally also by coincident positions. */
function pieces(position: THREE.BufferAttribute | THREE.InterleavedBufferAttribute, index: ArrayLike<number>, welded: boolean): Int32Array {
  const n = position.count, parent = new Int32Array(n).map((_, i) => i);
  const find = (i: number): number => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
  const union = (a: number, b: number) => { a = find(a); b = find(b); if (a !== b) parent[a] = b; };
  for (let t = 0; t < index.length; t += 3) { union(index[t], index[t + 1]); union(index[t], index[t + 2]); }
  if (welded) {
    const seen = new Map<string, number>();
    for (let i = 0; i < n; i++) {
      const key = `${Math.round(position.getX(i) * 1000)},${Math.round(position.getY(i) * 1000)},${Math.round(position.getZ(i) * 1000)}`;
      const other = seen.get(key);
      if (other === undefined) seen.set(key, i); else union(i, other);
    }
  }
  for (let i = 0; i < n; i++) parent[i] = find(i);
  return parent;
}

/** Splits a body geometry into its parts (once per geometry): classifies triangles by region, keeps connected pieces
 *  whole, duplicates vertices shared across a part boundary (so a swinging part never stretches a triangle to the
 *  body) and adds `damagePart`. Lamps stay on the body. Returns false when it was done before. */
export function splitParts(geometry: THREE.BufferGeometry, layout: DamageLayout): boolean {
  if (geometry.getAttribute('damagePart')) return false;
  const position = geometry.getAttribute('position'), normal = geometry.getAttribute('normal'), surface = geometry.getAttribute('fleetSurface');
  const n = position.count;
  let index = geometry.index?.array as ArrayLike<number> | undefined;
  if (!index) index = Uint32Array.from({ length: n }, (_, i) => i);
  const triangles = index.length / 3;
  const smooth = pieces(position, index, false), welded = smooth;
  // Per triangle: its region and area; per piece (triangle-connected: coincident positions often weld a mirror or a
  // bumper to the panel beside it): area by region and bounds.
  const own = new Uint8Array(triangles), regionOf = new Uint8Array(triangles), area = new Float32Array(triangles);
  const votes = new Map<number, Float64Array>(), bounds = new Map<number, THREE.Box3>(), smoothBounds = new Map<number, THREE.Box3>();
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), m = new THREE.Vector3();
  const lampOf = (t: number) => !!surface && surface.getW(index[t * 3]) >= .5;
  for (let t = 0; t < triangles; t++) {
    const i0 = index[t * 3], i1 = index[t * 3 + 1], i2 = index[t * 3 + 2];
    a.fromBufferAttribute(position, i0); b.fromBufferAttribute(position, i1); c.fromBufferAttribute(position, i2);
    m.copy(a).add(b).add(c).divideScalar(3);
    const ny = (normal.getY(i0) + normal.getY(i1) + normal.getY(i2)) / 3;
    own[t] = regionOf[t] = lampOf(t) ? 0 : region(layout, m.x, m.y, m.z, ny);
    area[t] = b.clone().sub(a).cross(c.clone().sub(a)).length() / 2 + 1e-9;
    const r = welded[i0];
    let v = votes.get(r);
    if (!v) { v = new Float64Array(PART_COUNT + 1); votes.set(r, v); bounds.set(r, new THREE.Box3()); }
    v[own[t]] += area[t];
    bounds.get(r)!.expandByPoint(a).expandByPoint(b).expandByPoint(c);
    let sb = smoothBounds.get(smooth[i0]);
    if (!sb) { sb = new THREE.Box3(); smoothBounds.set(smooth[i0], sb); }
    sb.expandByPoint(a).expandByPoint(b).expandByPoint(c);
  }
  // Welded pieces: whole to the majority part when contained in its region, cut by region when a whole-car shell,
  // else the body.
  const decided = new Map<number, number>();
  for (const [r, v] of votes) {
    let total = 0, best = 0;
    for (let k = 0; k < v.length; k++) { total += v[k]; if (v[k] > v[best]) best = k; }
    const box = bounds.get(r)!;
    if (best && v[best] >= MAJORITY * total && contained(layout, best, box)) decided.set(r, best);
    else if (box.max.z - box.min.z < SHELL * layout.length) decided.set(r, 0);
  }
  // Mirrors: smooth pieces sticking out at the A-pillars.
  const mirror = new Map<number, number>(), centreX = (layout.min[0] + layout.max[0]) / 2;
  for (const [r, box] of smoothBounds) {
    const mid = box.getCenter(m), f = (mid.z - layout.min[2]) / layout.length, h = (mid.y - layout.min[1]) / layout.height;
    const out = Math.max(box.max.x - centreX, centreX - box.min.x) / (layout.width / 2);
    if (box.getSize(a).length() < REGION.mirrorSize && out > REGION.mirrorA && f > REGION.mirrorF[0] && f < REGION.mirrorF[1]
      && h > REGION.mirrorH[0] && h < REGION.mirrorH[1]) mirror.set(r, mid.x > centreX ? PART_ID.mirrorLeft : PART_ID.mirrorRight);
  }
  for (let t = 0; t < triangles; t++) {
    const m = mirror.get(smooth[index[t * 3]]), d = decided.get(welded[index[t * 3]]);
    if (m !== undefined) own[t] = m;
    else if (lampOf(t)) own[t] = 0;
    else if (d !== undefined) own[t] = d;
  }
  // Vertices whose triangles fall in different parts are duplicated per extra part.
  const vertexPart = new Int16Array(n).fill(-1), copies = new Map<number, number>(), extra: number[] = [];
  const out = new Uint32Array(index.length);
  for (let t = 0; t < triangles; t++) for (let k = 0; k < 3; k++) {
    const v = index[t * 3 + k], p = own[t];
    if (vertexPart[v] < 0 || vertexPart[v] === p) { vertexPart[v] = p; out[t * 3 + k] = v; continue; }
    const key = v * 8 + p;
    let copy = copies.get(key);
    if (copy === undefined) { copy = n + extra.length; copies.set(key, copy); extra.push(v); }
    out[t * 3 + k] = copy;
  }
  const total = n + extra.length, parts = new Uint8Array(total * 4);
  for (let v = 0; v < n; v++) parts[v * 4] = Math.max(0, vertexPart[v]);
  for (const [key, copy] of copies) parts[copy * 4] = key % 8;
  if (extra.length) {
    for (const name of Object.keys(geometry.attributes)) {
      const attribute = geometry.getAttribute(name) as THREE.BufferAttribute;
      const size = attribute.itemSize, array = new (attribute.array.constructor as new (n: number) => THREE.TypedArray)(total * size);
      array.set(attribute.array as THREE.TypedArray);
      extra.forEach((v, j) => { for (let k = 0; k < size; k++) array[(n + j) * size + k] = attribute.array[v * size + k]; });
      geometry.setAttribute(name, new THREE.BufferAttribute(array, size, attribute.normalized));
    }
  }
  geometry.setIndex(new THREE.BufferAttribute(total > 65535 ? out : Uint16Array.from(out), 1));
  geometry.setAttribute('damagePart', new THREE.BufferAttribute(parts, 4));
  // Part bounds and triangles (for the hinges and the debris copies).
  const info: (PartInfo | null)[] = PARTS.map(() => null), lists = PARTS.map(() => [] as number[]);
  const finalPosition = geometry.getAttribute('position');
  for (let t = 0; t < triangles; t++) if (own[t]) lists[own[t] - 1].push(t);
  // Per part: the area of its region (every non-lamp triangle there, whichever piece it belongs to) and of the part
  // inside it, for the plausibility check.
  const regionArea = new Float64Array(PART_COUNT + 1), covered = new Float64Array(PART_COUNT + 1);
  for (let t = 0; t < triangles; t++) {
    const r = regionOf[t];
    regionArea[r] += area[t];
    if (own[t] === r) covered[r] += area[t];
  }
  const report = {} as SplitReport['parts'];
  PARTS.forEach((name, i) => {
    const triangleCount = lists[i].length;
    const min = new THREE.Vector3(Infinity, Infinity, Infinity), max = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
    for (const t of lists[i]) for (let k = 0; k < 3; k++) { a.fromBufferAttribute(finalPosition, out[t * 3 + k]); min.min(a); max.max(a); }
    const coverage = regionArea[i + 1] > 0 ? covered[i + 1] / regionArea[i + 1] : 0;
    const reason = triangleCount ? implausible(name, layout, min, max, triangleCount, coverage) : 'not found';
    report[name] = { triangles: triangleCount, coverage: Math.round(coverage * 100) / 100, kept: !reason, ...(reason ? { reason } : {}) };
    if (reason) {
      // An implausible part stays on the body: it deforms and wears with it, and never hangs loose or comes off.
      for (const t of lists[i]) for (let k = 0; k < 3; k++) parts[out[t * 3 + k] * 4] = 0;
      return;
    }
    info[i] = { name, min, max, triangles: Uint32Array.from(lists[i].flatMap(t => [out[t * 3], out[t * 3 + 1], out[t * 3 + 2]])) };
  });
  byGeometry.set(geometry, info);
  reports.set(geometry, { parts: report, fit: cageFit(geometry, layout) });
  return true;
}

/** A split part a loose swing or a debris copy would show wrongly, and why: too narrow for its role (a badge or a
 *  washer nozzle taken for a bonnet), too few triangles, or only a sliver of its region (the panel is fused to the
 *  shell, so the rest of it would stay on the car). Fractions of the damage box; null when plausible. */
export const PLAUSIBLE = {
  bumper: { width: .6, height: .06, coverage: .3 }, bonnet: { width: .5, length: .1, coverage: .35 },
  bootLid: { width: .45, height: .07, coverage: .25 }, triangles: 24, mirrorTriangles: 12,
} as const;
function implausible(name: PartName, layout: DamageLayout, min: THREE.Vector3, max: THREE.Vector3, triangles: number, coverage: number): string | null {
  const width = (max.x - min.x) / layout.width, height = (max.y - min.y) / layout.height, length = (max.z - min.z) / layout.length;
  if (name === 'mirrorLeft' || name === 'mirrorRight') return triangles < PLAUSIBLE.mirrorTriangles ? `${triangles} triangles` : null;
  if (triangles < PLAUSIBLE.triangles) return `${triangles} triangles`;
  const rule = name === 'bonnet' ? PLAUSIBLE.bonnet : name === 'bootLid' ? PLAUSIBLE.bootLid : PLAUSIBLE.bumper;
  if (width < rule.width) return `${Math.round(width * 100)} % of the width`;
  if ('height' in rule && height < rule.height) return `${Math.round(height * 100)} % of the height`;
  if ('length' in rule && length < rule.length) return `${Math.round(length * 100)} % of the length`;
  if (coverage < rule.coverage) return `${Math.round(coverage * 100)} % of its region`;
  return null;
}

/** The outcome of a body's split (splitParts): per part its triangles, the share of its region it covers, kept or
 *  why not; and how the body fits its cage (`cageFit`). */
export interface SplitReport { parts: Record<PartName, { triangles: number; coverage: number; kept: boolean; reason?: string }>; fit: CageFit }

/** How a body fits the cage of its model's box: the cage weights clamp to the box, so a vertex outside it moves with
 *  the nearest face (a mirror, a towbar); `outside` is the share of vertices more than `FIT.margin` out, `offset` (m)
 *  the body's bounds centre from the box centre along the length (a model not centred on its origin crumples short at
 *  one end and over-long at the other). `ok` within FIT. */
export interface CageFit { outside: number; offset: number; ok: boolean }
export const FIT = { margin: .1, outside: .01, offset: .05 } as const;
function cageFit(geometry: THREE.BufferGeometry, layout: DamageLayout): CageFit {
  const position = geometry.getAttribute('position'), n = position.count;
  let out = 0, low = Infinity, high = -Infinity;
  for (let i = 0; i < n; i++) {
    const x = position.getX(i), y = position.getY(i), z = position.getZ(i);
    low = Math.min(low, z); high = Math.max(high, z);
    if (x < layout.min[0] - FIT.margin || x > layout.max[0] + FIT.margin || y < layout.min[1] - FIT.margin || y > layout.max[1] + FIT.margin
      || z < layout.min[2] - FIT.margin || z > layout.max[2] + FIT.margin) out++;
  }
  const outside = n ? out / n : 0, offset = (low + high) / 2 - (layout.min[2] + layout.max[2]) / 2;
  return { outside: Math.round(outside * 1e4) / 1e4, offset: Math.round(offset * 100) / 100, ok: outside <= FIT.outside && Math.abs(offset) <= FIT.offset };
}
const reports = new WeakMap<THREE.BufferGeometry, SplitReport>();
/** The split report of a body geometry (splitParts). */
export const splitReport = (geometry: THREE.BufferGeometry): SplitReport | undefined => reports.get(geometry);

/** Glazing: every vertex on the body, and each window found by its facing (vehicle/damage.ts WINDOWS): pieces above
 *  `WINDOW_H` of the height (lower ones are lamp covers), facing the side beyond `SIDE_N` (left or right; front and
 *  rear by the gap at the B-pillar: the side's foremost run of pieces is the front window) or else the windscreen
 *  (ahead of the middle) and the rear window. A fused piece (glazing modelled as one shell: a piece over `FUSED_SPAN` of the
 *  length, or one of `FUSED_AREA` m² or more that faces both the side and an end) is classified triangle by triangle instead, its front and
 *  rear side glass split at the middle of that side's glazing, and vertices shared across windows are duplicated so
 *  a shattered window never drags its neighbour's edge. `damagePart.y` is the window (index + 1, 0 none); `crackUV`
 *  (m) places each vertex in its window's plane around the crack origin, a fixed point a little off the window's
 *  centre. Each window found is checked for its place and size (`splitReport(body).windows` lists the kept ones). */
const WINDOW_H = .62, SIDE_N = .55, FUSED_SPAN = .6, FUSED_SIDE = [.2, .8] as const, FUSED_AREA = .25;
const WINDOW_ID = Object.fromEntries(WINDOWS.map((name, i) => [name, i + 1])) as Record<WindowName, number>;
export function splitWindows(geometry: THREE.BufferGeometry, layout: DamageLayout) {
  if (geometry.getAttribute('damagePart')) return;
  const position = geometry.getAttribute('position'), normal = geometry.getAttribute('normal'), n = position.count;
  const index = (geometry.index?.array as ArrayLike<number> | undefined) ?? Uint32Array.from({ length: n }, (_, i) => i);
  const triangles = index.length / 3, piece = pieces(position, index, false), centreX = (layout.min[0] + layout.max[0]) / 2;
  const centre = new THREE.Vector3(centreX, (layout.min[1] + layout.max[1]) / 2, (layout.min[2] + layout.max[2]) / 2);
  // Per triangle: its outward facing (glazing is double-sided and its winding is not reliable: the vertex normals,
  // flipped outwards), area and centroid; per piece: bounds, the area-weighted normal and its side-facing share.
  const facing = new Float32Array(triangles * 3), area = new Float32Array(triangles), mid = new Float32Array(triangles * 3);
  const info = new Map<number, { box: THREE.Box3; normal: THREE.Vector3; area: number; side: number }>();
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), cross = new THREE.Vector3();
  for (let t = 0; t < triangles; t++) {
    a.fromBufferAttribute(position, index[t * 3]); b.fromBufferAttribute(position, index[t * 3 + 1]); c.fromBufferAttribute(position, index[t * 3 + 2]);
    let p = info.get(piece[index[t * 3]]);
    if (!p) { p = { box: new THREE.Box3(), normal: new THREE.Vector3(), area: 0, side: 0 }; info.set(piece[index[t * 3]], p); }
    p.box.expandByPoint(a).expandByPoint(b).expandByPoint(c);
    const m = a.clone().add(b).add(c).divideScalar(3);
    mid.set([m.x, m.y, m.z], t * 3);
    area[t] = b.clone().sub(a).cross(c.clone().sub(a)).length() / 2;
    cross.set(0, 0, 0);
    for (let k = 0; k < 3; k++) cross.add(b.fromBufferAttribute(normal, index[t * 3 + k]));
    if (cross.dot(m.clone().sub(centre).setZ(0)) < 0) cross.negate();
    cross.normalize();
    facing.set([cross.x, cross.y, cross.z], t * 3);
    p.normal.addScaledVector(cross, area[t]); p.area += area[t];
    if (Math.abs(cross.x) > SIDE_N) p.side += area[t];
  }
  const high = (y: number) => (y - layout.min[1]) / layout.height >= WINDOW_H;
  const fused = (p: { box: THREE.Box3; area: number; side: number }) => high((p.box.min.y + p.box.max.y) / 2)
    && (p.box.max.z - p.box.min.z > FUSED_SPAN * layout.length || (p.area > FUSED_AREA && p.side > FUSED_SIDE[0] * p.area && p.side < FUSED_SIDE[1] * p.area));
  const end = (z: number) => z > centre.z ? WINDOW_ID.windscreen : WINDOW_ID.rearWindow;
  // Side glass entries (a piece, or a triangle of a fused piece), for the front/rear split per side.
  type Entry = { root: number; triangle: number; min: number; max: number; z: number };
  const window = new Uint8Array(triangles), sides: Record<'left' | 'right', Entry[]> = { left: [], right: [] };
  const pieceWindow = new Map<number, number>();
  for (const [root, p] of info) {
    if (fused(p)) continue;
    const m = p.box.getCenter(new THREE.Vector3()), nrm = p.normal.clone().normalize();
    if (!high(m.y)) continue;
    if (Math.abs(nrm.x) > SIDE_N) sides[m.x > centreX ? 'left' : 'right'].push({ root, triangle: -1, min: p.box.min.z, max: p.box.max.z, z: m.z });
    else pieceWindow.set(root, end(m.z));
  }
  let anyFused = false;
  for (let t = 0; t < triangles; t++) {
    const root = piece[index[t * 3]], p = info.get(root)!;
    if (!fused(p)) continue;
    anyFused = true;
    if (!high(mid[t * 3 + 1])) continue;
    const z = mid[t * 3 + 2];
    if (Math.abs(facing[t * 3]) > SIDE_N) sides[mid[t * 3] > centreX ? 'left' : 'right'].push({ root, triangle: t, min: z, max: z, z });
    else window[t] = end(z);
  }
  for (const side of ['left', 'right'] as const) {
    const list = sides[side].sort((x, y) => y.max - x.max), suffix = side === 'left' ? 'Left' : 'Right';
    const pieceList = list.filter(e => e.triangle < 0), triangleList = list.filter(e => e.triangle >= 0);
    // Pieces: the foremost run of overlapping pieces is the front window; behind the first gap of 3 cm, the rear window.
    let front = pieceList[0]?.min ?? 0, rear = false;
    for (const e of pieceList) {
      if (!rear && e.max < front - .03) rear = true;
      if (!rear) front = Math.min(front, e.min);
      pieceWindow.set(e.root, WINDOW_ID[`${rear ? 'rear' : 'front'}${suffix}`]);
    }
    // Fused side glass: front and rear at the middle of this side's glazing.
    if (triangleList.length) {
      const lo = Math.min(...list.map(e => e.min)), hi = Math.max(...list.map(e => e.max)), split = (lo + hi) / 2;
      for (const e of triangleList) window[e.triangle] = WINDOW_ID[`${e.z > split ? 'front' : 'rear'}${suffix}`];
    }
  }
  for (let t = 0; t < triangles; t++) { const w = pieceWindow.get(piece[index[t * 3]]); if (w !== undefined && !fused(info.get(piece[index[t * 3]])!)) window[t] = w; }
  // Each window's bounds and facing; one that is not where and as big as that window would be stays unbreakable.
  const boxes = WINDOWS.map(() => new THREE.Box3()), normals = WINDOWS.map(() => new THREE.Vector3()), areas = new Float64Array(WINDOWS.length);
  for (let t = 0; t < triangles; t++) {
    const w = window[t];
    if (!w) continue;
    for (let k = 0; k < 3; k++) boxes[w - 1].expandByPoint(a.fromBufferAttribute(position, index[t * 3 + k]));
    normals[w - 1].add(b.set(facing[t * 3], facing[t * 3 + 1], facing[t * 3 + 2]).multiplyScalar(area[t])); areas[w - 1] += area[t];
  }
  const dropped: string[] = [];
  WINDOWS.forEach((name, i) => {
    const reason = boxes[i].isEmpty() ? null : implausibleWindow(name, layout, boxes[i]);
    if (!reason) return;
    dropped.push(`${name} (${reason})`);
    for (let t = 0; t < triangles; t++) if (window[t] === i + 1) window[t] = 0;
  });
  // Vertices whose triangles lie in different windows are duplicated per extra window (only fused pieces have them).
  let out: ArrayLike<number> = index, total = n;
  const vertexWindow = new Int16Array(n).fill(-1);
  if (anyFused) {
    const copies = new Map<number, number>(), extra: number[] = [], next = new Uint32Array(index.length);
    for (let t = 0; t < triangles; t++) for (let k = 0; k < 3; k++) {
      const v = index[t * 3 + k], w = window[t];
      if (vertexWindow[v] < 0 || vertexWindow[v] === w) { vertexWindow[v] = w; next[t * 3 + k] = v; continue; }
      const key = v * 8 + w;
      let copy = copies.get(key);
      if (copy === undefined) { copy = n + extra.length; copies.set(key, copy); extra.push(v); }
      next[t * 3 + k] = copy;
    }
    if (extra.length) {
      total = n + extra.length;
      for (const name of Object.keys(geometry.attributes)) {
        const attribute = geometry.getAttribute(name) as THREE.BufferAttribute;
        const size = attribute.itemSize, array = new (attribute.array.constructor as new (n: number) => THREE.TypedArray)(total * size);
        array.set(attribute.array as THREE.TypedArray);
        extra.forEach((v, j) => { for (let k = 0; k < size; k++) array[(n + j) * size + k] = attribute.array[v * size + k]; });
        geometry.setAttribute(name, new THREE.BufferAttribute(array, size, attribute.normalized));
      }
      geometry.setIndex(new THREE.BufferAttribute(total > 65535 ? next : Uint16Array.from(next), 1));
      out = next;
    }
  }
  const ids = new Uint8Array(total);
  for (let t = 0; t < triangles; t++) for (let k = 0; k < 3; k++) ids[out[t * 3 + k]] = window[t];
  // Each window's plane: its centre, outward normal and two in-plane axes; the crack origin a fixed offset from the centre.
  const planes = new Map<number, { centre: THREE.Vector3; u: THREE.Vector3; v: THREE.Vector3 }>();
  WINDOWS.forEach((_, i) => {
    const id = i + 1;
    if (!window.includes(id)) return;
    const box = boxes[i], nrm = normals[i].clone().normalize();
    const u = new THREE.Vector3(0, 1, 0).cross(nrm).normalize(), v = nrm.clone().cross(u).normalize(), size = box.getSize(new THREE.Vector3());
    const at = box.getCenter(new THREE.Vector3()).addScaledVector(u, .18 * size.length() * (id % 2 ? 1 : -1)).addScaledVector(v, .08 * size.y);
    planes.set(id, { centre: at, u, v });
  });
  const finalPosition = geometry.getAttribute('position'), parts = new Uint8Array(total * 4), uv = new Float32Array(total * 2);
  for (let i = 0; i < total; i++) {
    const id = ids[i];
    parts[i * 4 + 1] = id;
    const plane = planes.get(id);
    if (!plane) continue;
    a.fromBufferAttribute(finalPosition, i).sub(plane.centre);
    uv[i * 2] = a.dot(plane.u); uv[i * 2 + 1] = a.dot(plane.v);
  }
  geometry.setAttribute('damagePart', new THREE.BufferAttribute(parts, 4));
  // The crack coordinates share the colour's buffer (one vertex buffer fewer: damageRig.ts damageBatch).
  const colour = geometry.getAttribute('color');
  if (colour && !(colour as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute && colour.itemSize === 3) {
    const both = new Float32Array(total * 5);
    for (let i = 0; i < total; i++) { both[i * 5] = colour.getX(i); both[i * 5 + 1] = colour.getY(i); both[i * 5 + 2] = colour.getZ(i); both[i * 5 + 3] = uv[i * 2]; both[i * 5 + 4] = uv[i * 2 + 1]; }
    const buffer = new THREE.InterleavedBuffer(both, 5);
    geometry.setAttribute('color', new THREE.InterleavedBufferAttribute(buffer, 3, 0, colour.normalized));
    geometry.setAttribute('crackUV', new THREE.InterleavedBufferAttribute(buffer, 2, 3));
  } else geometry.setAttribute('crackUV', new THREE.BufferAttribute(uv, 2));
  windowsByGeometry.set(geometry, [...planes].map(([id, plane]) => ({ window: WINDOWS[id - 1], centre: plane.centre.clone() })));
  windowReports.set(geometry, { windows: [...planes.keys()].sort().map(id => WINDOWS[id - 1]), dropped, fused: anyFused });
}
/** The outcome of a glazing's split (splitWindows): the windows that crack and shatter; `dropped` ones were found but
 *  implausible (they stay intact), with the reason; `fused`: some glazing was one shell, split triangle by triangle. */
export interface WindowReport { windows: WindowName[]; dropped: string[]; fused: boolean }
const windowReports = new WeakMap<THREE.BufferGeometry, WindowReport>();
export const windowReport = (geometry: THREE.BufferGeometry): WindowReport | undefined => windowReports.get(geometry);
/** A window found where it cannot be (a lamp cover or trim taken for it) or far larger than that window. Fractions of
 *  the damage box from the rear; null when plausible. */
function implausibleWindow(name: WindowName, layout: DamageLayout, box: THREE.Box3): string | null {
  const z0 = (box.min.z - layout.min[2]) / layout.length, z1 = (box.max.z - layout.min[2]) / layout.length;
  const span = z1 - z0, across = (box.max.x - box.min.x) / layout.width, at = (z0 + z1) / 2;
  if (name === 'windscreen') return at < .45 ? 'behind the middle' : span > .5 ? `${Math.round(span * 100)} % of the length` : across < .3 ? 'narrow' : null;
  if (name === 'rearWindow') return at > .55 ? 'ahead of the middle' : span > .6 ? `${Math.round(span * 100)} % of the length` : across < .25 ? 'narrow' : null;
  return across > .3 ? `${Math.round(across * 100)} % of the width` : span > .7 ? `${Math.round(span * 100)} % of the length` : null;
}
const windowsByGeometry = new WeakMap<THREE.BufferGeometry, { window: WindowName; centre: THREE.Vector3 }[]>();
/** The windows found in a glazing geometry (splitWindows), with their crack origins (fleet frame). */
export const windowsOf = (geometry: THREE.BufferGeometry) => windowsByGeometry.get(geometry);

// ------------------------------------------------------------------ loose parts: a cheap visual swing
/** How a loose part hangs and swings: `axis` of its hinge in the fleet frame, the hinge at its bounds (`pivot`, as
 *  fractions of its box per axis; bumpers by their free end), the rest angle `rest` (rad; a bumper's free end drops to
 *  the ground), the extra opening per (m/s)² of forward speed (`lift`: the wind under a popped bonnet), the swing
 *  frequency (Hz) and damping ratio, which car-frame acceleration drives it (`drive`: axis and gain, rad per m/s²) and
 *  the angle limits. */
interface Hinge { axis: 'x' | 'z'; pivot: (side: number) => [number, number, number]; rest: (info: PartInfo) => number; lift: number; hz: number; zeta: number;
  drive: { axis: 0 | 1 | 2; gain: number }; limits: (info: PartInfo) => [number, number]; sign: (side: number) => number;
  /** A fixed turn about the hinge before the swing (Euler x, y, z; rad): a hanging bumper's free end pulled out. */
  set?: (side: number) => [number, number, number] }
/** A hanging bumper's free end drops until its lower edge reaches the ground (4 cm below its rest height). */
const ground = (info: PartInfo) => Math.asin(Math.min(.6, Math.max(.05, (info.min.y + .04) / Math.max(.3, info.max.x - info.min.x))));
/** Bumpers hang from the end that held: the free end drops, sticks out 15-20 cm and its face tips outward. */
const bumper = (front: boolean): Hinge => ({ axis: 'z', pivot: side => [side > 0 ? 0 : 1, .75, front ? .3 : .7], rest: info => .9 * ground(info), lift: 0,
  hz: 2.2, zeta: .15, drive: { axis: 1, gain: .02 }, limits: info => [.3 * ground(info), ground(info)], sign: side => side > 0 ? -1 : 1,
  set: side => [front ? .12 : -.12, (front ? -1 : 1) * side * .1, 0] });
const HINGES: Record<PartName, Hinge> = {
  frontBumper: bumper(true), rearBumper: bumper(false),
  // A popped bonnet rides up on its hinges at the scuttle, more with speed (the wind under it) and on bumps.
  bonnet: { axis: 'x', pivot: () => [.5, 1, 0], rest: () => .12, lift: .0016, hz: 1.6, zeta: .2, drive: { axis: 1, gain: -.02 }, limits: () => [.03, 1.05], sign: () => -1 },
  // The boot lid hangs open from its upper edge and swings with braking and acceleration.
  bootLid: { axis: 'x', pivot: () => [.5, 1, 1], rest: () => .35, lift: .0004, hz: 1.4, zeta: .12, drive: { axis: 2, gain: .03 }, limits: () => [.1, 1.1], sign: () => 1 },
  // A mirror dangles on its cable and swings sideways.
  mirrorLeft: { axis: 'z', pivot: () => [0, .5, .5], rest: () => .9, lift: 0, hz: 2.5, zeta: .1, drive: { axis: 0, gain: .04 }, limits: () => [.3, 1.5], sign: () => -1 },
  mirrorRight: { axis: 'z', pivot: () => [1, .5, .5], rest: () => .9, lift: 0, hz: 2.5, zeta: .1, drive: { axis: 0, gain: -.04 }, limits: () => [.3, 1.5], sign: () => 1 },
};

/** One car's part transforms: per part a rotation about its hinge (quaternion) and a translation and scale (scale 0
 *  collapses a detached part), written by `pose` into the pool row (damageRig.ts). `moving` while a loose part swings. */
export class PartPoses {
  readonly rotations = new Float32Array(PART_COUNT * 4);
  readonly translations = new Float32Array(PART_COUNT * 4);
  private readonly angle = new Float32Array(PART_COUNT);
  private readonly rate = new Float32Array(PART_COUNT);
  private readonly q = new THREE.Quaternion();
  private readonly p = new THREE.Vector3();
  constructor() { this.reset(); }
  reset() {
    this.rotations.fill(0); this.translations.fill(0); this.angle.fill(-1); this.rate.fill(0);
    for (let i = 0; i < PART_COUNT; i++) { this.rotations[i * 4 + 3] = 1; this.translations[i * 4 + 3] = 1; }
  }
  /** Steps the loose parts' swing by `dt` (s) under the car-frame acceleration `accel` (m/s², fleet frame, gravity not
   *  included) at forward speed `speed` (m/s) and writes every part's transform; true when any part is loose. */
  update(parts: ModelParts | undefined, state: DamageState, dt: number, accel: readonly number[], speed: number): boolean {
    let loose = false;
    for (let i = 0; i < PART_COUNT; i++) {
      const info = parts?.[i], s = state.parts[i];
      const r = i * 4;
      if (!info || s === PART_STATE.attached) { this.set(r, 0, 0, 0, 1, 0, 0, 0, 1); this.angle[i] = -1; continue; }
      if (s === PART_STATE.detached) {
        // Scale 0: the shader collapses every vertex of the part onto one point (no triangle left to draw).
        this.set(r, 0, 0, 0, 1, 0, 0, 0, 0);
        continue;
      }
      loose = true;
      const hinge = HINGES[info.name], side = state.partSide[i] || 1, [lo, hi] = hinge.limits(info);
      // A part that just worked loose starts closed and falls to its rest angle.
      if (this.angle[i] < 0) { this.angle[i] = 0; this.rate[i] = 0; }
      const target = Math.min(hi, hinge.rest(info) + hinge.lift * speed * Math.max(0, speed)), w = 2 * Math.PI * hinge.hz;
      const steps = Math.max(1, Math.ceil(dt / (1 / 120))), h = Math.min(dt, .1) / steps;
      for (let k = 0; k < steps; k++) {
        this.rate[i] += h * (-w * w * (this.angle[i] - target) - 2 * hinge.zeta * w * this.rate[i] + hinge.drive.gain * w * w * accel[hinge.drive.axis]);
        this.angle[i] += h * this.rate[i];
        if (this.angle[i] < lo) { this.angle[i] = lo; this.rate[i] *= -.3; }
        if (this.angle[i] > hi) { this.angle[i] = hi; this.rate[i] *= -.3; }
      }
      const f = hinge.pivot(side);
      this.p.set(info.min.x + f[0] * (info.max.x - info.min.x), info.min.y + f[1] * (info.max.y - info.min.y), info.min.z + f[2] * (info.max.z - info.min.z));
      const angle = hinge.sign(side) * this.angle[i];
      this.q.setFromAxisAngle(hinge.axis === 'x' ? X : Z, angle);
      const set = hinge.set?.(side);
      if (set) this.q.multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(set[0], set[1], set[2])));
      // x -> q (x - p) + p
      const t = this.p.clone().applyQuaternion(this.q);
      this.set(r, this.q.x, this.q.y, this.q.z, this.q.w, this.p.x - t.x, this.p.y - t.y, this.p.z - t.z, 1);
    }
    return loose;
  }
  private set(r: number, qx: number, qy: number, qz: number, qw: number, tx: number, ty: number, tz: number, scale: number) {
    this.rotations[r] = qx; this.rotations[r + 1] = qy; this.rotations[r + 2] = qz; this.rotations[r + 3] = qw;
    this.translations[r] = tx; this.translations[r + 1] = ty; this.translations[r + 2] = tz; this.translations[r + 3] = scale;
  }
}
const X = new THREE.Vector3(1, 0, 0), Z = new THREE.Vector3(0, 0, 1);
