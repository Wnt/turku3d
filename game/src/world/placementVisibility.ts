import * as THREE from 'three/webgpu';
import { ACTIVE, BOX_STRIDE, MAX_SLOT_BOXES, SLOT_STRIDE } from './occlusionRaster';
import { bufferBytes, CAMERA, CONTROL, layout, MAX_SLOTS } from './visibilityLayout';
import { staticBatchPlacements } from './staticBatch';

type Vec3 = [number, number, number];
interface OccluderBox { min: Vec3; max: Vec3; yaw?: number }
/** Results computed for a camera farther than this (metres) or turned more than this (cosine) are not used. */
const STALE_METRES = 1.5, STALE_COS = Math.cos(THREE.MathUtils.degToRad(3));

function readBoxes(value: unknown): OccluderBox[] {
  const data = value as { version?: number; boxes?: unknown[] } | undefined;
  if (data?.version !== 1 || !Array.isArray(data.boxes)) return [];
  const finite = (v: unknown): v is Vec3 => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite);
  return data.boxes.flatMap(row => {
    const box = row as OccluderBox;
    return finite(box?.min) && finite(box?.max) && box.min.every((v, i) => v < box.max[i])
      && (box.yaw === undefined || Number.isFinite(box.yaw)) ? [box] : [];
  }).slice(0, MAX_SLOT_BOXES);
}

/** Occlusion of placed building models in a dedicated worker (GTA-style box occluders).
 *
 * Placement bounds and occluder corners live in a SharedArrayBuffer; the main thread publishes the camera each
 * frame and reads the occluded bitmask the worker computed for the previous camera. Results only gate the primary
 * camera's beauty/normal draws (`occluded`), never shadows, reflections or shader preparation, and are ignored when
 * the camera moved or turned noticeably since they were computed. Without cross-origin isolation it stays off. */
export class PlacementVisibility {
  private views: ReturnType<typeof layout> | null = null;
  private worker: Worker | null = null;
  private free: number[] = [];
  private high = 0;
  private meshes = new WeakMap<THREE.Object3D, number>();
  private snapshot = new Uint32Array(MAX_SLOTS / 32);
  private fresh = false;
  private forward = new THREE.Vector3();
  private seq = 0;
  private vp = new THREE.Matrix4();
  /** Per-frame batch decisions: prepass and beauty must agree, and owners are scanned once. */
  private batches = new WeakMap<THREE.Object3D, number>();
  /** The primary camera; only its draws are gated. */
  camera: THREE.Camera | null = null;
  readonly stats = { slots: 0, hidden: 0, workerMs: 0, stale: 0 };

  constructor(enabled = typeof SharedArrayBuffer !== 'undefined' && globalThis.crossOriginIsolated === true,
    create: (() => Worker) | null = () => new Worker(new URL('./visibility.worker.ts', import.meta.url), { type: 'module', name: 'placement-visibility' })) {
    if (!enabled) return;
    const buffer = new SharedArrayBuffer(bufferBytes());
    this.views = layout(buffer);
    if (create) { this.worker = create(); this.worker.postMessage({ buffer }); }
  }

  get enabled() { return this.views !== null; }
  /** Shared views, exposed for tests and the in-thread fallback. */
  get shared() { return this.views; }

  /** Register a translated placement wrapper; returns its slot or -1 (then it is never culled and occludes nothing). */
  register(wrapper: THREE.Object3D, bounds: { min: Vec3; max: Vec3 }, viewDistance: number, occluders: unknown) {
    const v = this.views;
    if (!v) return -1;
    const slot = this.free.pop() ?? (this.high < MAX_SLOTS ? this.high++ : -1);
    if (slot < 0) return -1;
    const p = wrapper.position, at = slot * SLOT_STRIDE;
    v.slots.set([p.x + bounds.min[0], p.y + bounds.min[1], p.z + bounds.min[2],
      p.x + bounds.max[0], p.y + bounds.max[1], p.z + bounds.max[2], viewDistance, 0], at);
    const boxes = readBoxes(occluders);
    for (const [b, box] of boxes.entries()) {
      const c = Math.cos(box.yaw ?? 0), s = Math.sin(box.yaw ?? 0), base = (slot * MAX_SLOT_BOXES + b) * BOX_STRIDE;
      for (let i = 0; i < 8; i++) {
        const u = i & 1 ? box.max[0] : box.min[0], y = i & 2 ? box.max[1] : box.min[1], w = i & 4 ? box.max[2] : box.min[2];
        // docs/specs/building-models.md: x = u cos(yaw) - v sin(yaw), z = u sin(yaw) + v cos(yaw).
        v.boxes[base + i * 3] = p.x + u * c - w * s;
        v.boxes[base + i * 3 + 1] = p.y + y;
        v.boxes[base + i * 3 + 2] = p.z + u * s + w * c;
      }
    }
    v.boxCounts[slot] = boxes.length;
    Atomics.store(v.control, CONTROL.slotCount, this.high);
    wrapper.userData.visibilitySlot = slot;
    wrapper.traverse(o => { if ((o as THREE.Mesh).isMesh) this.meshes.set(o, slot); });
    this.stats.slots++;
    return slot;
  }

  unregister(slot: number) {
    const v = this.views;
    if (!v || slot < 0) return;
    v.slots[slot * SLOT_STRIDE + 7] = 0; v.boxCounts[slot] = 0;
    this.free.push(slot); this.stats.slots--;
  }

  /** In the scene and within its own view distance: may occlude and may be culled. */
  setActive(slot: number | undefined, active: boolean) {
    if (this.views && slot !== undefined && slot >= 0) this.views.slots[slot * SLOT_STRIDE + 7] = active ? ACTIVE : 0;
  }

  /** Once per frame before rendering: adopt the previous result and publish this frame's camera. */
  update(camera: THREE.PerspectiveCamera) {
    const v = this.views;
    if (!v) return;
    this.camera = camera;
    camera.updateMatrixWorld();
    const done = Atomics.load(v.control, CONTROL.resultSeq), half = done & 1;
    const eye = v.resultEye[half];
    camera.getWorldDirection(this.forward);
    const position = camera.position, moved = Math.hypot(position.x - eye[0], position.y - eye[1], position.z - eye[2]);
    const turned = this.forward.x * eye[5] + this.forward.y * eye[6] + this.forward.z * eye[7];
    this.fresh = done > 0 && done === this.seq && moved <= STALE_METRES && turned >= STALE_COS;
    if (this.fresh) { this.snapshot.set(v.results[half]); this.stats.hidden = eye[3]; this.stats.workerMs = eye[4]; }
    else this.stats.stale++;
    this.vp.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    v.camera.set(this.vp.elements, CAMERA.vp);
    v.camera[CAMERA.eye] = position.x; v.camera[CAMERA.eye + 1] = position.y; v.camera[CAMERA.eye + 2] = position.z;
    v.camera[CAMERA.near] = (camera.near ?? .1) + .05;
    // The worker copies the camera with the result; keep the view direction beside it for the staleness check.
    const next = this.seq + 1;
    v.resultEye[next & 1][5] = this.forward.x; v.resultEye[next & 1][6] = this.forward.y; v.resultEye[next & 1][7] = this.forward.z;
    this.seq = next;
    Atomics.store(v.control, CONTROL.cameraSeq, next);
    Atomics.notify(v.control, CONTROL.cameraSeq);
  }

  private hidden(slot: number | undefined) {
    return slot !== undefined && slot >= 0 && (this.snapshot[slot >> 5] & (1 << (slot & 31))) !== 0;
  }

  /** Whether a primary-camera draw of `object` can be skipped this frame. */
  occluded(object: THREE.Object3D, camera: THREE.Camera) {
    if (!this.fresh || camera !== this.camera) return false;
    const slot = this.meshes.get(object);
    if (slot !== undefined) return this.hidden(slot);
    const cached = this.batches.get(object);
    if (cached !== undefined && cached >> 1 === this.seq) return (cached & 1) === 1;
    const owners = staticBatchPlacements(object);
    let hidden = !!owners?.length;
    if (owners) for (const owner of owners) if (!this.hidden(owner.userData.visibilitySlot as number | undefined)) { hidden = false; break; }
    this.batches.set(object, this.seq << 1 | (hidden ? 1 : 0));
    return hidden;
  }

  dispose() { this.worker?.terminate(); this.worker = null; this.views = null; }
}

export const placementVisibility = new PlacementVisibility();
