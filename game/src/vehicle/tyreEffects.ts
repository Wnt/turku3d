import * as THREE from 'three/webgpu';
import { attribute, float, smoothstep, uniform, uv } from 'three/tsl';
import type { Quat, WheelState } from '../physics/protocol';
import type { V3 } from '../core/origin';
import type { QualityTier } from '../render/quality';
import type { VehicleKind } from '../physics/constants';
import { TyreSlipFilter } from './tyreSlip';

const SEGMENTS = 4096, PARTICLES = 384, TRACKS = 33 * 4;
const LIFE = 3;

/** Two permanent draws and materials, including during startup warmup. All runtime storage is bounded;
 * no geometries, materials, vectors or particle objects are created by the frame/emission paths. */
export class TyreEffects {
  readonly root = new THREE.Group();
  private readonly time = uniform(0);
  private readonly serial = uniform(0);
  private readonly positions = new THREE.BufferAttribute(new Float32Array(SEGMENTS * 12), 3).setUsage(THREE.DynamicDrawUsage);
  private readonly born = new THREE.BufferAttribute(new Float32Array(SEGMENTS * 4), 1).setUsage(THREE.DynamicDrawUsage);
  private readonly strength = new THREE.BufferAttribute(new Float32Array(SEGMENTS * 4), 1).setUsage(THREE.DynamicDrawUsage);
  private readonly order = new THREE.BufferAttribute(new Float32Array(SEGMENTS * 4), 1).setUsage(THREE.DynamicDrawUsage);
  private readonly opacity = new THREE.InstancedBufferAttribute(new Float32Array(PARTICLES), 1).setUsage(THREE.DynamicDrawUsage);
  private readonly smoke: THREE.InstancedMesh;
  private readonly track = new Float64Array(TRACKS * 9);
  private readonly active = new Uint8Array(TRACKS);
  private readonly emission = new Float32Array(TRACKS);
  private readonly slip = new TyreSlipFilter(TRACKS);
  private readonly seen = new Uint8Array(TRACKS);
  private readonly touched = new Uint8Array(TRACKS);
  private readonly reserved = new Uint8Array(33);
  private readonly particles = new Float32Array(PARTICLES * 6);
  private readonly point = new THREE.Vector3();
  private readonly normal = new THREE.Vector3();
  private readonly side = new THREE.Vector3();
  private readonly rotation = new THREE.Quaternion();
  private readonly matrix = new THREE.Matrix4();
  private readonly scale = new THREE.Vector3();
  private nextSegment = 0;
  private nextParticle = 0;
  private elapsed = 0;
  private marksDirty = false;
  private readonly density: number;

  constructor(parent: THREE.Object3D, quality: QualityTier) {
    this.density = quality === 'low' ? 7 : quality === 'medium' ? 13 : 20;
    this.root.name = 'tyre-effects';
    parent.add(this.root);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', this.positions);
    geometry.setAttribute('tyreBorn', this.born);
    geometry.setAttribute('tyreStrength', this.strength);
    geometry.setAttribute('tyreOrder', this.order);
    const indices = new Uint16Array(SEGMENTS * 6), uvs = new Float32Array(SEGMENTS * 8);
    for (let i = 0; i < SEGMENTS; i++) {
      const v = i * 4;
      indices.set([v, v + 1, v + 2, v + 2, v + 1, v + 3], i * 6);
      uvs.set([0, 0, 1, 0, 0, 1, 1, 1], i * 8);
    }
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
    const marks = new THREE.MeshBasicNodeMaterial({ color: 0x11100f, transparent: true, depthWrite: false,
      side: THREE.DoubleSide, forceSinglePass: true, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 });
    const edge = smoothstep(0, 0.12, uv().x).mul(float(1).sub(smoothstep(0.88, 1, uv().x)));
    marks.opacityNode = attribute('tyreStrength', 'float').mul(0.65).mul(edge)
      .mul(float(1).sub(smoothstep(40, 65, this.time.sub(attribute('tyreBorn', 'float')))))
      .mul(float(1).sub(smoothstep(SEGMENTS - 256, SEGMENTS, this.serial.sub(attribute('tyreOrder', 'float')))));
    const mesh = new THREE.Mesh(geometry, marks);
    mesh.name = 'tyre-marks'; mesh.frustumCulled = false; mesh.renderOrder = 1;
    this.root.add(mesh);
    const smokeGeometry = new THREE.PlaneGeometry(1, 1);
    smokeGeometry.setAttribute('tyreOpacity', this.opacity);
    const smokeMaterial = new THREE.MeshBasicNodeMaterial({ color: 0xc5c9cc, transparent: true, depthWrite: false,
      side: THREE.DoubleSide, forceSinglePass: true });
    // Analytic soft sprites need no texture upload and use the same shader at every quality tier.
    smokeMaterial.opacityNode = attribute('tyreOpacity', 'float')
      .mul(float(1).sub(smoothstep(0.1, 0.5, uv().sub(0.5).length())));
    this.smoke = new THREE.InstancedMesh(smokeGeometry, smokeMaterial, PARTICLES);
    this.smoke.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.smoke.name = 'tyre-smoke'; this.smoke.frustumCulled = false; this.smoke.renderOrder = 2;
    this.matrix.makeScale(0, 0, 0);
    for (let i = 0; i < PARTICLES; i++) this.smoke.setMatrixAt(i, this.matrix);
    this.root.add(this.smoke);
  }

  reserve(): number {
    for (let i = 1; i < 33; i++) if (!this.reserved[i]) { this.reserved[i] = 1; return i; }
    return -1;
  }
  release(slot: number) {
    if (slot < 1) return;
    this.reserved[slot] = 0;
    for (let i = slot * 4; i < (slot + 1) * 4; i++) this.slip.reset(i);
    this.active.fill(0, slot * 4, (slot + 1) * 4);
    this.emission.fill(0, slot * 4, (slot + 1) * 4);
  }
  begin(dt: number) {
    this.elapsed += dt;
    this.time.value = this.elapsed;
    this.touched.fill(0); this.seen.fill(0);
  }

  vehicle(slot: number, pos: V3, rot: Quat, wheels: readonly WheelState[], kind: VehicleKind, dt: number, speed: number) {
    if (slot < 0 || slot >= 33 || kind === 'scooter') return;
    this.rotation.set(rot.x, rot.y, rot.z, rot.w);
    for (let i = 0; i < wheels.length && i < 4; i++) {
      const wheel = wheels[i], t = wheel.tyre, id = slot * 4 + i, k = id * 9;
      this.seen[id] = 1;
      const slip = this.slip.update(id, wheel, speed, dt);
      if (!t || slip <= 0) continue;
      this.point.set(t.point.x, t.point.y, t.point.z).applyQuaternion(this.rotation);
      this.point.x += pos.x; this.point.y += pos.y; this.point.z += pos.z;
      this.normal.set(t.normal.x, t.normal.y, t.normal.z).applyQuaternion(this.rotation).normalize();
      if (this.normal.y < 0.5) continue;
      this.touched[id] = 1;
      this.point.addScaledVector(this.normal, 0.018);
      // Width lies in the contact plane, including banked roads and sloped bridge decks.
      this.side.set(Math.sin(wheel.steer), 0, Math.cos(wheel.steer)).applyQuaternion(this.rotation);
      this.side.addScaledVector(this.normal, -this.side.dot(this.normal)).normalize()
        .multiplyScalar(kind === 'truck' ? 0.16 : 0.11);
      const lx = this.point.x - this.side.x, ly = this.point.y - this.side.y, lz = this.point.z - this.side.z;
      const rx = this.point.x + this.side.x, ry = this.point.y + this.side.y, rz = this.point.z + this.side.z;
      const distance = Math.hypot(this.point.x - this.track[k], this.point.y - this.track[k + 1], this.point.z - this.track[k + 2]);
      if (!this.active[id] || distance > 2) {
        this.setTrack(k, lx, ly, lz, rx, ry, rz);
      } else if (distance >= 0.12) {
        const segment = this.nextSegment++ % SEGMENTS, p = segment * 12;
        const a = this.positions.array;
        a[p] = this.track[k + 3]; a[p + 1] = this.track[k + 4]; a[p + 2] = this.track[k + 5];
        a[p + 3] = this.track[k + 6]; a[p + 4] = this.track[k + 7]; a[p + 5] = this.track[k + 8];
        a[p + 6] = lx; a[p + 7] = ly; a[p + 8] = lz;
        a[p + 9] = rx; a[p + 10] = ry; a[p + 11] = rz;
        for (let v = segment * 4; v < segment * 4 + 4; v++) {
          this.born.array[v] = this.elapsed; this.strength.array[v] = slip; this.order.array[v] = this.nextSegment;
        }
        this.serial.value = this.nextSegment;
        this.marksDirty = true;
        this.setTrack(k, lx, ly, lz, rx, ry, rz);
      }
      // Fractional emissions are carried per wheel: density is independent of render FPS.
      this.emission[id] += dt * this.density * slip;
      if (this.emission[id] >= 1) {
        this.emission[id] %= 1;
        this.emit(slip);
      }
    }
  }

  private setTrack(k: number, lx: number, ly: number, lz: number, rx: number, ry: number, rz: number) {
    this.active[k / 9] = 1;
    this.track[k] = this.point.x; this.track[k + 1] = this.point.y; this.track[k + 2] = this.point.z;
    this.track[k + 3] = lx; this.track[k + 4] = ly; this.track[k + 5] = lz;
    this.track[k + 6] = rx; this.track[k + 7] = ry; this.track[k + 8] = rz;
  }
  private emit(slip: number) {
    const k = (this.nextParticle++ % PARTICLES) * 6;
    this.particles[k] = this.point.x; this.particles[k + 1] = this.point.y + 0.1; this.particles[k + 2] = this.point.z;
    this.particles[k + 3] = this.elapsed; this.particles[k + 4] = slip;
  }

  dispose() {
    this.root.removeFromParent();
    for (const child of this.root.children) {
      const mesh = child as THREE.Mesh;
      mesh.geometry.dispose();
      (mesh.material as THREE.Material).dispose();
    }
    this.smoke.dispose();
  }

  end(camera: THREE.Camera) {
    for (let i = 0; i < TRACKS; i++) {
      if (!this.seen[i]) this.slip.reset(i);
      if (!this.touched[i]) { this.active[i] = 0; this.emission[i] = 0; }
    }
    if (this.marksDirty) {
      this.positions.needsUpdate = this.born.needsUpdate = this.strength.needsUpdate = this.order.needsUpdate = true;
      this.marksDirty = false;
    }
    for (let i = 0; i < PARTICLES; i++) {
      const k = i * 6, age = this.elapsed - this.particles[k + 3], strength = this.particles[k + 4];
      if (strength <= 0 || age >= LIFE) {
        this.opacity.array[i] = 0; this.matrix.makeScale(0, 0, 0);
      } else {
        this.point.set(this.particles[k] + age * 0.18, this.particles[k + 1] + age * 0.65, this.particles[k + 2] + age * 0.1);
        this.scale.setScalar(0.35 + age * 0.85);
        this.matrix.compose(this.point, camera.quaternion, this.scale);
        this.opacity.array[i] = strength * Math.min(1, age / 0.12) * (1 - age / LIFE) * 0.24;
      }
      this.smoke.setMatrixAt(i, this.matrix);
    }
    this.opacity.needsUpdate = this.smoke.instanceMatrix.needsUpdate = true;
  }
}
