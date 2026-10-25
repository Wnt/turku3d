// Python writer -> TypeScript reader round trip (docs/specs/col-format.md; Task 1.10 "decoded
// geometry identical to source within 2 mm"). Positions must in fact be bit-identical.
// Cases come from `python -m turku3d.col.roundtrip`; set COL_RT_DIR to reuse a generated directory.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { MeshoptDecoder } from 'meshoptimizer';
import { crc32, readCol, splitByClass } from '../../../game/src/shared/col/index.ts';
import type { ColTile } from '../../../game/src/shared/col/index.ts';

const REPO = resolve(import.meta.dirname, '../../..');
const PY = process.env.PIPELINE_PY ?? join(REPO, 'pipeline/.venv/bin/python');

function caseDir(): string {
  if (process.env.COL_RT_DIR) return process.env.COL_RT_DIR;
  const d = mkdtempSync(join(tmpdir(), 'colrt-'));
  execFileSync(PY, ['-m', 'turku3d.col.roundtrip', '--out', d], { cwd: join(REPO, 'pipeline'), stdio: 'pipe' });
  return d;
}

function canonical(t: ColTile): { bits: number[][]; mats: number[] } {
  const u = new Uint32Array(t.positions.buffer, t.positions.byteOffset, t.positions.length);
  const rows: number[][] = [];
  for (let k = 0; k < t.header.triangleCount; k++) {
    const c = [0, 1, 2].map((j) => { const v = t.indices[k * 3 + j]; return [u[v * 3], u[v * 3 + 1], u[v * 3 + 2]]; });
    const lt = (a: number[], b: number[]) => a[0] !== b[0] ? a[0] < b[0] : a[1] !== b[1] ? a[1] < b[1] : a[2] < b[2];
    let m = 0;
    for (let j = 1; j < 3; j++) if (lt(c[j], c[m])) m = j;
    const r = [...c.slice(m), ...c.slice(0, m)].flat();
    r.push(t.materials[k]);
    rows.push(r);
  }
  rows.sort((a, b) => { for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] - b[i]; return 0; });
  return { bits: rows.map((r) => r.slice(0, 9)), mats: rows.map((r) => r[9]) };
}

await MeshoptDecoder.ready;
const dir = caseDir();
const expected = JSON.parse(readFileSync(join(dir, 'expected.json'), 'utf8'));
const f32 = new Float32Array(1), u32 = new Uint32Array(f32.buffer);
const bitsToF = (b: number) => { u32[0] = b; return f32[0]; };

for (const [name, exp] of Object.entries(expected) as [string, any][]) {
  test(`round trip ${name}`, () => {
    const buf = readFileSync(join(dir, `${name}.col`));
    const t = readCol(buf, { decoder: MeshoptDecoder });
    assert.equal(t.id, exp.id);
    assert.deepEqual(t.header.origin, exp.origin);
    assert.equal(t.header.flags, exp.flags);
    assert.equal(t.header.vertexCount, exp.vertex_count);
    assert.equal(t.header.triangleCount, exp.triangle_count);
    assert.deepEqual(t.header.bboxMin, exp.bbox_min);
    assert.deepEqual(t.header.bboxMax, exp.bbox_max);
    assert.deepEqual(Object.fromEntries(t.sections.map((s) => [s.tag, s.encoding])), exp.encodings);
    const c = canonical(t);
    assert.equal(c.bits.length, exp.triangles_bits.length);
    let maxErr = 0;
    for (let k = 0; k < c.bits.length; k++) {
      assert.deepEqual(c.bits[k], exp.triangles_bits[k], `${name}: triangle ${k} bits`);
      for (let j = 0; j < 9; j++) {
        const world = t.header.origin[j % 3] + bitsToF(c.bits[k][j]);
        maxErr = Math.max(maxErr, Math.abs(world - exp.triangles_world[k][j]));
      }
    }
    assert.ok(maxErr <= 0.002, `${name}: max world error ${maxErr} m > 2 mm`);
    assert.deepEqual(c.mats, exp.triangles_material);
    assert.equal(t.primitives.length, exp.primitives.length);
    t.primitives.forEach((p, i) => {
      const e = exp.primitives[i];
      assert.equal(p.kind, e.kind);
      assert.equal(p.material, e.material);
      assert.deepEqual(p.center, e.center);
      assert.deepEqual(p.rotation, e.rotation);
      assert.deepEqual(p.params, e.params);
    });
    assert.deepEqual(t.meta, exp.meta);
    // class split keeps every triangle exactly once
    const split = splitByClass(t);
    assert.equal(split.reduce((s, m) => s + m.indices.length / 3, 0), t.header.triangleCount);
  });
}

test('reader verifies the CRC-32', () => {
  const good = new Uint8Array(readFileSync(join(dir, 'grid_meshopt_v0.col')));
  const t = readCol(good, { decoder: MeshoptDecoder });
  const matl = t.sections.find((s) => s.tag === 'MATL')!;
  const bad = good.slice();
  bad[matl.offset] ^= 0x01; // flip one material byte
  assert.throws(() => readCol(bad, { decoder: MeshoptDecoder }), /CRC/);
  const t2 = readCol(bad, { decoder: MeshoptDecoder, verifyCrc: false });
  assert.notEqual(t2.materials[0], t.materials[0]);
});

test('reader version / tag / encoding rules', () => {
  const name = 'grid_raw';
  const good = new Uint8Array(readFileSync(join(dir, `${name}.col`)));
  const fix = (b: Uint8Array) => { new DataView(b.buffer).setUint32(92, crc32(b, 92, 96), true); return b; };
  const major = good.slice(); new DataView(major.buffer).setUint16(4, 2, true);
  assert.throws(() => readCol(fix(major)), /version_major/);
  const minor = good.slice(); new DataView(minor.buffer).setUint16(6, 7, true);
  assert.equal(readCol(fix(minor)).header.versionMinor, 7);
  // rename the META section tag -> unknown tag must be skipped
  const unk = good.slice();
  const dv = new DataView(unk.buffer);
  const n = dv.getUint16(84, true);
  for (let k = 0; k < n; k++) {
    const o = 96 + k * 24;
    if (String.fromCharCode(...unk.subarray(o, o + 4)) === 'META') unk.set([0x58, 0x58, 0x58, 0x58], o);
  }
  const t = readCol(fix(unk));
  assert.equal(t.meta, null);
  assert.ok(t.sections.some((s) => s.tag === 'XXXX'));
  // MATL with encoding 1 -> reject
  const enc = good.slice();
  const dv2 = new DataView(enc.buffer);
  for (let k = 0; k < n; k++) {
    const o = 96 + k * 24;
    if (String.fromCharCode(...enc.subarray(o, o + 4)) === 'MATL') dv2.setUint16(o + 4, 1, true);
  }
  assert.throws(() => readCol(fix(enc)), /encoding/);
  // truncated
  assert.throws(() => readCol(good.subarray(0, good.length - 8)), /file_bytes/);
  // meshopt file without a decoder
  const mo = readFileSync(join(dir, 'grid_meshopt_v0.col'));
  assert.throws(() => readCol(mo), /no decoder/);
});

test('every case file was checked', () => {
  const cols = readdirSync(dir).filter((f) => f.endsWith('.col')).map((f) => f.replace(/\.col$/, '')).sort();
  assert.deepEqual(cols, Object.keys(expected).sort());
});
