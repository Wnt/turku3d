// Node-side physics tests on the generated dev tiles (npm run dev-tiles must have run; npm test does it via pretest).
import { describe, test, expect, beforeAll } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld, DT, CHARACTER } from '../../src/physics/world';
import { readCol, crc32 } from '../../src/physics/colTile';
import { enToEngine, OFFICE_EN } from '../../src/core/origin';
import { MAT } from '../../src/shared/col/materials';

const DIR = path.resolve(__dirname, '../../public/dev-tiles');
const manifest = JSON.parse(fs.readFileSync(path.join(DIR, 'manifest.json'), 'utf8'));
const load = (id: string) => { const b = fs.readFileSync(path.join(DIR, manifest.collision[id].url)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer; };
const G = 2.0;

async function worldWith(ids: string[]) {
  const pw = new PhysicsWorld(RAPIER);
  for (const id of ids) await pw.loadTile(id, load(id));
  pw.world.step(); // queries need one step after adding colliders
  return pw;
}
function walk(pw: PhysicsWorld, vx: number, vz: number, seconds: number) {
  const n = Math.round(seconds / DT);
  for (let k = 0; k < n; k++) pw.fixedStep({ vx, vz, jump: false }, null);
  return pw.characterState()!;
}

describe('.col reader', () => {
  test('all dev tiles parse, CRC ok, header matches manifest', async () => {
    for (const [id, e] of Object.entries<any>(manifest.collision)) {
      const buf = load(id);
      const t = await readCol(buf, { checkCrc: true });
      expect(t.indices.length / 3).toBe(e.tris);
      expect(t.prims.length).toBe(e.prims);
      expect(`c_${t.tileI}_${t.tileJ}`).toBe(id);
      expect(t.origin).toEqual([t.tileI * 62.5, 0, t.tileJ * 62.5]);
      expect(t.flags).toBe(e.flags);
      for (const m of t.materials) expect(m).toBeGreaterThan(0);
    }
  });
  test('crc mismatch is detected', async () => {
    const buf = load('c_2_3'); new Uint8Array(buf)[200] ^= 0xff;
    await expect(readCol(buf, { checkCrc: true })).rejects.toThrow(/CRC/);
  });
  test('crc32 matches zlib', async () => {
    const zlib = await import('node:zlib');
    const d = new TextEncoder().encode('turku3d');
    expect(crc32(d)).toBe(zlib.crc32(d) >>> 0);
  });
});

describe('physics world on dev tiles', () => {
  let pw: PhysicsWorld;
  const spawn = enToEngine(OFFICE_EN.E, OFFICE_EN.N, G);
  beforeAll(async () => { pw = await worldWith(Object.keys(manifest.collision)); });

  test('spawn is at the office in c_2_3 and ground ray hits road at H=2', () => {
    expect(spawn.x).toBe(168); expect(spawn.z).toBe(246);
    const hit = pw.raycast({ x: spawn.x, y: 50, z: spawn.z }, { x: 0, y: -1, z: 0 }, 100)!;
    expect(hit.point.y).toBeCloseTo(G, 4);
    expect(hit.material).toBe(MAT.road);
    expect(hit.tile).toBe('c_2_3');
  });
  test('ray materials: curb face, sidewalk top, pole primitive, building', () => {
    expect(pw.raycast({ x: 168, y: G + 0.06, z: 245 }, { x: 0, y: 0, z: -1 }, 20)!.material).toBe(MAT.curb);
    expect(pw.raycast({ x: 168, y: 10, z: 232 }, { x: 0, y: -1, z: 0 }, 20)!.material).toBe(MAT.sidewalk);
    expect(pw.raycast({ x: 160, y: 3, z: 242 }, { x: 1, y: 0, z: 0 }, 5)!.material).toBe(MAT.pole);
    expect(pw.raycast({ x: 202, y: 30, z: 208 }, { x: 0, y: -1, z: 0 }, 40)!.material).toBe(MAT.roof);
  });
  test('character walks 5 m north and stays on the ground', () => {
    pw.spawnCharacter(spawn);
    walk(pw, 0, 0, 0.5);
    const s = walk(pw, 0, -2, 2.5);
    // single-move KCC (vy = -0.5 m/s while grounded) loses ~1-2 frames of travel to the ground contact
    expect(Math.abs(s.pos.z - 241)).toBeLessThan(0.1);
    expect(s.pos.y).toBeCloseTo(G, 1);
    expect(s.grounded).toBe(true);
  });
  test('character steps up the 12 cm curb with autostep', () => {
    pw.spawnCharacter(spawn);
    const s = walk(pw, 0, -2, 7); // 14 m north: crosses the curb at z = 238
    expect(s.pos.z).toBeLessThan(234);
    expect(s.pos.y).toBeGreaterThan(G + 0.1);
    expect(s.pos.y).toBeLessThan(G + 0.16);
    expect(s.groundMaterial).toBe(MAT.sidewalk);
  });
  test('character climbs the 16 cm stair and reaches the 0.96 m platform', () => {
    pw.spawnCharacter({ x: 131, y: G, z: 224 });
    const s = walk(pw, 0, -1.5, 8);
    expect(s.pos.z).toBeLessThan(216);
    expect(s.pos.y).toBeCloseTo(G + 0.96, 1);
  });
  test('character is blocked by the building wall', () => {
    pw.spawnCharacter({ x: 186, y: G, z: 208 });
    const s = walk(pw, 3, 0, 4);
    expect(s.pos.x).toBeLessThan(192 - CHARACTER.radius + 0.05);
    expect(s.pos.y).toBeLessThan(G + 0.3);
  });
  test('jump goes up and lands', () => {
    pw.spawnCharacter({ x: 100, y: G, z: 100 });
    walk(pw, 0, 0, 0.5);
    pw.fixedStep({ vx: 0, vz: 0, jump: true }, null);
    let maxY = 0;
    for (let k = 0; k < 120; k++) { pw.fixedStep({ vx: 0, vz: 0, jump: false }, null); maxY = Math.max(maxY, pw.characterState()!.pos.y); }
    expect(maxY).toBeGreaterThan(G + 0.8);
    expect(pw.characterState()!.pos.y).toBeCloseTo(G, 1);
  });
  test('car drives north on the road', () => {
    pw.setCharacterEnabled(false);
    pw.spawnVehicle({ x: 120, y: G + 0.8, z: 150 }, Math.PI / 2); // yaw +90 deg: chassis +X -> -Z (north)
    for (let k = 0; k < 60; k++) pw.fixedStep({ vx: 0, vz: 0, jump: false }, { throttle: 0, steer: 0, brake: 0, handbrake: false });
    for (let k = 0; k < 240; k++) pw.fixedStep({ vx: 0, vz: 0, jump: false }, { throttle: 1, steer: 0, brake: 0, handbrake: false });
    const v = pw.vehicleState()!;
    expect(v.pos.z).toBeLessThan(140);
    expect(Math.abs(v.pos.x - 120)).toBeLessThan(1.5);
    expect(v.wheels.filter((w) => w.contact).length).toBe(4);
  });
  test('unload removes colliders', async () => {
    const p2 = await worldWith(['c_2_3']);
    expect(p2.raycast({ x: 168, y: 50, z: 246 }, { x: 0, y: -1, z: 0 }, 100)).not.toBeNull();
    expect(p2.unloadTile('c_2_3')).toBe(true);
    p2.world.step();
    expect(p2.raycast({ x: 168, y: 50, z: 246 }, { x: 0, y: -1, z: 0 }, 100)).toBeNull();
    expect(p2.totals().tris).toBe(0);
  });
  test('seams: walking across four collision tiles does not fall or snag', async () => {
    pw.spawnCharacter({ x: 120, y: G, z: 120 }); // c_1_1
    let minY = Infinity;
    for (let k = 0; k < 6 * 60; k++) { pw.fixedStep({ vx: 1.5, vz: 1.5, jump: false }, null); minY = Math.min(minY, pw.characterState()!.pos.y); }
    const s = pw.characterState()!;
    expect(s.pos.x).toBeGreaterThan(128); expect(s.pos.z).toBeGreaterThan(128); // crossed x=125 and z=125
    expect(minY).toBeGreaterThan(G - 0.05);
  });
});

// Canary: Rapier 0.21 JS reports featureId 0 for every trimesh face hit, which is why physics/triLocator.ts exists.
// If this starts failing, Rapier fixed it and the locator can be replaced by hit.featureId.
test('rapier trimesh featureId is still unusable (canary)', () => {
  const w = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  w.createCollider(RAPIER.ColliderDesc.trimesh(new Float32Array([0, 0, 0, 0, 0, 1, 1, 0, 0, 5, 0, 0, 5, 0, 1, 6, 0, 0]), new Uint32Array([0, 1, 2, 3, 4, 5])));
  w.step();
  const h = w.castRayAndGetNormal(new RAPIER.Ray({ x: 5.2, y: 1, z: 0.2 }, { x: 0, y: -1, z: 0 }), 20, true)!;
  expect(h.featureId).toBe(0); // the right answer would be 1
});
