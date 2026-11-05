import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import '../../src/render/numericSizes';

test('texture sizes never hand undefined to the target vector (a depth texture before its first size)', () => {
  const stored: unknown[] = [];
  const target = { set(...v: unknown[]) { stored.push(...v); return target; } };
  const depth = new THREE.DepthTexture(undefined as unknown as number, undefined as unknown as number);
  depth.source.getSize(target as unknown as THREE.Vector3);
  expect(stored).toEqual([0, 0, 1]);
  expect(new THREE.DataTexture(new Uint8Array(4 * 6), 3, 2).width).toBe(3);
  expect(new THREE.Texture().width).toBe(0);
});
