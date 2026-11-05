// The tile props the client turns into their own physics bodies, read from the tile's visual glTF for the drive
// sweep, so it drives into the same signs and bus stops players do. The client finds them in the loaded three.js
// model (world/poleExtraction.ts); both use the same records (world/poleRecords.ts). A record without a generator
// pole base stands at its node's world position, as in the client's fallback without geometry.
import { Matrix4, Quaternion, Vector3 } from 'three';
import type { PoleSpec } from '../../physics/protocol';
import { pole, poleHeight, poleId, recordedBase } from '../../world/poleRecords';

interface GltfNode { children?: number[]; translation?: number[]; rotation?: number[]; scale?: number[]; matrix?: number[]; mesh?: number; extras?: Record<string, unknown> }
interface Gltf { nodes?: GltfNode[]; scenes?: { nodes: number[] }[]; scene?: number }

/** The JSON chunk of a binary glTF. */
export function glbJson(bytes: Uint8Array): Gltf {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(0, true) !== 0x46546c67) throw new Error('not a glb');
  const length = view.getUint32(12, true);
  return JSON.parse(new TextDecoder().decode(bytes.subarray(20, 20 + length)));
}

function local(node: GltfNode): Matrix4 {
  if (node.matrix) return new Matrix4().fromArray(node.matrix);
  const t = node.translation ?? [0, 0, 0], r = node.rotation ?? [0, 0, 0, 1], s = node.scale ?? [1, 1, 1];
  return new Matrix4().compose(new Vector3(t[0], t[1], t[2]), new Quaternion(r[0], r[1], r[2], r[3]), new Vector3(s[0], s[1], s[2]));
}

/** Pole bodies of one tile's visual model, in engine coordinates (tile models sit at the world origin). */
export function polesOf(gltf: Gltf): PoleSpec[] {
  const nodes = gltf.nodes ?? [], out: PoleSpec[] = [], seen = new Set<string>();
  const roots = gltf.scenes?.[gltf.scene ?? 0]?.nodes ?? [];
  const add = (spec: PoleSpec) => { if (spec.id && !seen.has(spec.id)) { seen.add(spec.id); out.push(spec); } };
  const walk = (i: number, parent: Matrix4) => {
    const node = nodes[i], world = parent.clone().multiply(local(node)), extras = node.extras ?? {};
    const rows = Array.isArray(extras.objects) ? extras.objects as Record<string, unknown>[] : null;
    if (rows && node.mesh !== undefined) {
      for (const e of rows) {
        const b = pole(e) ? recordedBase(e) : null;
        if (b) add({ id: poleId(e), base: { x: b[0], y: b[1], z: b[2] }, yaw: 0, height: poleHeight(e) });
      }
    } else if (pole(extras)) {
      const b = recordedBase(extras), p = new Vector3().setFromMatrixPosition(world);
      const rotation = new Quaternion().setFromRotationMatrix(world);
      const yaw = Math.atan2(2 * (rotation.w * rotation.y + rotation.x * rotation.z), 1 - 2 * (rotation.y ** 2 + rotation.z ** 2));
      add({ id: poleId(extras), base: b ? { x: b[0], y: b[1], z: b[2] } : { x: p.x, y: p.y, z: p.z }, yaw, height: poleHeight(extras) });
    }
    for (const c of node.children ?? []) walk(c, world);
  };
  for (const r of roots) walk(r, new Matrix4());
  return out;
}
