import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { prepareDetached } from '../../src/world/prepareDetached';

test('preparing a detached tile includes parent transforms and leaves visibility insertion to the loader', async () => {
  const parent = new THREE.Group(), model = new THREE.Group();
  parent.rotation.x = -Math.PI / 2;
  model.position.set(10, 20, 30);
  await prepareDetached(model, parent, async () => {
    expect(model.parent).toBe(parent); expect(parent.children).not.toContain(model);
    const position = new THREE.Vector3().setFromMatrixPosition(model.matrixWorld);
    expect(position.x).toBe(10); expect(position.y).toBeCloseTo(30, 10); expect(position.z).toBeCloseTo(-20, 10);
    await Promise.resolve();
  });
  expect(model.parent).toBeNull();
  parent.add(model);
  expect(parent.children).toContain(model);
});

test('aborted/failed preparation restores detached ownership', async () => {
  const model = new THREE.Group(), parent = new THREE.Group();
  await expect(prepareDetached(model, parent, async () => { throw new Error('cancelled'); })).rejects.toThrow('cancelled');
  expect(model.parent).toBeNull(); expect(parent.children).toEqual([]);
});
