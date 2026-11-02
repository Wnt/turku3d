import * as THREE from 'three/webgpu';
import { REFLECTION_LAYER } from '../render/reflections';

/** Closest view-space depth of an axis-aligned world box, including pitched cameras. */
export function nearestFogDepth(box: THREE.Box3, eye: THREE.Vector3, forward: THREE.Vector3) {
  return (forward.x >= 0 ? box.min.x - eye.x : box.max.x - eye.x) * forward.x
    + (forward.y >= 0 ? box.min.y - eye.y : box.max.y - eye.y) * forward.y
    + (forward.z >= 0 ? box.min.z - eye.z : box.max.z - eye.z) * forward.z;
}

/** Static opaque scenery only. Reflections, shadow casters and fog opt-outs retain their other-pass work. */
export class SceneryFog {
  private bounds = new WeakMap<THREE.Object3D, { box: THREE.Box3; retained: boolean }>();
  private eye = new THREE.Vector3();
  private forward = new THREE.Vector3();
  private far = Infinity;

  follow(camera: THREE.Camera, fog: THREE.Scene['fog']) {
    camera.getWorldPosition(this.eye);
    camera.getWorldDirection(this.forward);
    this.far = fog instanceof THREE.Fog && Number.isFinite(fog.far) ? fog.far + 50 : Infinity;
  }

  visible(model: THREE.Object3D) {
    if (!Number.isFinite(this.far)) return true;
    let entry = this.bounds.get(model);
    if (!entry) {
      let retained = false;
      model.traverse(object => {
        const mesh = object as THREE.Mesh;
        if (!mesh.isMesh) return;
        const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        retained ||= mesh.castShadow || mesh.layers.isEnabled(REFLECTION_LAYER)
          || materials.some(material => (material as THREE.Material & { fog?: boolean }).fog === false || material.transparent);
      });
      entry = { box: new THREE.Box3().setFromObject(model), retained };
      this.bounds.set(model, entry);
    }
    return entry.retained || entry.box.isEmpty() || nearestFogDepth(entry.box, this.eye, this.forward) <= this.far;
  }
}
