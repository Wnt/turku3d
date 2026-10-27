// Boxed-in parked cars against tiny hand-built tiles (shipped tile-local positions plus origin).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MAT, PRIM_KIND } from '../../../game/src/shared/col/index.ts';
import type { ColPrimitive, ColTile } from '../../../game/src/shared/col/index.ts';
import { checkParked, withParked, type ParkedCar } from '../src/parked.ts';

const origin: [number, number, number] = [100, 0, 200];
// Model +Z (front) faces world +Z at yaw 0; the van spans z 197.5..202.5 around (100, 200).
const van: ParkedCar = { object_id: 'van', x: 100, y: 3, z: 200, yaw: 0, width: 1.98, height: 2.05, length: 5 };

/** Vertical quads across X at world depth z, from bottom to top. */
function tile(walls: [number, number, number, number, number, number][], primitives: ColPrimitive[] = []) {
  const positions: number[] = [], indices: number[] = [], materials: number[] = [];
  for (const [x0, x1, z, bottom, top, material] of walls) {
    const base = positions.length / 3;
    for (const [x, y] of [[x0, bottom], [x1, bottom], [x1, top], [x0, top]]) positions.push(x - origin[0], y, z - origin[2]);
    indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
    materials.push(material, material);
  }
  return { header: { origin }, positions: new Float32Array(positions), indices: new Uint32Array(indices), materials: new Uint8Array(materials), primitives } as unknown as ColTile;
}

const everywhere = () => true;

test('fence behind and wall ahead box the parked car in', () => {
  const result: any = checkParked([tile([[97, 103, 196.5, 3, 4.2, MAT.fence], [97, 103, 204, 2.8, 8, MAT.building]])], [van], everywhere);
  assert.equal(result.status, 'fail');
  assert.deepEqual(result.boxedIn.map((b: any) => [b.object_id, b.where, b.material]), [['van', 'front-and-rear', 'building']]);
  assert.equal(withParked({ status: 'skipped' }, result).status, 'fail');
});

test('a nose-in car with its rear exit clear passes, kerbs and overhangs are ignored', () => {
  const walls: [number, number, number, number, number, number][] = [
    [97, 103, 203.5, 2.8, 8, MAT.building], [97, 103, 196, 2.9, 3.2, MAT.wall], [97, 103, 196, 5.2, 8, MAT.building],
  ];
  const result: any = checkParked([tile(walls)], [van], everywhere);
  assert.equal(result.status, 'pass');
  assert.equal(withParked({ status: 'skipped' }, result).status, 'pass');
});

test('rotated box and cylinder primitives inside the side clearance fail', () => {
  const yaw = Math.PI / 6;
  const bench: ColPrimitive = { kind: PRIM_KIND.BOX, material: MAT.street_furniture, flags: 0, center: [2, 3.4, 0],
    rotation: [0, Math.sin(yaw / 2), 0, Math.cos(yaw / 2)], params: [0.3, 0.4, 0.9, 0] };
  assert.equal((checkParked([tile([], [bench])], [van], everywhere) as any).boxedIn[0].where, 'side');
  const pole: ColPrimitive = { kind: PRIM_KIND.CYLINDER, material: MAT.pole, flags: 0, center: [-2.4, 5, 0],
    rotation: [0, 0, 0, 1], params: [2, 0.1, 0, 0] };
  assert.equal((checkParked([tile([], [pole])], [van], everywhere) as any).boxedIn[0].material, 'pole');
  assert.equal(checkParked([tile([], [{ ...pole, center: [-2.7, 5, 0] }])], [van], everywhere).status, 'pass');
});

test('cars outside the owned tiles are not judged', () => {
  assert.equal(checkParked([tile([])], [van], () => false).status, 'skipped');
});
