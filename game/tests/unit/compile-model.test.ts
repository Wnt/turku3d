import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { context } from 'three/tsl';
import { compileModel } from '../../src/render/compileModel';

test('one-draw compilation preserves material arrays, children, visibility and renderer state across awaits', async () => {
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const originalContext = renderer.contextNode, passContext = context({}), target = new THREE.RenderTarget();
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
  const materials = [new THREE.MeshBasicMaterial(), new THREE.MeshBasicMaterial()];
  const parent = new THREE.Mesh(new THREE.BoxGeometry(), materials), child = new THREE.Mesh();
  const water = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()); water.material.name = 'river-planar';
  parent.add(child, water); scene.add(parent);
  const children = parent.children;
  const compile = vi.spyOn(renderer, 'compileAsync').mockImplementation((mesh, drawCamera, world) => {
    expect(mesh.children).toEqual([]); expect(Array.isArray((mesh as THREE.Mesh).material)).toBe(false);
    expect(mesh.frustumCulled).toBe(false); expect(renderer.contextNode).toBe(passContext);
    expect(renderer.getRenderTarget()).toBe(target); expect(drawCamera).toBe(camera); expect(world).toBe(scene);
    return Promise.resolve();
  });
  const prepared = compileModel(renderer, scene, scene, camera, target, null, passContext);
  expect(renderer.contextNode).toBe(originalContext); expect(renderer.getRenderTarget()).toBeNull();
  expect(parent.material).toBe(materials); expect(parent.children).toBe(children);
  await prepared;
  expect(compile).toHaveBeenCalledTimes(3);
  expect(parent.children).toEqual([child, water]); expect(parent.material).toBe(materials);
  expect(renderer.contextNode).toBe(originalContext); expect(parent.frustumCulled).toBe(true);
  target.dispose();
});
