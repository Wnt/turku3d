import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { WaterCoverage } from '../../src/render/reflections';

/** A 70 m wide river along +z (x from 0 to 70), water plane at y = 0, as two triangles. */
function river() {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, -500, 70, 0, -500, 70, 0, 500, 0, 0, -500, 70, 0, 500, 0, 0, 500], 3));
  return new THREE.Mesh(geometry);
}
const onWater = (x: number, z: number) => x >= 0 && x <= 70 && z >= -500 && z <= 500;

test('never culls a sphere any of whose points reflects onto water (randomized brute force)', () => {
  const coverage = new WaterCoverage(); coverage.add(river());
  let seed = 7; const random = () => (seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 2 ** 32;
  let culled = 0;
  for (let n = 0; n < 3000; n++) {
    const eye = new THREE.Vector3(-30 + random() * 130, 0.5 + random() * 40, -200 + random() * 400);
    const center = new THREE.Vector3(-300 + random() * 670, -5 + random() * 60, -400 + random() * 800), radius = 0.5 + random() * 40;
    let visible = false;
    for (let s = 0; s < 400 && !visible; s++) {
      const u = random() * 2 - 1, phi = random() * Math.PI * 2, r = radius * Math.cbrt(random());
      const p = new THREE.Vector3(Math.sqrt(1 - u * u) * Math.cos(phi), u, Math.sqrt(1 - u * u) * Math.sin(phi)).multiplyScalar(r).add(center);
      if (p.y <= 0) continue;
      const k = eye.y / (eye.y + p.y);
      visible = onWater(eye.x + (p.x - eye.x) * k, eye.z + (p.z - eye.z) * k);
    }
    const reflects = coverage.reflects(center, radius, eye, 0);
    if (visible) expect(reflects, `eye ${eye.toArray()} sphere ${center.toArray()} r ${radius}`).toBe(true);
    if (!reflects) culled++;
  }
  expect(culled).toBeGreaterThan(600); // the test also has teeth: plenty of spheres are provably invisible
});

test('a quay-side view keeps the opposite bank and tall towers, culls its own bank and anything under water', () => {
  const coverage = new WaterCoverage(); coverage.add(river());
  const eye = new THREE.Vector3(-5, 3.5, 0); // on the west quay, 3.5 m above the water
  expect(coverage.reflects(new THREE.Vector3(90, 8, 60), 8, eye, 0)).toBe(true);    // east-bank facade
  expect(coverage.reflects(new THREE.Vector3(-80, 10, 60), 8, eye, 0)).toBe(false);  // west-bank building, same side
  expect(coverage.reflects(new THREE.Vector3(-60, 10, -60), 8, eye, 0)).toBe(false);
  expect(coverage.reflects(new THREE.Vector3(250, 40, 200), 30, eye, 0)).toBe(true); // tall tower across the river
  expect(coverage.reflects(new THREE.Vector3(30, -6, 10), 4, eye, 0)).toBe(false);   // entirely below the plane
  const camera = new THREE.Object3D(); camera.position.copy(eye); camera.updateMatrixWorld();
  const sky = new THREE.Mesh(new THREE.BoxGeometry()); sky.frustumCulled = false;
  expect(coverage.reflectsObject(sky, camera, 0)).toBe(true);
});

test('removing the last water mesh leaves an empty coverage that culls nothing', () => {
  const coverage = new WaterCoverage(), mesh = river();
  coverage.add(mesh); coverage.add(mesh);
  const eye = new THREE.Vector3(-5, 3.5, 0);
  expect(coverage.reflects(new THREE.Vector3(-80, 10, 60), 8, eye, 0)).toBe(false);
  coverage.remove(mesh);
  expect(coverage.empty).toBe(true);
  expect(coverage.reflects(new THREE.Vector3(-80, 10, 60), 8, eye, 0)).toBe(true);
});
