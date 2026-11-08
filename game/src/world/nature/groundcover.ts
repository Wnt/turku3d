// Forest-floor plants built on the client, opaque like the grass tufts (grassBlades.ts) and drawn by the grass field
// with the same material: dwarf shrubs (bilberry and lingonberry mats on needle duff), ferns (wet and broadleaf
// floors) and low herbs (wood anemone, wood sorrel and seedlings under broadleaf crowns). Each is one plant at the
// origin; the field's instances place, turn, scale and tint them. Attributes as tuftGeometry: position, normal (bent
// towards up, so a plant lights like the floor it stands on), color (linear albedo) and `_wind`.
import * as THREE from 'three/webgpu';

/** Linear leaf albedo, dark to light. Bilberry is a fresh yellow-green, lingonberry a darker glossy green, the fern
 *  a bright summer green and the herbs between; summer forest-floor panoramas put the floor's green pixels at
 *  about 0.05-0.15 linear green. */
export const PLANT_COLOURS = {
  bilberry: [[0.035, 0.07, 0.016], [0.07, 0.125, 0.026], [0.1, 0.16, 0.035]],
  lingonberry: [[0.022, 0.045, 0.014], [0.04, 0.07, 0.02]],
  stem: [[0.05, 0.06, 0.025], [0.07, 0.075, 0.03]],
  fern: [[0.04, 0.085, 0.018], [0.08, 0.15, 0.03], [0.11, 0.18, 0.04]],
  herb: [[0.045, 0.085, 0.02], [0.075, 0.13, 0.03], [0.1, 0.15, 0.04]],
  /** Fallen wood: weathered grey, darker wet bark, a green moss film. */
  wood: [[0.11, 0.095, 0.075], [0.075, 0.06, 0.045], [0.06, 0.07, 0.035]],
} as const;

function rng(seed: number) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

function pick(r: () => number, list: readonly (readonly number[])[]) { return list[Math.floor(r() * list.length) % list.length]; }

class PlantBuilder {
  readonly position: number[] = []; readonly normal: number[] = []; readonly color: number[] = [];
  readonly wind: number[] = []; readonly index: number[] = [];
  private n = new THREE.Vector3(); private up = new THREE.Vector3(0, 1, 0);

  /** A vertex; `facing` is the leaf's own normal, bent towards up so both faces light alike. */
  vertex(p: THREE.Vector3, facing: THREE.Vector3, rgb: readonly number[], sway: number, phase: number) {
    this.n.copy(facing).multiplyScalar(0.45).add(this.up).normalize();
    this.position.push(p.x, p.y, p.z); this.normal.push(this.n.x, this.n.y, this.n.z); this.color.push(...rgb);
    this.wind.push(0.25 * sway * sway, 0.8 * sway, phase, 1);
    return this.position.length / 3 - 1;
  }

  /** A leaf: a diamond of two triangles from `base` along `dir` (unit), `length` long and `width` wide, cupped. */
  leaf(base: THREE.Vector3, dir: THREE.Vector3, side: THREE.Vector3, length: number, width: number, rgb: readonly number[], sway: number, phase: number) {
    const facing = new THREE.Vector3().crossVectors(side, dir).normalize();
    if (facing.y < 0) facing.negate();
    const mid = base.clone().addScaledVector(dir, length * 0.45).addScaledVector(facing, length * 0.06);
    const tip = base.clone().addScaledVector(dir, length);
    const a = this.vertex(base, facing, rgb, sway, phase);
    const l = this.vertex(mid.clone().addScaledVector(side, -width / 2), facing, rgb, sway, phase);
    const r = this.vertex(mid.clone().addScaledVector(side, width / 2), facing, rgb, sway, phase);
    const t = this.vertex(tip, facing, rgb, sway * 1.1, phase);
    this.index.push(a, l, r, l, t, r);
  }

  /** A thin stem ribbon from `a` to `b` (two triangles). */
  stem(a: THREE.Vector3, b: THREE.Vector3, width: number, rgb: readonly number[], swayA: number, swayB: number, phase: number) {
    const dir = new THREE.Vector3().subVectors(b, a).normalize();
    const side = new THREE.Vector3().crossVectors(dir, this.up);
    if (side.lengthSq() < 1e-6) side.set(1, 0, 0);
    side.normalize().multiplyScalar(width / 2);
    const facing = new THREE.Vector3().crossVectors(side, dir).normalize();
    const i = this.vertex(a.clone().sub(side), facing, rgb, swayA, phase), j = this.vertex(a.clone().add(side), facing, rgb, swayA, phase);
    const k = this.vertex(b.clone().sub(side), facing, rgb, swayB, phase), l = this.vertex(b.clone().add(side), facing, rgb, swayB, phase);
    this.index.push(i, j, k, j, l, k);
  }

  /** A still round stick from `a` to `b`: `sides` faces, outward normals, no wind. */
  tube(a: THREE.Vector3, b: THREE.Vector3, radiusA: number, radiusB: number, sides: number, rgb: readonly number[]) {
    const dir = new THREE.Vector3().subVectors(b, a).normalize();
    const u = new THREE.Vector3().crossVectors(dir, this.up);
    if (u.lengthSq() < 1e-6) u.set(1, 0, 0);
    u.normalize();
    const v = new THREE.Vector3().crossVectors(u, dir).normalize();
    const out = new THREE.Vector3(), start = this.position.length / 3;
    for (let k = 0; k <= sides; k++) {
      const angle = (k / sides) * Math.PI * 2;
      out.copy(u).multiplyScalar(Math.cos(angle)).addScaledVector(v, Math.sin(angle));
      this.vertex(a.clone().addScaledVector(out, radiusA), out, rgb, 0, 0);
      this.vertex(b.clone().addScaledVector(out, radiusB), out, rgb, 0, 0);
    }
    for (let k = 0; k < sides; k++) {
      const i = start + k * 2;
      this.index.push(i, i + 2, i + 1, i + 1, i + 2, i + 3);
    }
  }

  geometry() {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(this.position, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(this.normal, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(this.color, 3));
    geometry.setAttribute('_wind', new THREE.Float32BufferAttribute(this.wind, 4));
    geometry.setIndex(this.index);
    geometry.computeBoundingBox(); geometry.computeBoundingSphere();
    return geometry;
  }
}

/** A dwarf-shrub clump, about 0.5 m across and 0.2-0.3 m tall: stems branching from the root with small oval leaves
 *  (bilberry), a third of the stems the low, darker lingonberry. Clumps overlap into the mats of the needle duff. */
export function dwarfShrubGeometry(seed = 7321) {
  const r = rng(seed), b = new PlantBuilder();
  const a = new THREE.Vector3(), c = new THREE.Vector3(), dir = new THREE.Vector3(), side = new THREE.Vector3();
  for (let s = 0; s < 9; s++) {
    const lingon = s >= 6, at = r() * Math.PI * 2, from = Math.sqrt(r()) * 0.22, phase = r();
    const height = lingon ? 0.08 + r() * 0.06 : 0.17 + r() * 0.12, lean = 0.25 + r() * 0.35;
    a.set(Math.cos(at) * from, -0.01, Math.sin(at) * from);
    c.set(a.x + Math.cos(at) * lean * height, height, a.z + Math.sin(at) * lean * height);
    b.stem(a, c, 0.006, pick(r, PLANT_COLOURS.stem), 0, 1, phase);
    const leaves = lingon ? 6 : 8;
    for (let k = 0; k < leaves; k++) {
      const t = 0.3 + 0.7 * (k / (leaves - 1)), turn = r() * Math.PI * 2, rise = 0.1 + r() * 0.35;
      const base = a.clone().lerp(c, t);
      dir.set(Math.cos(turn) * Math.cos(rise), Math.sin(rise), Math.sin(turn) * Math.cos(rise)).normalize();
      side.set(-Math.sin(turn), 0, Math.cos(turn));
      const length = lingon ? 0.024 + r() * 0.01 : 0.03 + r() * 0.016;
      b.leaf(base, dir, side, length, length * (lingon ? 0.55 : 0.62), pick(r, lingon ? PLANT_COLOURS.lingonberry : PLANT_COLOURS.bilberry), t, phase);
    }
  }
  return b.geometry();
}

/** A fern, 0.4-0.6 m tall: 6 fronds arching out of a crown, each a rachis with paired pinnae that shorten to the tip. */
export function fernGeometry(seed = 5113) {
  const r = rng(seed), b = new PlantBuilder();
  const dir = new THREE.Vector3(), side = new THREE.Vector3(), out = new THREE.Vector3();
  for (let f = 0; f < 6; f++) {
    const at = (f / 6) * Math.PI * 2 + (r() - 0.5) * 0.6, length = 0.45 + r() * 0.25, rise = 1.05 + r() * 0.25, phase = r();
    const colour = pick(r, PLANT_COLOURS.fern);
    out.set(Math.cos(at), 0, Math.sin(at));
    side.set(-Math.sin(at), 0, Math.cos(at));
    const point = (t: number) => {
      // The rachis rises steeply and arches over: the angle above horizontal falls along it.
      const angle = rise * (1 - 0.85 * t);
      return new THREE.Vector3().addScaledVector(out, length * (0.15 + 0.55 * t) * t + 0.02).setY(length * Math.sin(angle) * t * 0.85);
    };
    const segments = 7;
    let prev = point(0);
    for (let k = 1; k <= segments; k++) {
      const t = k / segments, next = point(t);
      b.stem(prev, next, 0.006, colour, (k - 1) / segments, t, phase);
      // Paired pinnae from the segment's middle, longest a third of the way out, angled forward and slightly down.
      const at2 = prev.clone().lerp(next, 0.5), span = length * 0.22 * Math.sin(Math.PI * Math.min(1, t * 1.15)) + 0.015;
      dir.subVectors(next, prev).normalize();
      for (const s of [-1, 1]) {
        const p = side.clone().multiplyScalar(s).addScaledVector(dir, 0.55).setY(-0.12).normalize();
        const across = new THREE.Vector3().crossVectors(p, new THREE.Vector3(0, 1, 0)).normalize();
        b.leaf(at2, p, across, span, span * 0.38, colour, t, phase);
      }
      prev = next;
    }
  }
  return b.geometry();
}

/** A low herb patch, about 0.5 m across and 0.1-0.22 m tall: leaves on stalks, three-lobed (wood anemone, wood
 *  sorrel) as fans of three leaves 5-8 cm long. */
export function herbGeometry(seed = 9029) {
  const r = rng(seed), b = new PlantBuilder();
  const a = new THREE.Vector3(), c = new THREE.Vector3(), dir = new THREE.Vector3(), side = new THREE.Vector3();
  for (let s = 0; s < 11; s++) {
    const at = r() * Math.PI * 2, from = Math.sqrt(r()) * 0.24, height = 0.1 + r() * 0.12, phase = r();
    const colour = pick(r, PLANT_COLOURS.herb);
    a.set(Math.cos(at) * from, -0.01, Math.sin(at) * from);
    c.set(a.x + (r() - 0.5) * 0.03, height, a.z + (r() - 0.5) * 0.03);
    b.stem(a, c, 0.004, PLANT_COLOURS.stem[0], 0, 1, phase);
    const turn0 = r() * Math.PI * 2;
    for (let k = 0; k < 3; k++) {
      const turn = turn0 + (k * Math.PI * 2) / 3, droop = -0.05 - r() * 0.2;
      dir.set(Math.cos(turn) * Math.cos(droop), Math.sin(droop), Math.sin(turn) * Math.cos(droop)).normalize();
      side.set(-Math.sin(turn), 0, Math.cos(turn));
      const length = 0.05 + r() * 0.03;
      b.leaf(c, dir, side, length, length * 0.8, colour, 1, phase);
    }
  }
  return b.geometry();
}

/** A fallen branch, 0.9-1.6 m long and 3-5 cm thick, lying on the floor with two side twigs: the dead wood of the
 *  forest floor (reference video frames t035-t041; Finnish managed forests leave thinning branches). */
export function deadwoodGeometry(seed = 4421) {
  const r = rng(seed), b = new PlantBuilder();
  const length = 0.9 + r() * 0.7, radius = 0.018 + r() * 0.01, colour = pick(r, PLANT_COLOURS.wood);
  const points = [new THREE.Vector3(-length / 2, radius * 0.7, 0)];
  for (let k = 1; k <= 3; k++) points.push(new THREE.Vector3(-length / 2 + (length * k) / 3, radius * 0.7, (r() - 0.5) * 0.12));
  for (let k = 0; k < 3; k++) b.tube(points[k], points[k + 1], radius * (1 - k * 0.22), radius * (1 - (k + 1) * 0.22), 5, colour);
  for (const k of [1, 2]) {
    const from = points[k], side = r() < 0.5 ? -1 : 1, twig = 0.25 + r() * 0.25;
    const to = from.clone().add(new THREE.Vector3(twig * 0.6, 0.02 + r() * 0.05, side * twig * 0.8));
    b.tube(from, to, radius * 0.45, radius * 0.2, 4, colour);
  }
  return b.geometry();
}
