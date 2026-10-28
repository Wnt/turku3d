import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { extractPoles, PoleRanges } from '../../src/world/poleExtraction';
import { BreakablePoles } from '../../src/world/poles';

const sign = (id: string) => ({ layer: 'props', prop_type: 'sign', object_id: id, tile: 't0_0_0', pole_height: 2.7 });

test('extracts individual draft nodes with their stable pole identity and world position', () => {
  const root = new THREE.Group();
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 2.7).translate(0, 1.35, 0), new THREE.MeshBasicMaterial());
  pole.userData = sign('draft');
  pole.position.set(3, 4, 5);
  root.add(pole);
  const [candidate] = extractPoles(root);
  expect(candidate.spec.id).toBe('t0_0_0:draft');
  expect(candidate.spec.height).toBe(2.7);
  expect(candidate.spec.base.x).toBeCloseTo(3, 2);
  expect(candidate.spec.base.y).toBeCloseTo(4, 2);
  expect(candidate.spec.base.z).toBeCloseTo(5, 2);
});

test('extracts packed full-tile ranges and detaches only the broken sign', () => {
  const root = new THREE.Group();
  root.position.x = 100;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([
    1, 0, 2, 1.1, 0, 2, 0.9, 0, 2, 1, 2.7, 2,
    5, 0, 6, 5.1, 0, 6, 4.9, 0, 6, 5, 2.7, 6,
  ], 3));
  geometry.setIndex([0, 1, 3, 1, 2, 3, 4, 5, 7, 5, 6, 7]);
  const batch = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  batch.userData.objects = [{ ...sign('one'), start: 0, count: 6 }, { ...sign('two'), start: 6, count: 6 }];
  root.add(batch);
  const candidates = extractPoles(root);
  expect(candidates.map(c => c.spec.id)).toEqual(['t0_0_0:one', 't0_0_0:two']);
  expect(candidates[0].spec.base.x).toBeCloseTo(101);
  expect(candidates[1].spec.base.x).toBeCloseTo(105);
  const ranges = new PoleRanges();
  ranges.detach(candidates[0]);
  expect(batch.geometry.index?.count).toBe(6);
  expect(batch.geometry.index?.array[0]).toBe(4);
  ranges.detach(candidates[1]);
  expect(batch.geometry.index?.count).toBe(0);
  expect(root.children).toHaveLength(3);
});

test('registers already loaded full poles and later draft poles, then removes evicted bodies', () => {
  const model = (id: string, x: number) => {
    const root = new THREE.Group(), node = new THREE.Group();
    node.userData = { ...sign(id), pole_id: `t0_0_0:${id}` };
    node.position.x = x;
    root.add(node);
    return root;
  };
  const full = model('full', 2), draft = model('draft', 4);
  type Event = { scene: THREE.Object3D; tile: { extras: { id: string } } };
  const events = new THREE.EventDispatcher<{ 'load-model': Event; 'dispose-model': Event }>();
  const calls: { add: string[]; remove: string[] }[] = [];
  const physics = { setPoles(add: { id: string }[], remove: string[]) {
    calls.push({ add: add.map(p => p.id), remove });
  }, breakPole() {} };
  const tiles = { modelEvents: events, models: new Set([full]) };
  const poles = new BreakablePoles(tiles as never, physics as never, () => {});
  poles.update([], { x: 0, z: 0 }, 10);
  expect(calls.at(-1)).toEqual({ add: ['t0_0_0:full'], remove: [] });
  events.dispatchEvent({ type: 'load-model', scene: draft, tile: { extras: { id: 't0_0_0' } } });
  poles.update([], { x: 0, z: 0 }, 10);
  expect(calls.at(-1)).toEqual({ add: ['t0_0_0:draft'], remove: [] });
  events.dispatchEvent({ type: 'dispose-model', scene: draft, tile: { extras: { id: 't0_0_0' } } });
  expect(calls.at(-1)).toEqual({ add: [], remove: ['t0_0_0:draft'] });
  poles.dispose();
});
