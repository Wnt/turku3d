// Opaque grass tufts built on the client: tapered, curved blades with a root-to-tip colour and no alpha test. The
// alpha-tested card tufts read as flat planes edge-on and lost their thin blades to the 0.5 cut at a distance; blades
// keep their edges, the depth test rejects hidden blade pixels early, and TRAA resolves the thin tips.
import * as THREE from 'three/webgpu';

/** Linear albedo at the root, middle and tip of a green blade and of a straw (dry, seeding) one. The tuft's mean sits
 *  on the meadow finish's ground ([0.103, 0.13, 0.033], generator ground_finishes.py; area-weighted, so the wide dark
 *  roots count), and tufts grow out of their ground instead of standing on it as brighter or bluer patches. */
export const BLADE_COLOURS = {
  green: [[0.04, 0.062, 0.017], [0.105, 0.163, 0.038], [0.18, 0.232, 0.062]],
  straw: [[0.08, 0.08, 0.03], [0.21, 0.185, 0.085], [0.34, 0.29, 0.135]],
} as const;
/** Height fractions of a blade's rings; the tip is one vertex. */
const RINGS = [0, 0.35, 0.7, 1];

function rng(seed: number) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

/**
 * One tuft of `blades` blades around its root (model space, root at y = 0, about 0.42 m tall like the card tuft it
 * replaces). Attributes: position, normal (bent towards up, so both faces light alike), color (root-to-tip albedo),
 * `_wind` (x sway, rising with the square of the height; y flutter; z phase per blade) and nothing else: the grass
 * field's instances scale and turn it.
 */
export function tuftGeometry(blades = 14, seed = 4211) {
  const r = rng(seed);
  const position: number[] = [], normal: number[] = [], color: number[] = [], wind: number[] = [], index: number[] = [];
  const up = new THREE.Vector3(0, 1, 0), side = new THREE.Vector3(), facing = new THREE.Vector3(), tangent = new THREE.Vector3();
  const centre = new THREE.Vector3(), next = new THREE.Vector3(), n = new THREE.Vector3();
  for (let b = 0; b < blades; b++) {
    // Roots cluster in a 12 cm tuft; blades lean outwards and curve over more towards their tips.
    const at = r() * Math.PI * 2, from = Math.sqrt(r()) * 0.12, dir = at + (r() - 0.5) * 1.2;
    const rootX = Math.cos(at) * from, rootZ = Math.sin(at) * from;
    const height = 0.26 + r() * 0.2, lean = 0.1 + r() * 0.25, curve = 0.15 + r() * 0.35, width = 0.012 + r() * 0.008;
    const dx = Math.cos(dir), dz = Math.sin(dir), twist = (r() - 0.5) * 0.6;
    side.set(-dz * Math.cos(twist), Math.sin(twist) * 0.2, dx * Math.cos(twist)).normalize();
    const palette = r() < 0.18 ? BLADE_COLOURS.straw : BLADE_COLOURS.green, phase = r();
    const ring = (t: number, out: THREE.Vector3) => {
      const reach = (lean * t + curve * t * t) * height;
      return out.set(rootX + dx * reach, height * t * (1 - 0.25 * curve * t * t) - (t ? 0 : 0.02), rootZ + dz * reach);
    };
    const first = position.length / 3;
    for (const [k, t] of RINGS.entries()) {
      ring(t, centre); ring(Math.min(1, t + 0.05), next);
      tangent.subVectors(next, centre).normalize();
      if (k === RINGS.length - 1) tangent.subVectors(centre, ring(t - 0.05, next)).normalize();
      facing.crossVectors(side, tangent).normalize();
      n.copy(facing).multiplyScalar(0.35).add(up).normalize();
      const half = width * (1 - Math.pow(t, 1.6)) / 2;
      const albedo = t < 0.5 ? mixRgb(palette[0], palette[1], t / 0.5) : mixRgb(palette[1], palette[2], (t - 0.5) / 0.5);
      const swing = [0.3 * t * t, 0.8 * t, phase, 1];
      for (const s of k === RINGS.length - 1 ? [0] : [-1, 1]) {
        position.push(centre.x + side.x * half * s, centre.y + side.y * half * s, centre.z + side.z * half * s);
        normal.push(n.x, n.y, n.z); color.push(...albedo); wind.push(...swing);
      }
    }
    // Two quads between the three rings with two vertices, a triangle to the tip.
    for (let k = 0; k < RINGS.length - 2; k++) {
      const a = first + k * 2;
      index.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
    const last = first + (RINGS.length - 2) * 2;
    index.push(last, last + 1, last + 2);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(position, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normal, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(color, 3));
  geometry.setAttribute('_wind', new THREE.Float32BufferAttribute(wind, 4));
  geometry.setIndex(index);
  geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  return geometry;
}

function mixRgb(a: readonly number[], b: readonly number[], t: number) {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}
