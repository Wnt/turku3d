import * as THREE from 'three/webgpu';

const originals = new WeakMap<THREE.BufferGeometry, { position: Float32Array; color: Float32Array | null }>();

/** Broaden the individual leaf clusters around their branch attachment without adding
 * triangles or draws. Connected card islands survive meshopt vertex reordering. */
export function summerFoliage(geometry: THREE.BufferGeometry, scale = 1.4) {
  const p = geometry.getAttribute('position'), color = geometry.getAttribute('color');
  const wind = geometry.getAttribute('_wind');
  if (!wind) return; // Plain geometry in fixtures is not a foliage card atlas.
  let original = originals.get(geometry);
  if (!original) {
    original = { position: Float32Array.from({ length: p.count * 3 }, (_, i) => p.getComponent(Math.floor(i / 3), i % 3)), color: color ? Float32Array.from({ length: color.count * 3 }, (_, i) => color.getComponent(Math.floor(i / 3), i % 3)) : null };
    originals.set(geometry, original);
  }
  const parent = Int32Array.from({ length: p.count }, (_, i) => i);
  const root = (i: number): number => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
  const idx = geometry.index;
  for (let i = 0; i < (idx?.count ?? p.count); i += 3) {
    const a = idx?.getX(i) ?? i, b = idx?.getX(i + 1) ?? i + 1, c = idx?.getX(i + 2) ?? i + 2;
    parent[root(b)] = root(a); parent[root(c)] = root(a);
  }
  const anchors = new Map<number, { sum: THREE.Vector3; n: number }>();
  for (let i = 0; i < p.count; i++) {
    if (wind.getY(i) > 0.01) continue;
    const key = root(i), anchor = anchors.get(key) ?? { sum: new THREE.Vector3(), n: 0 };
    anchor.sum.add(new THREE.Vector3().fromArray(original.position, i * 3)); anchor.n++;
    anchors.set(key, anchor);
  }
  for (const anchor of anchors.values()) anchor.sum.divideScalar(anchor.n);
  for (let i = 0; i < p.count; i++) {
    const anchor = anchors.get(root(i));
    if (anchor) for (let k = 0; k < 3; k++) p.setComponent(i, k,
      anchor.sum.getComponent(k) + (original.position[i * 3 + k] - anchor.sum.getComponent(k)) * scale);
    if (color && original.color) color.setXYZ(i, original.color[i * 3] * 0.72,
      Math.min(1, original.color[i * 3 + 1] * 1.04), original.color[i * 3 + 2] * 0.58);
  }
  p.needsUpdate = true; if (color) color.needsUpdate = true;
  geometry.computeBoundingBox(); geometry.computeBoundingSphere();
}
