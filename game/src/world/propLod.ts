import * as THREE from 'three/webgpu';

/** Street furniture (the generator's `props_palette` batch, one per tile) switches off beyond `hide` metres from the
 *  camera and back on within `show`: a hard switch per tile batch (plan §3.10), with no discard or vertex fade, so
 *  no material program changes and the prepass keeps its cheap path. Distance is horizontal, to the batch's box.
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
    const { x, z } = camera.position;
    for (const draws of this.models.values()) for (const { mesh, box } of draws) {
      const d = footprintDistance(box, x, z);
      mesh.visible = d < (mesh.visible ? PROP_LOD.hide : PROP_LOD.show);
    }
  }

  get size() { let n = 0; for (const draws of this.models.values()) n += draws.length; return n; }
}
