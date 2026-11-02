import { describe, expect, it } from 'vitest';
import * as THREE from 'three/webgpu';
import { RainSurfaceIndex, type RainMeshData } from '../../src/render/weather/surfaceIndex';
function horizontal(y: number, size: number): RainMeshData {
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(size, size));
  mesh.rotation.x = -Math.PI / 2; mesh.position.y = y; mesh.updateMatrixWorld();
  return { position: mesh.geometry.attributes.position.array, stride: 3, offset: 0, normalized: false,
    index: mesh.geometry.index!.array as Uint16Array, matrix: mesh.matrixWorld.toArray() };
}
describe('rain roof/ground spatial index', () => {
  it('finds the roof rather than the road below, including bucket boundaries', () => {
    const index = new RainSurfaceIndex([horizontal(2, 100), horizontal(14, 10)]);
    expect(index.height(1, 1)?.y).toBeCloseTo(14);
    expect(index.height(1, 1)?.flat).toBe(true);
    expect(index.height(16, 16)?.y).toBeCloseTo(2);
    expect(index.height(-8, -8)?.y).toBeCloseTo(2);
    expect(index.height(100, 100)).toBeNull();
  });
  it('keeps slope height and rejects splashes on steep surfaces', () => {
    const mesh = horizontal(0, 10); mesh.matrix = new THREE.Matrix4().makeRotationZ(Math.PI / 3).multiply(new THREE.Matrix4().fromArray(mesh.matrix)).toArray();
    const index = new RainSurfaceIndex([mesh]);
    expect(index.height(1, 0)?.flat).toBe(false);
  });
  it('reads normalized interleaved attributes with their matrix transform', () => {
    const index = new RainSurfaceIndex([{ position: new Int16Array([7,-32767,0,-32767,7,32767,0,-32767,7,0,0,32767]), stride: 4, offset: 1, normalized: true,
      index: null, matrix: new THREE.Matrix4().makeTranslation(8, 3, -4).toArray() }]);
    expect(index.height(8, -4)?.y).toBeCloseTo(3);
    expect(index.height(0, 0)).toBeNull();
  });
});
