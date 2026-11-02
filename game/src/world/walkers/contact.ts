/** One instanced soft ground-contact pass for the whole crowd. No per-person shadow maps. */
import * as THREE from 'three/webgpu';
import { attribute, float, length, max, pow, uv, vec3 } from 'three/tsl';
import type { WalkerInstance } from './visuals';

export class WalkerContact {
  readonly mesh: THREE.InstancedMesh;
  private readonly matrix = new THREE.Matrix4();
  private readonly rotation = new THREE.Quaternion();
  private readonly axis = new THREE.Vector3(0, 1, 0);
  private readonly position = new THREE.Vector3();
  private readonly scale = new THREE.Vector3();
  constructor(capacity: number) {
    const geometry = new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2);
    const shade = new THREE.InstancedBufferAttribute(new Float32Array(capacity), 1).setUsage(THREE.DynamicDrawUsage);
    geometry.setAttribute('humanContact', shade);
    const material = new THREE.MeshBasicNodeMaterial({ transparent: true, depthWrite: false,
      polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 });
    material.name = 'human-soft-contact';
    material.colorNode = vec3(0.055, 0.065, 0.08);
    material.opacityNode = pow(max(float(0), float(1).sub(length(uv().sub(0.5).mul(2)))), 1.7)
      .mul(attribute('humanContact', 'float')).mul(0.44);
    this.mesh = new THREE.InstancedMesh(geometry, material, capacity);
    this.mesh.name = 'human-ground-contact';
    this.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.mesh.count = 0;
    this.mesh.renderOrder = 1;
  }
  update(instances: readonly WalkerInstance[]) {
    const shade = this.mesh.geometry.getAttribute('humanContact') as THREE.InstancedBufferAttribute;
    this.mesh.count = Math.min(instances.length, shade.count);
    for (let i = 0; i < this.mesh.count; i++) {
      const w = instances[i];
      this.rotation.setFromAxisAngle(this.axis, w.yaw);
      this.mesh.setMatrixAt(i, this.matrix.compose(this.position.set(w.x, w.y + 0.012, w.z), this.rotation,
        this.scale.set(0.7 * w.scale, 1, 0.9 * w.scale)));
      shade.setX(i, w.fade * (w.lod === 1 ? 0.6 : 1));
    }
    this.mesh.visible = this.mesh.count > 0;
    if (this.mesh.count) { this.mesh.instanceMatrix.needsUpdate = true; shade.needsUpdate = true; this.mesh.computeBoundingSphere(); }
  }
  dispose() {
    this.mesh.removeFromParent(); this.mesh.geometry.dispose();
    (this.mesh.material as THREE.Material).dispose(); this.mesh.dispose();
  }
}
