import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { materialColor } from 'three/tsl';
// @ts-expect-error r186 internal node cache has no DefinitelyTyped declaration.
import NodeManager from 'three/src/renderers/common/nodes/NodeManager.js';
// @ts-expect-error r186 internal render object has no DefinitelyTyped declaration.
import RenderObject from 'three/src/renderers/common/RenderObject.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { materialIdentity } from '../../src/render/materialIdentity';
import { StyledMaterials } from '../../src/render/styledMaterials';
import { SharedMaterials } from '../../src/render/sharedMaterials';

// Names and material keys from the real t0_1_0/t0_2_0 shipped stair/tunnel materials.
function stair(id: string, map: THREE.Texture) {
  const source = new THREE.MeshStandardMaterial({ name: `stairs:${id}:concrete:0_mat`, map, roughness: .95 });
  source.userData = { material_key: 'draft_bridge_concrete', library_material_key: 'draft_bridge_concrete' };
  return source;
}

test('incoming identical textured materials share one built graph despite tile/object names', () => {
  const pool = new StyledMaterials(), map = new THREE.Texture(), owners: THREE.Group[] = [];
  const build = vi.fn(() => new THREE.MeshStandardNodeMaterial({ map, roughness: .95 }));
  const materials = Array.from({ length: 32 }, (_, i) => {
    const owner = new THREE.Group(); owners.push(owner);
    return pool.acquire(stair(i ? `25893146-${i}` : '205322721', map), 'draft', owner, build);
  });
  expect(new Set(materials).size).toBe(1);
  expect(build).toHaveBeenCalledOnce();
  const dispose = vi.fn(); materials[0].addEventListener('dispose', dispose);
  for (const owner of owners.slice(0, -1)) pool.release(owner);
  expect(dispose).not.toHaveBeenCalled();
  pool.release(owners.at(-1)!); expect(dispose).toHaveBeenCalledOnce();
});

test('untextured tunnel materials share across names and retain each tile owner', () => {
  const pool = new SharedMaterials(), first = new THREE.Group(), second = new THREE.Group();
  const a = new THREE.MeshStandardMaterial({ name: 'tunnel:1265779110:floor_mat', color: 0x404448, roughness: .95 });
  a.userData.material_key = 'tunnel_concrete';
  const b = a.clone(); b.name = 'tunnel:1029612237:floor_mat';
  const shared = pool.acquire(a, first); expect(pool.acquire(b, second)).toBe(shared);
  const dispose = vi.fn(); shared.addEventListener('dispose', dispose);
  pool.releaseModel(first, new Set([shared])); expect(dispose).not.toHaveBeenCalled();
  pool.releaseModel(second, new Set([shared])); expect(dispose).toHaveBeenCalledOnce();
});

test('textures, render state and semantic data remain part of material identity', () => {
  const a = stair('a', new THREE.Texture());
  a.normalMap = a.map;
  const base = materialIdentity(a);
  const variants = [
    (m: THREE.MeshStandardMaterial) => { m.map = new THREE.Texture(); },
    (m: THREE.MeshStandardMaterial) => { m.side = THREE.DoubleSide; },
    (m: THREE.MeshStandardMaterial) => { m.alphaTest = .4; },
    (m: THREE.MeshStandardMaterial) => { m.color.set(0x102030); },
    (m: THREE.MeshStandardMaterial) => { m.userData.material_key = 'props_bus_stop'; },
    (m: THREE.MeshStandardMaterial) => { m.normalScale.set(2, 2); },
  ];
  for (const edit of variants) { const b = a.clone(); edit(b); expect(materialIdentity(b)).not.toBe(base); }
  const pool = new StyledMaterials(), owner = new THREE.Group(), build = vi.fn(() => new THREE.MeshStandardNodeMaterial());
  expect(pool.acquire(a, 'draft', owner, build)).not.toBe(pool.acquire(a, 'wet', owner, build));
});

test('custom shader/render callbacks and authored node graphs remain isolated', () => {
  const a = stair('a', new THREE.Texture()), b = a.clone(); b.name = 'b';
  expect(materialIdentity(a)).toBe(materialIdentity(b));
  const hook = () => {};
  for (const property of ['onBeforeCompile', 'onBeforeRender', 'customProgramCacheKey'] as const) {
    const first = a.clone(), second = b.clone();
    if (property === 'customProgramCacheKey') first[property] = second[property] = () => 'private';
    else first[property] = second[property] = hook;
    expect(materialIdentity(first)).not.toBe(materialIdentity(second));
  }
  const pool = new StyledMaterials(), owner = new THREE.Group(), build = vi.fn();
  const first = new THREE.MeshStandardNodeMaterial(), second = new THREE.MeshStandardNodeMaterial();
  expect(pool.acquire(first, 'draft', owner, build)).toBe(first);
  expect(pool.acquire(second, 'draft', owner, build)).toBe(second);
  expect(build).not.toHaveBeenCalled();
});


test('a new static tile reuses the actual r186 built node state without another shader builder', () => {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  const nodes = new NodeManager(renderer, renderer.backend), scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(), lights = renderer.lighting.getNode(scene), context = { id: 1 };
  const map = new THREE.DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1);
  const pool = new StyledMaterials(), create = () => {
    const material = new THREE.MeshStandardNodeMaterial({ map });
    material.colorNode = materialColor.mul(.95); return material;
  };
  const builder = vi.spyOn(nodes, '_createNodeBuilder');
  const draws = ['205322721', '25893146'].map(id => {
    const owner = new THREE.Group(), material = pool.acquire(stair(id, map), 'wet', owner, create);
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(), material);
    const draw = new RenderObject(nodes, {}, renderer, mesh, material, scene, camera, lights, context, null);
    draw.onDispose = () => nodes.delete(draw);
    return draw;
  });
  expect(draws[0].geometry).not.toBe(draws[1].geometry);
  expect(draws[0].initialCacheKey).toBe(draws[1].initialCacheKey);
  const prepared = nodes.getForRender(draws[0]);
  expect(nodes.getForRender(draws[1])).toBe(prepared);
  expect(builder).toHaveBeenCalledOnce();
  for (const draw of draws) draw.dispose();
});


test.each(['color', 'emissive', 'blendColor', 'attenuationColor', 'sheenColor', 'specularColor'] as const)(
  'material identity preserves exact working-space %s components', field => {
    const first = new THREE.MeshPhysicalMaterial(), second = first.clone();
    first[field].setRGB(.2, .2, .2); second[field].setRGB(.2001, .2, .2);
    expect(first[field].getHex()).toBe(second[field].getHex()); // Legacy JSON identity loses this distinction.
    expect(materialIdentity(first)).not.toBe(materialIdentity(second));
  });

test('HDR emission above one cannot collapse into a different material', () => {
  const first = new THREE.MeshStandardMaterial(), second = first.clone();
  first.emissive.setRGB(1.1, .2, .3); second.emissive.setRGB(1.8, .2, .3);
  expect(first.emissive.getHex()).toBe(second.emissive.getHex());
  expect(materialIdentity(first)).not.toBe(materialIdentity(second));
});
