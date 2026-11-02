import { expect, it } from 'vitest';
import * as THREE from 'three/webgpu';
import { summerFoliage } from '../../src/world/nature/foliageCards';

it('preserves bright green in normalized byte colours instead of wrapping into purple', () => {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([-1, 0, 0, 1, 0, 0, 0, 1, 0], 3));
  geometry.setAttribute('_wind', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 1], 4));
  geometry.setAttribute('color', new THREE.Uint8BufferAttribute([255, 255, 255, 255, 255, 255, 255, 255, 255], 3, true));
  geometry.setIndex([0, 1, 2]);
  summerFoliage(geometry);
  const color = geometry.getAttribute('color'), position = geometry.getAttribute('position');
  for (let i = 0; i < 3; i++) {
    expect(color.getY(i)).toBe(1); expect(color.getY(i)).toBeGreaterThan(color.getX(i));
  }
  const once = [...position.array]; summerFoliage(geometry);
  expect([...position.array]).toEqual(once); expect(color.getY(0)).toBe(1);
});
