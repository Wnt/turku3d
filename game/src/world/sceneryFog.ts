import * as THREE from 'three/webgpu';
import { REFLECTION_LAYER } from '../render/reflections';

/** Closest view-space depth of an axis-aligned world box, including pitched cameras. */
export function nearestFogDepth(box: THREE.Box3, eye: THREE.Vector3, forward: THREE.Vector3) {
  return (forward.x >= 0 ? box.min.x - eye.x : box.max.x - eye.x) * forward.x
    + (forward.y >= 0 ? box.min.y - eye.y : box.max.y - eye.y) * forward.y
    + (forward.z >= 0 ? box.min.z - eye.z : box.max.z - eye.z) * forward.z;
}

/** Static scenery only. Keep reflected surfaces: their mirrored view depth can be shorter. */
export class SceneryFog {
  private bounds = new WeakMap<THREE.Object3D, { box: THREE.Box3; reflected: boolean }>();
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
      let reflected = false;
      model.traverse(object => {
        if ((object as THREE.Mesh).isMesh && object.layers.isEnabled(REFLECTION_LAYER)) reflected = true;
      });
      entry = { box: new THREE.Box3().setFromObject(model), reflected };
      this.bounds.set(model, entry);
    }
    return entry.reflected || entry.box.isEmpty() || nearestFogDepth(entry.box, this.eye, this.forward) <= this.far;
  }
}
