import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { PrototypeGeometry } from '../../src/world/prototypeGeometry';

test('prototype cache retains recent idle geometry but never evicts an active neighbour', () => {
  const cache = new PrototypeGeometry(2), create = vi.fn(() => new THREE.BoxGeometry());
  const active = cache.acquire('active', create), idle = cache.acquire('idle', create);
  const activeDisposed = vi.fn(), idleDisposed = vi.fn();
  active.addEventListener('dispose', activeDisposed); idle.addEventListener('dispose', idleDisposed);
  cache.release(idle);
  expect(cache.acquire('idle', create)).toBe(idle); cache.release(idle);
  expect(create).toHaveBeenCalledTimes(2);
  for (let i = 0; i < 10; i++) cache.release(cache.acquire(`other-${i}`, create));
  expect(idleDisposed).toHaveBeenCalledOnce(); expect(activeDisposed).not.toHaveBeenCalled();
  expect(cache.acquire('active', create)).toBe(active);
});
