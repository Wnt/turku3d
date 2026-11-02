import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { nearestFogDepth, SceneryFog } from '../../src/world/sceneryFog';
import { REFLECTION_LAYER } from '../../src/render/reflections';

test('depth bound includes the closest corner for arbitrary camera pitch and heading', () => {
  const box = new THREE.Box3(new THREE.Vector3(-20, 10, -800), new THREE.Vector3(200, 90, -400));
  const eye = new THREE.Vector3(12, 35, 80);
  for (const direction of [[1, 2, -3], [-1, -2, 3], [0, 1, 0], [0, 0, -1]]) {
    const forward = new THREE.Vector3(...direction).normalize(), depths = [];
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y])
      for (const z of [box.min.z, box.max.z]) depths.push(new THREE.Vector3(x, y, z).sub(eye).dot(forward));
    expect(nearestFogDepth(box, eye, forward)).toBeCloseTo(Math.min(...depths), 8);
  }
});

test('opaque fog skips distant scenery, retains intersecting boxes and restores clear weather', () => {
  const cull = new SceneryFog(), camera = new THREE.PerspectiveCamera();
  const model = new THREE.Mesh(new THREE.BoxGeometry(100, 100, 100)); model.position.z = -600;
  cull.follow(camera, new THREE.Fog(0, 100, 450));
  expect(cull.visible(model)).toBe(false);
  const crossing = new THREE.Mesh(new THREE.BoxGeometry(100, 100, 300)); crossing.position.z = -600;
  expect(cull.visible(crossing)).toBe(true);
  cull.follow(camera, new THREE.Fog(0, 500, 12000)); expect(cull.visible(model)).toBe(true);
  cull.follow(camera, null); expect(cull.visible(model)).toBe(true);
  cull.follow(camera, new THREE.FogExp2(0, .1)); expect(cull.visible(model)).toBe(true);
});

test('reflection-layer models remain available to the mirrored camera', () => {
  const cull = new SceneryFog(), camera = new THREE.PerspectiveCamera();
  const model = new THREE.Group(), mesh = new THREE.Mesh(new THREE.BoxGeometry(100, 100, 100));
  mesh.layers.enable(REFLECTION_LAYER); model.add(mesh); model.position.z = -1000;
  cull.follow(camera, new THREE.Fog(0, 100, 450)); expect(cull.visible(model)).toBe(true);
});

test('shadow casters and materials outside opaque fog keep their pass contribution', () => {
  const cull = new SceneryFog(); cull.follow(new THREE.PerspectiveCamera(), new THREE.Fog(0, 100, 450));
  for (const kind of ['shadow', 'fog-opt-out', 'transparent']) {
    const material = new THREE.MeshBasicMaterial({ fog: kind !== 'fog-opt-out', transparent: kind === 'transparent' });
    const model = new THREE.Mesh(new THREE.BoxGeometry(100, 100, 100), material);
    model.position.z = -1000; model.castShadow = kind === 'shadow';
    expect(cull.visible(model)).toBe(true);
  }
});
