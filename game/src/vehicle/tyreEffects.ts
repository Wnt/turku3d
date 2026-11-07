import * as THREE from 'three/webgpu';
import { attribute, color, float, smoothstep, uniform, uv } from 'three/tsl';
import type { Quat, WheelState } from '../physics/protocol';
import type { V3 } from '../core/origin';
import type { QualityTier } from '../render/quality';
import type { VehicleKind } from '../physics/constants';
import { TyreSlipFilter } from './tyreSlip';

const SEGMENTS = 4096, PARTICLES = 384, TRACKS = 33 * 4;
const LIFE = 3;
/** Smoke sprite kinds sharing the one draw: tyre smoke, engine-bay steam (a damaged radiator) and engine smoke. Per
 *  kind: lifetime (s), rise and drift (m/s), start size and growth (m, m/s), peak opacity and brightness (times the
 *  tyre smoke's grey). */
const KINDS = [
  { life: LIFE, rise: .65, drift: .18, size: .35, growth: .85, opacity: .24, shade: 1 },
  { life: 1.3, rise: .7, drift: .35, size: .25, growth: 1.3, opacity: .3, shade: 1.45 },
  { life: 3, rise: .8, drift: .3, size: .3, growth: 1.1, opacity: .3, shade: .14 },
] as const;
/** Where a damaged engine bay vents, in the chassis frame (+X forward): the starter car's bonnet front. */
export const ENGINE_BAY = { x: 1.45, y: .3, z: 0 } as const;

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
  private readonly shade = new THREE.InstancedBufferAttribute(new Float32Array(PARTICLES).fill(1), 1).setUsage(THREE.DynamicDrawUsage);
  private shadeDirty = false;
  /** Fractional steam and smoke emissions per venting car (by id, apart from the tyre slots, which a busy room can
   *  use up); a car not vented in a frame is dropped. */
  private readonly bays = new Map<string, { steam: number; smoke: number; seen: boolean }>();
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
    this.density = quality === 'mobile' ? 4 : quality === 'low' ? 7 : quality === 'medium' || quality === 'reduced' ? 13 : 20;
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
    smokeGeometry.setAttribute('tyreShade', this.shade);
    const smokeMaterial = new THREE.MeshBasicNodeMaterial({ color: 0xc5c9cc, transparent: true, depthWrite: false,
      side: THREE.DoubleSide, forceSinglePass: true });
    // Analytic soft sprites need no texture upload and use the same shader at every quality tier; steam and engine
    // smoke are the same sprites, brighter or darker.
    smokeMaterial.colorNode = color(0xc5c9cc).mul(attribute('tyreShade', 'float'));
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

  /** A damaged car's engine bay (vehicle/mechanics.ts `steam` and `smoke`, 0..1) vents at ENGINE_BAY of the chassis
   *  pose `pos`/`rot` while its engine runs: white steam from a leaking radiator, dark smoke from a damaged engine. */
  engineBay(id: string, pos: V3, rot: Quat, steam: number, smoke: number, dt: number) {
    if (steam <= 0 && smoke <= 0) return;
    let bay = this.bays.get(id);
    if (!bay) { bay = { steam: 0, smoke: 0, seen: true }; this.bays.set(id, bay); }
    bay.seen = true;
    this.rotation.set(rot.x, rot.y, rot.z, rot.w);
    this.point.set(ENGINE_BAY.x, ENGINE_BAY.y, ENGINE_BAY.z).applyQuaternion(this.rotation);
    this.point.x += pos.x; this.point.y += pos.y; this.point.z += pos.z;
    bay.steam = steam > 0 ? Math.min(4, bay.steam + dt * this.density * .9 * steam) : 0;
    bay.smoke = smoke > 0 ? Math.min(4, bay.smoke + dt * this.density * .9 * smoke) : 0;
    while (bay.steam >= 1) { bay.steam -= 1; this.emit(steam, 1, 0); }
    while (bay.smoke >= 1) { bay.smoke -= 1; this.emit(smoke, 2, 0); }
  }

  private setTrack(k: number, lx: number, ly: number, lz: number, rx: number, ry: number, rz: number) {
    this.active[k / 9] = 1;
    this.track[k] = this.point.x; this.track[k + 1] = this.point.y; this.track[k + 2] = this.point.z;
    this.track[k + 3] = lx; this.track[k + 4] = ly; this.track[k + 5] = lz;
    this.track[k + 6] = rx; this.track[k + 7] = ry; this.track[k + 8] = rz;
  }
  private emit(strength: number, kind = 0, lift = .1) {
    const i = this.nextParticle++ % PARTICLES, k = i * 6;
    this.particles[k] = this.point.x; this.particles[k + 1] = this.point.y + lift; this.particles[k + 2] = this.point.z;
    this.particles[k + 3] = this.elapsed; this.particles[k + 4] = strength; this.particles[k + 5] = kind;
    if (this.shade.array[i] !== KINDS[kind].shade) { this.shade.array[i] = KINDS[kind].shade; this.shadeDirty = true; }
    this.smokeUntil = Math.max(this.smokeUntil, this.elapsed + KINDS[kind].life);
  }
  /** Effect time after which every smoke particle has died, and whether the buffers already hold them all dead: then
   *  end() skips its 384-particle pass and the instance upload (every frame of the game, with or without smoke). */
  private smokeUntil = -Infinity;
  private smokeCleared = false;

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
    for (const [id, bay] of this.bays) { if (!bay.seen) this.bays.delete(id); else bay.seen = false; }
    for (let i = 0; i < TRACKS; i++) {
      if (!this.seen[i]) this.slip.reset(i);
      if (!this.touched[i]) { this.active[i] = 0; this.emission[i] = 0; }
    }
    if (this.marksDirty) {
      this.positions.needsUpdate = this.born.needsUpdate = this.strength.needsUpdate = this.order.needsUpdate = true;
      this.marksDirty = false;
    }
    const alive = this.elapsed < this.smokeUntil;
    if (!alive && this.smokeCleared) { if (this.shadeDirty) { this.shade.needsUpdate = true; this.shadeDirty = false; } return; }
    this.smokeCleared = !alive;
    for (let i = 0; i < PARTICLES; i++) {
      const k = i * 6, age = this.elapsed - this.particles[k + 3], strength = this.particles[k + 4], kind = KINDS[this.particles[k + 5]];
      if (strength <= 0 || age >= kind.life) {
        this.opacity.array[i] = 0; this.matrix.makeScale(0, 0, 0);
      } else {
        this.point.set(this.particles[k] + age * kind.drift, this.particles[k + 1] + age * kind.rise, this.particles[k + 2] + age * 0.1);
        this.scale.setScalar(kind.size + age * kind.growth);
        this.matrix.compose(this.point, camera.quaternion, this.scale);
        this.opacity.array[i] = strength * Math.min(1, age / 0.12) * (1 - age / kind.life) * kind.opacity;
      }
      this.smoke.setMatrixAt(i, this.matrix);
    }
    this.opacity.needsUpdate = this.smoke.instanceMatrix.needsUpdate = true;
    if (this.shadeDirty) { this.shade.needsUpdate = true; this.shadeDirty = false; }
  }
}
