// Street lamp posts: one InstancedMesh for every resident rule lamp, holding only the lamps near the camera.
// Posts fade by scale-to-zero on the CPU (no discard, no positionNode), so the prepass keeps its cheap normal
// material and every pass draws the same count-independent instanced program (render/instancePrograms.ts).
import * as THREE from 'three/webgpu';
import { attribute, uniform, vec3 } from 'three/tsl';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

/** Post dimensions at scale 1, metres. Origin at the base; the arm points along local +X toward the road. */
export const POST_SHAPE = { height: 8, arm: 1.6, light: 7.58 } as const;

export const POST_FADE = { full: 105, gone: 125 } as const;

/** Scale factor of a post at a horizontal camera distance: 1 inside `full`, smoothly 0 at `gone`. */
export function postFade(distance: number): number {
  const t = Math.min(1, Math.max(0, (distance - POST_FADE.full) / (POST_FADE.gone - POST_FADE.full)));
  return 1 - t * t * (3 - 2 * t);
}

function part(geometry: THREE.BufferGeometry, color: readonly number[], glow: number) {
  const g = geometry.index ? geometry.toNonIndexed() : geometry;
  const n = g.attributes.position.count;
  g.setAttribute('color', new THREE.Float32BufferAttribute(Array.from({ length: n }, () => color).flat(), 3));
  g.setAttribute('lampGlow', new THREE.Float32BufferAttribute(new Array(n).fill(glow), 1));
  g.deleteAttribute('uv');
  return g;
}

/** About 60 triangles: tapered pole, arm, head box and a downward diffuser panel. */
export function postGeometry(): THREE.BufferGeometry {
  const { height, arm } = POST_SHAPE;
  const steel = [0.32, 0.34, 0.35], dark = [0.2, 0.21, 0.22], glass = [0.92, 0.9, 0.84];
  const pole = new THREE.CylinderGeometry(0.07, 0.11, height, 6, 1, true).translate(0, height / 2, 0);
  const cap = new THREE.BoxGeometry(0.18, 0.2, 0.18).translate(0, height - 0.1, 0);
  const bar = new THREE.BoxGeometry(arm, 0.08, 0.08).translate(arm / 2, height - 0.2, 0);
  const head = new THREE.BoxGeometry(0.7, 0.18, 0.32).translate(arm, height - 0.29, 0);
  const diffuser = new THREE.PlaneGeometry(0.6, 0.26).rotateX(Math.PI / 2).translate(arm, height - 0.385, 0);
  const merged = mergeGeometries([part(pole, steel, 0), part(cap, dark, 0), part(bar, steel, 0), part(head, dark, 0), part(diffuser, glass, 1)]);
  merged.computeBoundingSphere();
  return merged;
}

export interface PostLamp { key: string; x: number; y: number; z: number; yaw: number; scale: number }

const _m = new THREE.Matrix4(), _s = new THREE.Vector3();

export class LampPosts {
  readonly mesh: THREE.InstancedMesh;
  /** Night level 0..1 for the diffusers' emissive glow. */
  readonly night = uniform(0);
  private lamps: readonly PostLamp[] = [];
  private slots = new Map<string, number>();
  private free: number[] = [];
  private used = 0;
  private last = { x: Infinity, z: Infinity, t: -Infinity };
  private dirty = true;

  constructor(readonly capacity = 1024) {
    const material = new THREE.MeshStandardNodeMaterial({ vertexColors: true, roughness: 0.55, metalness: 0.4 });
    material.name = 'street-lamp-post';
    material.emissiveNode = vec3(1.0, 0.88, 0.72).mul(attribute('lampGlow', 'float')).mul(this.night).mul(6);
    this.mesh = new THREE.InstancedMesh(postGeometry(), material, capacity);
    this.mesh.name = 'street-lamp-posts';
    this.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.mesh.castShadow = true; this.mesh.receiveShadow = true;
    // Instances span the window; per-pass culling of one draw buys nothing.
    this.mesh.frustumCulled = false;
    this.mesh.count = 1;
    this.parkSlot(0);
  }

  /** All resident rule lamps; only those near the camera reach the instance buffer. */
  setLamps(lamps: readonly PostLamp[]) { this.lamps = lamps; this.dirty = true; }

  /** Refresh near instances after moving 1 m or every 100 ms. Slots stay stable so motion vectors stay valid. */
  update(x: number, z: number, now: number) {
    if (!this.dirty && Math.hypot(x - this.last.x, z - this.last.z) < 1 && now - this.last.t < 100) return;
    this.dirty = false; this.last = { x, z, t: now };
    const near = new Map<string, PostLamp>();
    const reach = POST_FADE.gone * POST_FADE.gone;
    for (const lamp of this.lamps) {
      const dx = lamp.x - x, dz = lamp.z - z;
      if (dx * dx + dz * dz < reach) near.set(lamp.key, lamp);
    }
    for (const [key, slot] of this.slots) if (!near.has(key)) { this.slots.delete(key); this.free.push(slot); this.parkSlot(slot); }
    this.free.sort((a, b) => b - a);
    for (const [key, lamp] of near) {
      let slot = this.slots.get(key);
      if (slot === undefined) {
        slot = this.free.pop() ?? (this.used < this.capacity ? this.used++ : undefined);
        if (slot === undefined) continue;
        this.slots.set(key, slot);
      }
      const s = lamp.scale * postFade(Math.hypot(lamp.x - x, lamp.z - z));
      _m.makeRotationY(lamp.yaw).scale(_s.setScalar(s)).setPosition(lamp.x, lamp.y, lamp.z);
      if (s <= 0) this.park(_m);
      _m.toArray(this.mesh.instanceMatrix.array, slot * 16);
    }
    // Trim trailing free slots so the draw covers only used instances; keep one so the program stays warm.
    while (this.used > 1 && this.free.includes(this.used - 1)) { this.free.splice(this.free.indexOf(this.used - 1), 1); this.used--; }
    this.mesh.count = Math.max(1, this.used);
    this.mesh.instanceMatrix.clearUpdateRanges();
    this.mesh.instanceMatrix.addUpdateRange(0, this.mesh.count * 16);
    this.mesh.instanceMatrix.needsUpdate = true;
  }

  get drawn() { return this.slots.size; }

  /** A collapsed instance far below the city: no fragments, but the draw (and its pipeline) stays alive. */
  private park(m: THREE.Matrix4) { return m.makeScale(0, 0, 0).setPosition(0, -1000, 0); }
  private parkSlot(slot: number) { this.park(_m).toArray(this.mesh.instanceMatrix.array, slot * 16); }

  dispose() { this.mesh.geometry.dispose(); (this.mesh.material as THREE.Material).dispose(); this.mesh.dispose(); }
}
