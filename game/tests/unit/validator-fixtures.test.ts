// Integration: the game's physics world (shared .col reader + its own KCC) on the validator's synthetic fixtures
// (tools/validator/fixtures/good, written by the Python writer with meshopt sections). The game must agree with
// the validator contract in tools/validator/fixtures/expected.json: 12 cm and 25 cm curbs are climbed, 40 cm blocks.
import { describe, test, expect, beforeAll } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld, DT } from '../../src/physics/world';
import { MAT } from '../../src/shared/col/materials';

const FIX = path.resolve(__dirname, '../../../tools/validator/fixtures/good');
const expected = JSON.parse(fs.readFileSync(path.resolve(FIX, '../expected.json'), 'utf8')).good.curb_steps as Record<string, string>;
const Y0 = 2.0;
// tile c_1_2 origin (62.5, 0, 125); platforms (tile-local x0..x1, z 10..18): pipeline/turku3d/col/fixtures.py PLATFORMS
const PLATFORMS: [string, number, number][] = [['curb_12cm', 4, 14], ['curb_25cm', 20, 30], ['step_40cm', 36, 46]];
const OX = 62.5, OZ = 125;

function buf(f: string) { const b = fs.readFileSync(path.join(FIX, f)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer; }

describe('game KCC on validator fixtures', () => {
  let pw: PhysicsWorld;
  beforeAll(async () => {
    pw = new PhysicsWorld(RAPIER);
    await pw.loadTile('c_1_2', buf('c_1_2.col'));
    await pw.loadTile('c_2_2', buf('c_2_2.col'));
    pw.world.step();
  });

  test('meshopt fixture tiles read through the shared reader; per-triangle material lookup works', () => {
    const hit = pw.raycast({ x: OX + 9, y: 10, z: OZ + 14 }, { x: 0, y: -1, z: 0 }, 20)!;
    expect(hit.point.y).toBeCloseTo(Y0 + 0.12, 3);
    expect(hit.material).toBe(MAT.sidewalk);
    expect(pw.raycast({ x: OX + 9, y: 10, z: OZ + 5 }, { x: 0, y: -1, z: 0 }, 20)!.material).toBe(MAT.road);
    expect(pw.raycast({ x: OX + 9, y: 10, z: OZ + 22 }, { x: 0, y: -1, z: 0 }, 20)!.material).toBe(MAT.sidewalk);
  });

  for (const speed of [1.5, 3.6]) {
    for (const [name, x0, x1] of PLATFORMS) {
      test(`${name} at ${speed} m/s: ${expected[name]}`, () => {
        const x = OX + (x0 + x1) / 2;
        pw.spawnCharacter({ x, y: Y0 + 0.02, z: OZ + 22 });
        for (let k = 0; k < 20; k++) pw.fixedStep({ vx: 0, vz: 0, jump: false }, null);
        let maxY = -Infinity;
        const n = Math.round(6 / speed / DT); // 6 m north: z 22 -> 16, the platform edge is at z 18
        for (let k = 0; k < n; k++) { pw.fixedStep({ vx: 0, vz: -speed, jump: false }, null); maxY = Math.max(maxY, pw.characterState()!.pos.y); }
        const s = pw.characterState()!;
        const climbed = s.pos.z < OZ + 17.5 && maxY > Y0 + 0.08;
        expect(climbed ? 'climb' : 'block').toBe(expected[name]);
      });
    }
  }
});
