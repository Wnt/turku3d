import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
// @ts-expect-error r186 renderer internals have no DefinitelyTyped declarations.
import RenderLists from 'three/src/renderers/common/RenderLists.js';
import ClippingContext from 'three/src/renderers/common/ClippingContext.js';
import { Environment } from '../../src/render/environment';
import { RiverMaterial, waterSurface } from '../../src/render/water';
import { REFLECTION_LAYER, type WaterReflection } from '../../src/render/reflections';
import { ReflectionScope } from '../../src/render/reflectionScope';
import { freezeModel } from '../../src/world/frozenTransforms';

/* eslint-disable @typescript-eslint/no-explicit-any */
/** A river reach of two water tiles along z = 0, the north bank's building in a third tile, the camera on the south
 *  bank looking across: a reflection render must draw the north bank, through the game's own reflection path
 *  (RiverMaterial's reflector and its mirror camera, frozen tiles, ReflectionScope, r186 projection and the per-draw
 *  water cull), also after the reach's mirror moved to a mesh whose normal array was released. */
function riverside() {
  vi.stubGlobal('requestAnimationFrame', () => 1);
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement }) as any;
  const scene = new THREE.Scene(); scene.updateMatrix(); scene.matrixAutoUpdate = false;
  const env = new Environment(scene, renderer, 'medium');
  const water = (x: number) => {
    const tile = new THREE.Group(); tile.position.set(x, 0, 0);
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(250, 40).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial());
    mesh.userData.material_key = 'water';
    tile.add(mesh); scene.add(tile); tile.updateMatrixWorld(true);
    waterSurface(mesh); // styling samples the mirror plane (render/materials.ts)
    return { tile, mesh };
  };
  const a = water(0), b = water(250);
  const bank = new THREE.Group(); bank.position.set(0, 0, -80);
  const building = new THREE.Mesh(new THREE.BoxGeometry(60, 30, 20), new THREE.MeshBasicMaterial());
  // Over the second water tile, so the bank is still mirrored once the first tile has gone.
  building.position.set(290, 15, 0); building.layers.enable(REFLECTION_LAYER); bank.add(building); scene.add(bank);
  const river = new RiverMaterial(a.mesh, env, 'medium', true, renderer);
  river.coverage.add(a.mesh); river.coverage.add(b.mesh);
  for (const tile of [a.tile, b.tile, bank]) freezeModel(tile);
  const camera = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 6000);
  camera.position.set(290, 4, 30); camera.lookAt(290, 4, -80); camera.updateMatrixWorld();
  return { renderer, scene, env, river, a, b, bank, building, camera };
}

/** Run the river's reflector once and return what its render would draw on the reflection layer. */
function reflected(world: ReturnType<typeof riverside>, scope: ReflectionScope | null) {
  const { scene, camera, river } = world;
  world.scene.updateMatrixWorld();
  const projector = world.renderer;
  Object.assign(projector, { _renderLists: new RenderLists(), lighting: { enabled: true, getNode: () => ({ setLights: () => {} }) },
    sortObjects: false, backend: { get: () => ({}) } });
  let drawn: THREE.Object3D[] | null = null;
  const stub = {
    getDrawingBufferSize: (v: THREE.Vector2) => v.set(1600, 900), getRenderTarget: () => null, setRenderTarget: () => {},
    getMRT: () => null, setMRT: () => {}, autoClear: true, clear: () => {},
    render: (s: THREE.Scene, mirror: THREE.PerspectiveCamera) => {
      const water = mirror.userData.waterReflection as WaterReflection;
      scope?.begin(s, mirror, water);
      const frustum = new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(mirror.projectionMatrix, mirror.matrixWorldInverse),
        mirror.coordinateSystem);
      const intersects = THREE.Mesh.prototype.intersectsFrustum;
      const culling = vi.spyOn(THREE.Mesh.prototype, 'intersectsFrustum').mockImplementation(function (this: THREE.Mesh) { return intersects.call(this, frustum); });
      try {
        const list = projector._renderLists.get(s, mirror, projector.lighting);
        list.begin(); projector._projectObject(s, mirror, 0, list, new ClippingContext()); list.finish();
        const level = water.plane.matrixWorld.elements[13];
        drawn = list.opaque.map((item: { object: THREE.Object3D }) => item.object)
          .filter((o: THREE.Object3D) => water.coverage.reflectsObject(o, water.eye, level));
      } finally { culling.mockRestore(); scope?.end(); }
    },
  };
  const reflector = (river as any).reflection.reflector;
  reflector.updateBefore({ scene, camera, renderer: stub, material: river.material });
  return drawn as THREE.Object3D[] | null;
}

test('a reflection over a reach draws the opposite bank, with and without the reflection scope', () => {
  const world = riverside();
  for (const scope of [null, new ReflectionScope()]) {
    if (scope) for (const tile of [world.a.tile, world.b.tile, world.bank]) scope.add(tile);
    // The sky is on the reflection layer too.
    expect(reflected(world, scope)).toEqual([world.env.sky, world.building]);
  }
});

test('after the reach mirror moves to a mesh whose normal array was released, the bank is still reflected', () => {
  const world = riverside(), scope = new ReflectionScope();
  for (const tile of [world.b.tile, world.bank]) scope.add(tile);
  // Uploaded render arrays are released (world/releaseArrays.ts), then the mirror's tile unloads (materials.release).
  (world.b.mesh.geometry.getAttribute('normal') as THREE.BufferAttribute).array = new Float32Array(0);
  world.river.coverage.remove(world.a.mesh);
  world.a.tile.removeFromParent();
  world.river.attach(world.b.mesh);
  const target = (world.river as any).reflection.target as THREE.Object3D;
  expect([...target.quaternion.toArray(), ...target.position.toArray()].every(Number.isFinite)).toBe(true);
  expect(reflected(world, scope)).toEqual([world.env.sky, world.building]);
});
