import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { styleScenery } from '../../src/render/sceneryMaterial';
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
