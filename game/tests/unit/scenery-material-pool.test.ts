import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { sceneryMaterial, styleScenery, translateLegacyGround } from '../../src/render/sceneryMaterial';
import farGround from '../../src/render/farGroundPalette.json';
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

test('far ground built with the old fixed palette takes the near materials distant colours', () => {
  const quantized = (rgb: number[][]) => new THREE.BufferAttribute(new Uint8Array(rgb.flat().map(v => Math.round(v * 255))), 3, true);
  const geometry = new THREE.BufferGeometry();
  // Old grass, white (unmatched granite curb), old stone, and a survey colour that is not part of the old palette.
  geometry.setAttribute('color', quantized([[.22, .29, .12], [1, 1, 1], [.35, .34, .32], [.24, .29, .16]]));
  translateLegacyGround(geometry);
  const colour = geometry.getAttribute('color') as THREE.BufferAttribute;
  const at = (i: number) => [colour.getX(i), colour.getY(i), colour.getZ(i)].map(v => Math.round(v * 255));
  const near = (rgb: number[]) => rgb.map(v => Math.round(v * 255));
  expect(at(0)).toEqual(near(farGround.near.grass));
  expect(at(1)).toEqual(near(farGround.near.granite_curb));
  expect(at(2)).toEqual(near(farGround.near.stone));
  expect(at(3)).toEqual(near([.24, .29, .16]));
  translateLegacyGround(geometry); // once per geometry: a translated pigment is never translated again
  expect(at(0)).toEqual(near(farGround.near.grass));
});
