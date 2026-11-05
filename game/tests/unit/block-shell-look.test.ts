import * as THREE from 'three/webgpu';
import { expect, test } from 'vitest';
import { shellRoofs, styleBlockLod } from '../../src/render/sceneryMaterial';

/** An HD placement wrapper (as BuildingModelCache.attach builds it) with one orthophoto roof mapped uv = (x, z) / 250. */
function placement(block: string, x: number, map: THREE.Texture) {
  const wrapper = new THREE.Group(); wrapper.position.set(x, 0, 40); wrapper.userData = { archetype: 'a', block };
  const node = new THREE.Group(); node.userData = { surface: 'roof' };
  const geometry = new THREE.PlaneGeometry(10, 10).rotateX(-Math.PI / 2);
  const pos = geometry.getAttribute('position'), uv = geometry.getAttribute('uv');
  for (let i = 0; i < pos.count; i++) uv.setXY(i, (pos.getX(i) + x) / 250, (pos.getZ(i) + 40) / 250);
  node.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ map })));
  wrapper.add(node);
  return wrapper;
}

test('a block shell samples its HD roofs\' orthophoto through their planar world mapping', () => {
  const owner = new THREE.Group(), map = new THREE.Texture();
  owner.add(placement('b1', 100, map), placement('b1', 130, map));
  const roof = shellRoofs(owner).get('b1')!;
  expect(roof.map).toBe(map);
  const at = (x: number, z: number) => [roof.u[0] * x + roof.u[1] * z + roof.u[2], roof.v[0] * x + roof.v[1] * z + roof.v[2]];
  expect(at(125, 60)[0]).toBeCloseTo(0.5, 5); expect(at(125, 60)[1]).toBeCloseTo(0.24, 5);
});

test('roofs that are not one planar projection keep the shell pigment', () => {
  const owner = new THREE.Group(), map = new THREE.Texture(), odd = placement('b1', 100, map);
  const uv = ((odd.children[0].children[0] as THREE.Mesh).geometry).getAttribute('uv');
  uv.setXY(0, 0.9, 0.1);
  owner.add(odd, placement('b1', 130, map));
  expect(shellRoofs(owner).has('b1')).toBe(false);
});

test('glazed shells with the migration\'s kit grid get the grid variant of the window material', () => {
  const shell = new THREE.Group(), owner = new THREE.Group();
  const plain = new THREE.Mesh(new THREE.BoxGeometry()), gridded = new THREE.Mesh(new THREE.BoxGeometry());
  plain.userData.lod = gridded.userData.lod = 'walls-glazed';
  gridded.geometry.setAttribute('_shell_grid', new THREE.Float32BufferAttribute(new Array(gridded.geometry.getAttribute('position').count * 4).fill(3), 4));
  shell.add(plain, gridded);
  styleBlockLod(shell, owner);
  expect((plain.material as THREE.Material).name).toBe('distant-city-buildings-lod-glazed');
  expect((gridded.material as THREE.Material).name).toBe('distant-city-buildings-lod-glazed-grid');
});

test('shells with their own normals shade from them and get glass and wall roughness; normal-free ones stay flat', () => {
  const shell = new THREE.Group(), owner = new THREE.Group();
  const normals = new THREE.Mesh(new THREE.BoxGeometry()), bare = new THREE.Mesh(new THREE.BoxGeometry());
  bare.geometry.deleteAttribute('normal');
  normals.userData.lod = bare.userData.lod = 'walls-glazed';
  shell.add(normals, bare);
  styleBlockLod(shell, owner);
  const shaded = normals.material as THREE.MeshStandardNodeMaterial, flat = bare.material as THREE.MeshStandardNodeMaterial;
  expect(shaded.flatShading).toBe(false);
  expect(flat.flatShading).toBe(true);
  expect(shaded.roughnessNode).toBeTruthy();
});
