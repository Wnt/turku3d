import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { sceneryMaterial, styleScenery } from '../../src/render/sceneryMaterial';
import { SharedMaterials } from '../../src/render/sharedMaterials';
import { disposeModelResources } from '../../src/world/disposeModel';

test('distant nodes share each layer graph until its final model retires', () => {
  const shared = new SharedMaterials();
  const model = () => {
    const root = new THREE.Group();
    for (const layer of ['ground', 'buildings', 'vegetation']) {
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
      mesh.userData.layer = layer; root.add(mesh);
    }
    styleScenery(root, shared); return root;
  };
  const first = model(), second = model();
  const material = (root: THREE.Group, index: number) => (root.children[index] as THREE.Mesh).material as THREE.Material;
  for (let i = 0; i < 3; i++) expect(material(first, i)).toBe(material(second, i));
  expect(material(first, 0)).not.toBe(material(first, 1));
  const release = vi.fn(); material(first, 1).addEventListener('dispose', release);
  styleScenery(first, shared); expect(release).not.toHaveBeenCalled();
  disposeModelResources(first, shared); expect(release).not.toHaveBeenCalled();
  disposeModelResources(second, shared); expect(release).toHaveBeenCalledTimes(1);
  const next = model(); expect(material(next, 1)).not.toBe(material(second, 1));
  disposeModelResources(next, shared);
});

// Shells contain no authored opening coordinates, so their graph must not invent light.
test('plain far shells stay dark and texture atlases retain distinct samplers', async () => {
  const { sceneryMaterial } = await import('../../src/render/sceneryMaterial');
  const shell = sceneryMaterial('buildings');
  expect(shell.emissiveNode).toBeNull();
  const atlas = new THREE.DataTexture(new Uint8Array([80,60,40,255]),1,1);
  const mapped = sceneryMaterial('buildings-mapped', undefined, atlas);
  expect(mapped.map).toBe(atlas); expect(mapped.emissiveNode).not.toBeNull();
});

test('plain scenery reuses program keys while coverage and atlas bindings stay distinct', () => {
  const ground = sceneryMaterial('ground');
  const shell = sceneryMaterial('buildings');
  expect(ground).not.toBe(shell);
  expect(ground.customProgramCacheKey()).toBe(shell.customProgramCacheKey());
  const texture = () => new THREE.DataTexture(new Uint8Array([80, 60, 40, 255]), 1, 1);
  const first = texture(), second = texture();
  const masked = sceneryMaterial('regional', first);
  const otherMask = sceneryMaterial('regional', second);
  expect(masked.customProgramCacheKey()).not.toBe(ground.customProgramCacheKey());
  expect(masked.customProgramCacheKey()).not.toBe(otherMask.customProgramCacheKey());
  const mapped = sceneryMaterial('buildings-mapped', undefined, first);
  const otherMap = sceneryMaterial('buildings-mapped', undefined, second);
  expect(mapped.customProgramCacheKey()).not.toBe(otherMap.customProgramCacheKey());
  for (const material of [ground, shell, masked, otherMask, mapped, otherMap]) material.dispose();
  first.dispose(); second.dispose();
});
