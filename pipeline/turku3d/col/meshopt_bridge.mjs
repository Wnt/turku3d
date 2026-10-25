// Node bridge used by turku3d/col/meshopt.py to run the official meshoptimizer encoder (npm WASM).
// usage: node meshopt_bridge.mjs <meshoptimizer pkg dir> <tmpdir> <vertexCount> <indexCount> <optimize 0|1> <vertexVersion>
// reads  <tmpdir>/pos.bin (f32x3) and idx.bin (u32)
// writes <tmpdir>/pos_out.bin, idx_out.bin (after optional reorderMesh), posn.enc, indx.enc
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [pkg, dir, vcS, icS, optS, verS] = process.argv.slice(2);
const { MeshoptEncoder } = await import(pathToFileURL(join(pkg, 'meshopt_encoder.js')).href);
await MeshoptEncoder.ready;

const vc = Number(vcS), ic = Number(icS), optimize = optS === '1', version = Number(verS);
const rd = (f) => { const b = readFileSync(join(dir, f)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); };
let pos = new Float32Array(rd('pos.bin'));
let idx = new Uint32Array(rd('idx.bin'));
if (pos.length !== vc * 3 || idx.length !== ic) throw new Error('size mismatch');

if (optimize && ic > 0) {
  // reorderMesh = optimizeVertexCache + optimizeVertexFetchRemap; remaps idx in place, returns old->new remap.
  const [remap, unique] = MeshoptEncoder.reorderMesh(idx, true, false);
  const out = new Float32Array(unique * 3);
  for (let i = 0; i < vc; i++) {
    const r = remap[i];
    if (r === 0xffffffff) continue; // unreferenced vertex: dropped
    out[r * 3] = pos[i * 3]; out[r * 3 + 1] = pos[i * 3 + 1]; out[r * 3 + 2] = pos[i * 3 + 2];
  }
  pos = out;
}
const nv = pos.length / 3;
const posn = nv > 0 ? MeshoptEncoder.encodeVertexBufferLevel(new Uint8Array(pos.buffer), nv, 12, 2, version) : new Uint8Array(0);
const indx = idx.length > 0 ? MeshoptEncoder.encodeIndexBuffer(new Uint8Array(idx.buffer), idx.length, 4) : new Uint8Array(0);
writeFileSync(join(dir, 'pos_out.bin'), new Uint8Array(pos.buffer));
writeFileSync(join(dir, 'idx_out.bin'), new Uint8Array(idx.buffer));
writeFileSync(join(dir, 'posn.enc'), posn);
writeFileSync(join(dir, 'indx.enc'), indx);
