import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
// @ts-expect-error r186 renderer internals have no DefinitelyTyped declarations.
import RenderLists from 'three/src/renderers/common/RenderLists.js';
import ClippingContext from 'three/src/renderers/common/ClippingContext.js';
import { REFLECTION_LAYER, WaterCoverage } from '../../src/render/reflections';
import { ReflectionScope } from '../../src/render/reflectionScope';

/* eslint-disable @typescript-eslint/no-explicit-any */
const box = (x: number, z: number, size = 4, height = 4, reflective = true) => {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(size, height, size), new THREE.MeshBasicMaterial());
  mesh.position.set(x, height / 2, z);
  if (reflective) mesh.layers.enable(REFLECTION_LAYER);
  return mesh;
};

/** A river along z = 0 (x -400..400, 30 m wide), tiles near and far, scenery, population, sky and lights. */
function world(random: () => number) {
  const scene = new THREE.Scene(), tiles: THREE.Group[] = [];
  const water = new THREE.Mesh(new THREE.PlaneGeometry(800, 30).rotateX(-Math.PI / 2));
  for (const [tx, tz] of [[0, 0], [250, 0], [0, 250], [500, 500], [-250, -250]]) {
    const tile = new THREE.Group(); tile.position.set(tx, 0, tz);
    for (let i = 0; i < 12; i++) tile.add(box(random() * 240 - 120, random() * 240 - 120, 2 + random() * 30, 1 + random() * 40));
    const placement = new THREE.Group(); placement.position.set(random() * 200 - 100, 0, random() * 200 - 100);
    for (let i = 0; i < 6; i++) placement.add(box(random() * 20, random() * 20, 3, 5 + random() * 30));
    const vegetation = new THREE.Group();
    for (let i = 0; i < 8; i++) vegetation.add(box(random() * 200 - 100, random() * 200 - 100, 3, 8, false));
    const leaf = new THREE.Object3D();
    tile.add(placement, vegetation, leaf);
    scene.add(tile); tiles.push(tile);
  }
  const scenery = new THREE.Group(); for (let i = 0; i < 10; i++) scenery.add(box(2000 + i * 50, 0, 40, 30, false));
  const population = new THREE.Group(); for (let i = 0; i < 10; i++) population.add(box(i * 5, 10, 2, 2, false));
  const sky = box(0, 0, 9000, 9000); sky.frustumCulled = false;
  const light = new THREE.HemisphereLight(); light.layers.enable(REFLECTION_LAYER);
  scene.add(scenery, population, sky, light);
  scene.updateMatrixWorld(true);
  const coverage = new WaterCoverage(); coverage.add(water);
  return { scene, tiles, scenery, coverage };
}

function projector() {
  vi.stubGlobal('requestAnimationFrame', () => 1);
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement }) as any;
  Object.assign(renderer, { _renderLists: new RenderLists(), lighting: { enabled: true, getNode: () => ({ setLights: () => {} }) }, sortObjects: false,
    backend: { get: () => ({}) } });
  return renderer;
}

test('reflection renders skip only subtrees that cannot appear: identical reflected draws and lights, far fewer visits', () => {
  let seed = 7; const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const { scene, tiles, scenery, coverage } = world(random);
  const scope = new ReflectionScope();
  for (const tile of [...tiles, scenery]) scope.add(tile);
  const renderer = projector(), project = vi.spyOn(renderer, '_projectObject');
  const plane = new THREE.Object3D(); plane.updateMatrixWorld();
  let visitsWith = 0, visitsWithout = 0, drawn = 0;
  for (let trial = 0; trial < 60; trial++) {
    const eye = new THREE.PerspectiveCamera(60, 1.6, 0.1, 4000);
    eye.position.set(random() * 600 - 300, 1 + random() * 60, random() * 300 - 150);
    eye.lookAt(random() * 600 - 300, 0, random() * 300 - 150); eye.updateMatrixWorld();
    // The mirrored camera below the plane, seeing the reflection layer only.
    const camera = eye.clone(); camera.position.y = -eye.position.y; camera.up.set(0, -1, 0);
    camera.lookAt(new THREE.Vector3(0, 0, -1).applyQuaternion(eye.quaternion).add(eye.position).setY(-eye.position.y + (eye.position.y - 0)));
    camera.layers.set(REFLECTION_LAYER); camera.updateMatrixWorld();
    const water = { coverage, eye, plane };
    camera.userData.waterReflection = water;
    const frustum = new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
    const list = (scoped: boolean) => {
      project.mockClear();
      if (scoped) scope.begin(scene, camera, water);
      const internal = renderer as any;
      // r186's projection culls against a module frustum that only _renderScene sets: test each mesh against this
      // camera's frustum instead, through r186's own Mesh.intersectsFrustum.
      const intersects = THREE.Mesh.prototype.intersectsFrustum;
      const culling = vi.spyOn(THREE.Mesh.prototype, 'intersectsFrustum').mockImplementation(function (this: THREE.Mesh) { return intersects.call(this, frustum); });
      const renderList = internal._renderLists.get(scene, camera, internal.lighting);
      try {
        renderList.begin();
        internal._projectObject(scene, camera, 0, renderList, new ClippingContext());
        renderList.finish();
      } finally { culling.mockRestore(); }
      if (scoped) scope.end();
      const visits = project.mock.calls.length;
      // GameRenderer's per-draw water cull, applied after projection.
      const objects = renderList.opaque.map((item: { object: THREE.Object3D }) => item.object)
        .filter((o: THREE.Object3D) => coverage.reflectsObject(o, eye, 0));
      return { objects: new Set(objects), lights: renderList.lightsArray.length, visits };
    };
    const full = list(false), scoped = list(true);
    expect(scoped.objects).toEqual(full.objects);
    expect(scoped.lights).toBe(full.lights);
    visitsWith += scoped.visits; visitsWithout += full.visits; drawn += full.objects.size;
  }
  // Every hidden object is visible again afterwards.
  scene.traverse(o => expect(o.visible).toBe(true));
  expect(drawn).toBeGreaterThan(60);
  expect(visitsWith).toBeLessThan(visitsWithout * 0.6);
});

test('a model gaining children is measured again, and removed models are forgotten', () => {
  const scene = new THREE.Scene(), scope = new ReflectionScope(), coverage = new WaterCoverage();
  const water = new THREE.Mesh(new THREE.PlaneGeometry(40, 40).rotateX(-Math.PI / 2)); coverage.add(water);
  const tile = new THREE.Group(); tile.add(box(1000, 1000)); scene.add(tile); scope.add(tile);
  const eye = new THREE.PerspectiveCamera(); eye.position.set(0, 5, 30); eye.updateMatrixWorld();
  const camera = eye.clone(); camera.position.y = -5; camera.lookAt(0, -5, 0); camera.updateMatrixWorld();
  const plane = new THREE.Object3D(); plane.updateMatrixWorld();
  const reflection = { coverage, eye, plane };
  scene.updateMatrixWorld(true);
  scope.begin(scene, camera, reflection); expect(tile.visible).toBe(false); scope.end(); expect(tile.visible).toBe(true);
  // A tall building attached next to the water later: the tile now reflects.
  tile.add(box(0, -10, 6, 60)); scene.updateMatrixWorld(true);
  scope.begin(scene, camera, reflection); expect(tile.visible).toBe(true); scope.end();
  scope.remove(tile);
  scope.begin(scene, camera, reflection); expect(scope.stats.models).toBe(0); scope.end();
});
