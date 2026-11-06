import * as THREE from 'three/webgpu';
import ANCHORS from './anchors.json';

/** One steering wheel per driven car: where a model's source names its steering wheel (anchors.json `own`, the box of
 *  those source parts from the importer's roles.json, model frame), the seated driver draws its turning wheel at that
 *  place (layout.ts) and the car drawn on its own (driven or remote, fleet/visuals.ts) leaves the model's static one
 *  out. Parked and traffic batches keep the source wheel. */

type Box = [[number, number, number], [number, number, number]];
const OWN = Object.fromEntries(Object.entries(ANCHORS as unknown as Record<string, { own?: Box }>)
  .flatMap(([id, a]) => a.own ? [[id, a.own]] : [])) as Record<string, Box>;
/** The source boxes are rounded to centimetres. */
const PAD = 0.015;

/** The box of model `id`'s own steering wheel, if its source names one. */
export function ownWheelBox(id: string): Box | null { return OWN[id] ?? null; }

/** A geometry over the same attributes whose index leaves out every connected part (shared corners or coincident
 *  positions) that lies wholly inside `box`: the wheel's rim, spokes and hub, not the dashboard or column it touches. */
export function withoutOwnWheel(geometry: THREE.BufferGeometry, box: Box): THREE.BufferGeometry {
  const position = geometry.getAttribute('position'), n = position.count, index = geometry.index;
  const corner = (i: number) => (index ? index.getX(i) : i), count = index ? index.count : n;
  const parent = new Int32Array(n).map((_, i) => i);
  const find = (i: number): number => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
  const union = (a: number, b: number) => { a = find(a); b = find(b); if (a !== b) parent[a] = b; };
  for (let i = 0; i < count; i += 3) { union(corner(i), corner(i + 1)); union(corner(i), corner(i + 2)); }
  // Millimetre position keys (±10 m per axis fits 53 bits): hard edges split welded vertices.
  const seen = new Map<number, number>();
  for (let i = 0; i < n; i++) {
    const key = ((Math.round(position.getX(i) * 1000) + 10000) * 20001 + Math.round(position.getY(i) * 1000) + 10000) * 20001
      + Math.round(position.getZ(i) * 1000) + 10000;
    const other = seen.get(key);
    if (other === undefined) seen.set(key, i); else union(i, other);
  }
  const [lo, hi] = box;
  const inside = (i: number) => position.getX(i) >= lo[0] - PAD && position.getX(i) <= hi[0] + PAD && position.getY(i) >= lo[1] - PAD
    && position.getY(i) <= hi[1] + PAD && position.getZ(i) >= lo[2] - PAD && position.getZ(i) <= hi[2] + PAD;
  // A part is kept as soon as one of its vertices lies outside the box.
  const outside = new Uint8Array(n);
  for (let i = 0; i < n; i++) if (!inside(i)) outside[find(i)] = 1;
  const kept: number[] = [];
  for (let i = 0; i < count; i += 3) if (outside[find(corner(i))]) kept.push(corner(i), corner(i + 1), corner(i + 2));
  const result = new THREE.BufferGeometry();
  for (const [name, attribute] of Object.entries(geometry.attributes)) result.setAttribute(name, attribute);
  result.setIndex(n > 65535 ? new THREE.Uint32BufferAttribute(kept, 1) : new THREE.Uint16BufferAttribute(kept, 1));
  result.boundingBox = geometry.boundingBox?.clone() ?? null;
  result.boundingSphere = geometry.boundingSphere?.clone() ?? null;
  return result;
}
