import * as THREE from 'three/webgpu';

/** Static large surfaces only; actor, vegetation, pole and small-prop work stays out of the river pass. */
export const REFLECTION_LAYER = 2;
const SURFACES = new Set(['ground', 'buildings', 'structures', 'massing', 'roads']);
export function reflectionSurface(mesh: THREE.Mesh): boolean {
  if (mesh.userData.shadowBatch || (mesh as THREE.InstancedMesh).isInstancedMesh) return false;
  const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  if (materials.some(material => material.transparent || (material as THREE.MeshPhysicalMaterial).transmission > 0)) return false;
  if (mesh.userData.prop_type || mesh.userData.objects?.some((row: Record<string, unknown>) => row.prop_type)) return false;
  if (!mesh.geometry.boundingSphere) mesh.geometry.computeBoundingSphere();
  // A four-metre diameter keeps facades, banks and road surfaces, not individual trim/rail pieces.
  if ((mesh.geometry.boundingSphere?.radius ?? 0) * mesh.matrixWorld.getMaxScaleOnAxis() < 2) return false;
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

/** A moving camera re-renders the reflection every frame: an image from an older pose visibly trails
 * the view. Only a still camera reuses it, refreshing at `slowFps` for moving water and actors. */
export class ReflectionCadence {
  private last = -Infinity;
  private pose = new THREE.Matrix4();
  constructor(private slowFps: number) {}

  shouldUpdate(camera: THREE.Camera, now: number, hasOutput: boolean, force = false): boolean {
    const moved = !camera.matrixWorld.equals(this.pose);
    if (hasOutput && !force && !moved && now - this.last < 1000 / this.slowFps) return false;
    this.last = now;
    this.pose.copy(camera.matrixWorld);
    return true;
  }
}
