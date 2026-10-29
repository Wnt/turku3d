import { test, expect, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { NodeAttributes } from '../../src/render/nodeAttributes';
import { disposeDraw } from '../../src/render/sharedMaterials';

test('all pass-specific attributes release with the object while shared geometry stays alive', () => {
  const remove = vi.fn(), tracker = new NodeAttributes(remove), geometry = new THREE.BoxGeometry();
  const a = new THREE.Mesh(geometry), b = new THREE.Mesh(geometry);
  const current = new THREE.InterleavedBuffer(new Float32Array(16), 16);
  const previous = new THREE.InterleavedBuffer(new Float32Array(16), 16);
  const x = new THREE.InterleavedBufferAttribute(current, 4, 0), y = new THREE.InterleavedBufferAttribute(current, 4, 4);
  const p = new THREE.InterleavedBufferAttribute(previous, 4, 0);
  const attributes = [geometry.attributes.position, x];
  const first = { object: a, geometry, getAttributes: () => attributes };
  tracker.track(first); tracker.track(first);
  tracker.track({ object: a, geometry, getAttributes: () => [y, p] });
  tracker.track({ object: b, geometry, getAttributes: () => [x] });
  disposeDraw(a);
  expect(remove.mock.calls.flat()).toEqual([p]);
  disposeDraw(b);
  expect(remove.mock.calls.flat()).toEqual([p, x, y]);
  geometry.dispose(); expect(remove).toHaveBeenCalledTimes(3);
});

test('geometry retirement also releases generated fields, never ordinary geometry attributes', () => {
  const remove = vi.fn(), tracker = new NodeAttributes(remove), object = new THREE.Mesh(new THREE.BoxGeometry());
  const generated = new THREE.Float32BufferAttribute([1, 2, 3], 3);
  tracker.track({ object, geometry: object.geometry, getAttributes: () => [object.geometry.attributes.position, generated] });
  object.geometry.dispose(); disposeDraw(object);
  expect(remove).toHaveBeenCalledExactlyOnceWith(generated);
});

test('renderer retirement releases surviving owners once and removes their event listeners', () => {
  const remove = vi.fn(), tracker = new NodeAttributes(remove), object = new THREE.Mesh(new THREE.BoxGeometry());
  const generated = new THREE.Float32BufferAttribute([1, 2, 3], 3);
  tracker.track({ object, geometry: object.geometry, getAttributes: () => [generated] });
  tracker.dispose(); tracker.dispose(); disposeDraw(object); object.geometry.dispose();
  expect(remove).toHaveBeenCalledExactlyOnceWith(generated);
});
