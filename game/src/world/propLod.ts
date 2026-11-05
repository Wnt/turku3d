import * as THREE from 'three/webgpu';
import { streamTrace } from '../telemetry/streamTrace';

/** Street furniture (the generator's `props_palette` batch, one per tile) switches off beyond `hide` metres from the
 *  camera and back on within `show`: a hard switch per tile batch (plan §3.10), with no discard or vertex fade, so
 *  no material program changes and the prepass keeps its cheap path. Distance is 3D, to the batch's box.
 *  Sign and bus-stop poles stay: they are breakable and keep their own batch. Shadows stay in the tile shadow batch. */
export const PROP_LOD = { hide: 125, show: 115 } as const;

const breakable = (row: Record<string, unknown>) => row.prop_type === 'sign' || row.prop_type === 'bus_stop';

/** Furniture meshes of a tile model: props-layer draws with no breakable pole in them. */
export function furnitureMeshes(model: THREE.Object3D): THREE.Mesh[] {
  const found: THREE.Mesh[] = [];
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh || mesh.userData.layer !== 'props' || mesh.userData.shadowBatch) return;
    const rows = Array.isArray(mesh.userData.objects) ? mesh.userData.objects as Record<string, unknown>[] : [mesh.userData];
    if (!rows.some(breakable)) found.push(mesh);
  });
  return found;
}

/** Horizontal distance from (x, z) to a box's footprint; 0 inside it. */
export function footprintDistance(box: THREE.Box3, x: number, z: number) {
  const dx = Math.max(box.min.x - x, 0, x - box.max.x), dz = Math.max(box.min.z - z, 0, z - box.max.z);
  return Math.hypot(dx, dz);
}

interface Draw { mesh: THREE.Mesh; box: THREE.Box3 }

export class PropLod {
  private models = new Map<THREE.Object3D, Draw[]>();

  add(model: THREE.Object3D) {
    if (this.models.has(model)) return;
    model.updateMatrixWorld(true);
    const draws = furnitureMeshes(model).map(mesh => {
      mesh.geometry.computeBoundingBox();
      return { mesh, box: mesh.geometry.boundingBox!.clone().applyMatrix4(mesh.matrixWorld) };
    });
    if (draws.length) this.models.set(model, draws);
  }

  remove(model: THREE.Object3D) { this.models.delete(model); }

  update(camera: THREE.Camera) {
    const { x, y, z } = camera.position;
    let shown = 0, hidden = 0;
    for (const [model, draws] of this.models) for (const { mesh, box } of draws) {
      // 3D: a camera high above a tile is as far from its furniture as one standing beside it at that distance.
      const d = Math.hypot(footprintDistance(box, x, z), Math.max(0, y - box.max.y, box.min.y - y));
      const visible = d < (mesh.visible ? PROP_LOD.hide : PROP_LOD.show);
      if (visible !== mesh.visible && streamTrace.on) {
        if (visible) shown++; else hidden++;
        streamTrace.event('band.props', model.userData.traceId as string | undefined, { to: visible ? 'shown' : 'hidden', d3: Math.round(d) });
      }
      mesh.visible = visible;
    }
  }

  get size() { let n = 0; for (const draws of this.models.values()) n += draws.length; return n; }
}

/** Footprints (engine x/z boxes) of every street-furniture object and pole of `models` within `radius` of `at`: each
 *  props-layer mesh merges many objects, and its `userData.objects` rows give each one's index range (start, count).
 *  The start search keeps the player and the own car off them (world/startGround.ts): ground rays do not see props
 *  without collision, and the car once spawned on a bench at the Turku office. */
export function propFootprints(models: Iterable<THREE.Object3D>, at: { x: number; z: number }, radius: number) {
  const out: { minX: number; maxX: number; minZ: number; maxZ: number }[] = [];
  const v = new THREE.Vector3();
  for (const model of models) {
    model.updateMatrixWorld(true);
    model.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh || mesh.userData.layer !== 'props' || mesh.userData.shadowBatch) return;
      const position = mesh.geometry.getAttribute('position'), index = mesh.geometry.index;
      if (!position) return;
      const rows = Array.isArray(mesh.userData.objects) ? mesh.userData.objects as { start?: number; count?: number }[] : [];
      const ranges = rows.length && rows.every(r => typeof r.start === 'number' && typeof r.count === 'number')
        ? rows.map(r => [r.start!, r.count!] as const) : [[0, index ? index.count : position.count] as const];
      for (const [start, count] of ranges) {
        let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
        for (let i = start; i < start + count; i++) {
          v.fromBufferAttribute(position, index ? index.getX(i) : i).applyMatrix4(mesh.matrixWorld);
          if (v.x < minX) minX = v.x; if (v.x > maxX) maxX = v.x; if (v.z < minZ) minZ = v.z; if (v.z > maxZ) maxZ = v.z;
        }
        if (minX === Infinity) continue;
        const dx = Math.max(minX - at.x, 0, at.x - maxX), dz = Math.max(minZ - at.z, 0, at.z - maxZ);
        if (Math.hypot(dx, dz) <= radius) out.push({ minX, maxX, minZ, maxZ });
      }
    });
  }
  return out;
}

/** Footprint boxes of parked-car slots within `radius` of `at` (axis-aligned around each slot's rotated rectangle). */
export function parkedFootprints(vehicles: readonly { x: number; z: number; yaw: number; length: number; width: number }[] | undefined,
  at: { x: number; z: number }, radius: number) {
  const out: { minX: number; maxX: number; minZ: number; maxZ: number }[] = [];
  for (const car of vehicles ?? []) {
    if (Math.hypot(car.x - at.x, car.z - at.z) > radius + car.length) continue;
    const c = Math.abs(Math.cos(car.yaw)), s = Math.abs(Math.sin(car.yaw));
    const hx = (car.length * c + car.width * s) / 2, hz = (car.length * s + car.width * c) / 2;
    out.push({ minX: car.x - hx, maxX: car.x + hx, minZ: car.z - hz, maxZ: car.z + hz });
  }
  return out;
}
