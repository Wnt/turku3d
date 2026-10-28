import type * as THREE from 'three/webgpu';

/** Static large surfaces only; actor, vegetation, pole and small-prop work stays out of the river pass. */
export const REFLECTION_LAYER = 2;
const SURFACES = new Set(['ground', 'buildings', 'structures', 'massing', 'roads']);
export function reflectionSurface(mesh: THREE.Mesh): boolean {
  if (mesh.userData.shadowBatch || (mesh as THREE.InstancedMesh).isInstancedMesh) return false;
  for (let node: THREE.Object3D | null = mesh; node; node = node.parent) {
    if (node.userData.material_key === 'water' || node.userData.material_key === 'river') return false;
    if (node.userData.layer) return SURFACES.has(node.userData.layer);
  }
  return false;
}

/** CSM is fitted to the primary camera. Reflections sample those same maps instead of drawing them again. */
export class ReflectionShadows {
  private automatic: boolean[] = [];
  private dirty: boolean[] = [];
  private paused: (THREE.LightShadow | undefined)[] = [];
  pause(lights: readonly { shadow?: THREE.LightShadow }[]) {
    this.paused.length = lights.length;
    for (let i = 0; i < lights.length; i++) {
      const shadow = this.paused[i] = lights[i].shadow;
      if (!shadow) continue;
      this.automatic[i] = shadow.autoUpdate; this.dirty[i] = shadow.needsUpdate;
      shadow.autoUpdate = false; shadow.needsUpdate = false;
    }
  }
  restore(lights: readonly { shadow?: THREE.LightShadow }[]) {
    for (let i = 0; i < this.paused.length; i++) {
      const shadow = this.paused[i];
      if (!shadow || lights[i]?.shadow !== shadow) continue;
      shadow.autoUpdate = this.automatic[i]; shadow.needsUpdate = this.dirty[i];
    }
  }
}
