import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { ACTIVE, BOX_STRIDE, computeOcclusion, DepthGrid, MAX_SLOT_BOXES, SLOT_STRIDE } from '../../src/world/occlusionRaster';
import { CAMERA, CONTROL } from '../../src/world/visibilityLayout';
import { PlacementVisibility } from '../../src/world/placementVisibility';

function camera(position: [number, number, number], target: [number, number, number]) {
  const c = new THREE.PerspectiveCamera(60, 2, .1, 6000);
  c.position.set(...position); c.lookAt(...target); c.updateMatrixWorld();
  return c;
}
function vp(c: THREE.PerspectiveCamera) { return new THREE.Matrix4().multiplyMatrices(c.projectionMatrix, c.matrixWorldInverse).elements; }
function corners(min: number[], max: number[]) {
  const out: number[] = [];
  for (let i = 0; i < 8; i++) out.push(i & 1 ? max[0] : min[0], i & 2 ? max[1] : min[1], i & 4 ? max[2] : min[2]);
  return out;
}

test('a box occluder hides only bounds wholly behind its covered pixels', () => {
  const c = camera([0, 2, 0], [0, 2, -100]), m = vp(c), grid = new DepthGrid();
  grid.clear();
  // A 40 m wide block rising 6 m above the eye, 20-30 m ahead.
  expect(grid.rasterBox(m, .15, corners([-20, -5, -30], [20, 8, -20]))).toBe(true);
  expect(grid.occluded(m, .15, [-3, 0, -60], [3, 4, -50])).toBe(true);
  // Beside the silhouette, peeking over it, or in front of it: visible.
  expect(grid.occluded(m, .15, [50, 0, -70], [70, 4, -60])).toBe(false);
  expect(grid.occluded(m, .15, [-3, 0, -200], [3, 120, -190])).toBe(false);
  expect(grid.occluded(m, .15, [-3, 0, -15], [3, 8, -10])).toBe(false);
  // Straddling the front face: part of it is in front of the occluder.
  expect(grid.occluded(m, .15, [-3, 0, -25], [3, 8, -15])).toBe(false);
});

test('anything crossing the near plane fails open', () => {
  const c = camera([0, 2, 0], [0, 2, -100]), m = vp(c), grid = new DepthGrid();
  grid.clear();
  expect(grid.rasterBox(m, .15, corners([-20, -5, -30], [20, 25, 1]))).toBe(false);
  expect(grid.occluded(m, .15, [-3, 0, -60], [3, 8, -50])).toBe(false);
  grid.rasterBox(m, .15, corners([-20, -5, -30], [20, 25, -20]));
  expect(grid.occluded(m, .15, [-3, 0, -60], [3, 8, 5])).toBe(false);
});

function slots(count: number) {
  return { slots: new Float32Array(count * SLOT_STRIDE), boxCounts: new Uint8Array(count),
    boxes: new Float32Array(count * MAX_SLOT_BOXES * BOX_STRIDE), slotCount: count };
}

test('only active, in-range placements occlude; occluded placements are reported by slot', () => {
  const c = camera([0, 2, 0], [0, 2, -100]), m = vp(c), views = slots(2), bits = new Uint32Array(1);
  views.slots.set([-21, 0, -31, 21, 26, -19, 500, ACTIVE], 0);
  views.boxCounts[0] = 1; views.boxes.set(corners([-20, -5, -30], [20, 25, -20]), 0);
  views.slots.set([-3, 0, -60, 3, 8, -50, 500, ACTIVE], SLOT_STRIDE);
  expect(computeOcclusion(views, m, [0, 2, 0], .15, new DepthGrid(), bits)).toBe(1);
  expect(bits[0]).toBe(0b10);
  views.slots[7] = 0;
  expect(computeOcclusion(views, m, [0, 2, 0], .15, new DepthGrid(), bits)).toBe(0);
  views.slots[7] = ACTIVE; views.slots[6] = 10; // Beyond its own view distance it is not drawn, so it hides nothing.
  expect(computeOcclusion(views, m, [0, 2, 0], .15, new DepthGrid(), bits)).toBe(0);
});

function run(visibility: PlacementVisibility) {
  // In-thread stand-in for the worker: same layout, same pass.
  const v = visibility.shared!, seq = Atomics.load(v.control, CONTROL.cameraSeq), half = seq & 1;
  const eye = v.camera.subarray(CAMERA.eye, CAMERA.eye + 3);
  computeOcclusion({ slots: v.slots, boxCounts: v.boxCounts, boxes: v.boxes, slotCount: Atomics.load(v.control, CONTROL.slotCount) },
    v.camera.subarray(CAMERA.vp, CAMERA.vp + 16), eye, v.camera[CAMERA.near], new DepthGrid(), v.results[half]);
  v.resultEye[half].set(eye); Atomics.store(v.control, CONTROL.resultSeq, seq);
}

test('primary draws of hidden placements and fully hidden batches are skipped one frame late; stale or other cameras draw', () => {
  const visibility = new PlacementVisibility(true, null), c = camera([0, 2, 0], [0, 2, -100]);
  const front = new THREE.Group(), back = new THREE.Group(), mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());
  front.position.set(0, 0, -25); back.position.set(0, 0, -55); back.add(mesh);
  // A yawed box: a quarter turn swaps the local u/v extents in the world (spec: x = u cos - v sin, z = u sin + v cos).
  const occluders = { version: 1, boxes: [{ min: [-5, -5, -20], max: [5, 25, 20], yaw: Math.PI / 2 }] };
  const a = visibility.register(front, { min: [-21, 0, -6], max: [21, 26, 6] }, 500, occluders);
  const b = visibility.register(back, { min: [-3, 0, -5], max: [3, 8, 5] }, 500, undefined);
  const v = visibility.shared!, base = a * MAX_SLOT_BOXES * BOX_STRIDE;
  const xs = [0, 1, 2, 3, 4, 5, 6, 7].map(i => v.boxes[base + i * 3]), zs = [0, 1, 2, 3, 4, 5, 6, 7].map(i => v.boxes[base + i * 3 + 2]);
  expect(Math.min(...xs)).toBeCloseTo(-20, 4); expect(Math.max(...zs)).toBeCloseTo(-20, 4);
  visibility.setActive(a, true); visibility.setActive(b, true);
  visibility.update(c);
  expect(visibility.occluded(mesh, c)).toBe(false); // No result yet.
  run(visibility); visibility.update(c);
  expect(visibility.occluded(mesh, c)).toBe(true);
  expect(visibility.occluded(mesh, camera([0, 2, 0], [0, 2, -100]))).toBe(false);
  run(visibility);
  c.position.x += 2; c.updateMatrixWorld(); visibility.update(c);
  expect(visibility.occluded(mesh, c)).toBe(false);
  // A slot reused by a new placement never inherits the previous placement's hidden bit.
  c.position.x -= 2; c.updateMatrixWorld(); visibility.update(c); run(visibility); visibility.update(c);
  expect(visibility.occluded(mesh, c)).toBe(true);
  visibility.unregister(b);
  const other = new THREE.Group(), fresh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());
  other.position.set(0, 0, -55); other.add(fresh);
  expect(visibility.register(other, { min: [-3, 0, -5], max: [3, 8, 5] }, 500, undefined)).toBe(b);
  visibility.setActive(b, true);
  expect(visibility.occluded(fresh, c)).toBe(false);
  // The result for the camera published before registration does not apply; the next one does.
  run(visibility); visibility.update(c);
  expect(visibility.occluded(fresh, c)).toBe(false);
  run(visibility); visibility.update(c);
  expect(visibility.occluded(fresh, c)).toBe(true);
  visibility.unregister(b); visibility.setActive(a, false);
});
