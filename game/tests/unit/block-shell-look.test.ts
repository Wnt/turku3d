import * as THREE from 'three/webgpu';
import { expect, test } from 'vitest';
import { SHELL_ROOF, shellRoofs, shellWallPigments, styleBlockLod, trimShare } from '../../src/render/sceneryMaterial';

/** An HD placement wrapper (as BuildingModelCache.attach builds it) with one orthophoto roof mapped uv = (x, z) / 250. */
function placement(block: string, x: number, map: THREE.Texture, program?: Record<string, unknown>) {
  const wrapper = new THREE.Group(); wrapper.position.set(x, 0, 40); wrapper.userData = { archetype: 'a', block };
  const node = new THREE.Group(); node.userData = { surface: 'roof' };
  const geometry = new THREE.PlaneGeometry(10, 10).rotateX(-Math.PI / 2);
  const pos = geometry.getAttribute('position'), uv = geometry.getAttribute('uv');
  for (let i = 0; i < pos.count; i++) uv.setXY(i, (pos.getX(i) + x) / 250, (pos.getZ(i) + 40) / 250);
  node.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ map })));
  wrapper.add(node);
  if (program) { const walls = new THREE.Group(); walls.userData = { surface: 'wall', facade_kit: program }; wrapper.add(walls); }
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

test('shell walls take their building\'s trim share, roofs keep the roof pigment', () => {
  const program = { wall: '#a97b5a', trim: '#e1e3dc', bay: 3, storey: 3, opening: [.5, .5, .25, .3], bands: [0, 0, .3, 0], size: [30, 20] };
  const share = trimShare(program);
  expect(share).toBeGreaterThan(.1); expect(share).toBeLessThanOrEqual(.6);
  const owner = new THREE.Group(); owner.add(placement('b1', 100, new THREE.Texture(), program));
  const wall = new THREE.Color('#a97b5a');
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(new Array(6).fill(0), 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute([wall.r, wall.g, wall.b, ...SHELL_ROOF], 3));
  const shell = new THREE.Group(); shell.add(new THREE.Mesh(geometry));
  styleBlockLod(shell, owner, undefined, undefined, shellWallPigments(owner));
  const colour = geometry.getAttribute('color');
  expect(colour.getX(0)).toBeGreaterThan(wall.r); // mixed towards the light trim
  expect([colour.getX(1), colour.getY(1), colour.getZ(1)].map(v => +v.toFixed(3))).toEqual([...SHELL_ROOF]);
});
