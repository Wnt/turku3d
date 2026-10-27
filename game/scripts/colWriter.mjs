// .col v1.0 writer (RAW sections, docs/specs/col-format.md) for synthetic fixtures: the dev tiles
// (make-dev-tiles.mjs) and the draft-tile e2e check. Positions are tile-local (origin i * 62.5, 0, j * 62.5).
import zlib from 'node:zlib';

const COL = 62.5;
function align8(n) { return (n + 7) & ~7; }
export function writeCol({ i, j, positions, indices, materials, prims, flags, meta }) {
  const metaBytes = Buffer.from(JSON.stringify(meta), 'utf8');
  const primBuf = Buffer.alloc(prims.length * 48);
  prims.forEach((p, k) => {
    const o = k * 48; primBuf.writeUInt8(p.kind, o); primBuf.writeUInt8(p.material, o + 1); primBuf.writeUInt16LE(0, o + 2);
    p.center.forEach((v, q) => primBuf.writeFloatLE(v, o + 4 + 4 * q));
    p.rotation.forEach((v, q) => primBuf.writeFloatLE(v, o + 16 + 4 * q));
    p.params.forEach((v, q) => primBuf.writeFloatLE(v, o + 32 + 4 * q));
  });
  const sections = [
    ['POSN', Buffer.from(positions.buffer, positions.byteOffset, positions.byteLength), positions.length / 3, 12],
    ['INDX', Buffer.from(indices.buffer, indices.byteOffset, indices.byteLength), indices.length, 4],
    ['MATL', Buffer.from(materials), materials.length, 1],
    ['PRIM', primBuf, prims.length, 48],
    ['META', metaBytes, metaBytes.length, 1],
  ];
  const tableEnd = 96 + sections.length * 24;
  let off = align8(tableEnd);
  const placed = sections.map(([tag, buf, count, stride]) => { const e = { tag, buf, count, stride, offset: off }; off = align8(off + buf.length); return e; });
  const total = off;
  const out = Buffer.alloc(total);
  out.write('TCOL', 0, 'latin1'); out.writeUInt16LE(1, 4); out.writeUInt16LE(0, 6); out.writeUInt32LE(96, 8); out.writeUInt32LE(flags, 12);
  out.writeDoubleLE(i * COL, 16); out.writeDoubleLE(0, 24); out.writeDoubleLE(j * COL, 32);
  out.writeInt32LE(i, 40); out.writeInt32LE(j, 44);
  const mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
  for (let k = 0; k < positions.length; k++) { mn[k % 3] = Math.min(mn[k % 3], positions[k]); mx[k % 3] = Math.max(mx[k % 3], positions[k]); }
  for (const p of prims) { const r = Math.max(...p.params.slice(0, 3)) + (p.kind === 1 ? 0 : p.params[1]); for (let q = 0; q < 3; q++) { mn[q] = Math.min(mn[q], p.center[q] - r); mx[q] = Math.max(mx[q], p.center[q] + r); } }
  for (let q = 0; q < 3; q++) { out.writeFloatLE(mn[q], 48 + 4 * q); out.writeFloatLE(mx[q], 60 + 4 * q); }
  out.writeUInt32LE(positions.length / 3, 72); out.writeUInt32LE(indices.length / 3, 76); out.writeUInt32LE(prims.length, 80);
  out.writeUInt16LE(sections.length, 84); out.writeUInt16LE(1, 86); out.writeUInt32LE(total, 88); out.writeUInt32LE(0, 92);
  placed.forEach((e, k) => {
    const o = 96 + k * 24; out.write(e.tag, o, 'latin1'); out.writeUInt16LE(0, o + 4); out.writeUInt16LE(0, o + 6);
    out.writeUInt32LE(e.offset, o + 8); out.writeUInt32LE(e.buf.length, o + 12); out.writeUInt32LE(e.count, o + 16); out.writeUInt32LE(e.stride, o + 20);
    e.buf.copy(out, e.offset);
  });
  out.writeUInt32LE(zlib.crc32(out) >>> 0, 92);
  return out;
}
