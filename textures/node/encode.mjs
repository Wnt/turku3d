// KTX2 encoder (basis_universal WASM via ktx2-encoder 0.4.2; verified in docs/research/ai-textures.md).
// usage: node encode.mjs <in.png> <out.ktx2> <etc1s|uastc|uastc-normal> [--srgb|--linear] [--quality N]
// Prints one JSON line: {in, out, mode, bytes, ms}.
// Notes: albedo -> etc1s + sRGB; ORM -> etc1s + linear; normals -> uastc-normal (linear, normal-map
// mode, zstd supercompression + RDO).
import { encodeToKTX2 } from 'ktx2-encoder';
import { PNG } from 'pngjs';
import fs from 'fs';

const args = process.argv.slice(2);
const [inp, out, mode = 'etc1s'] = args;
if (!inp || !out) { console.error('usage: encode.mjs in.png out.ktx2 etc1s|uastc|uastc-normal [--srgb|--linear] [--quality N]'); process.exit(2); }
const linear = args.includes('--linear') || mode === 'uastc-normal';
const qi = args.indexOf('--quality');
const quality = qi >= 0 ? Number(args[qi + 1]) : 128;
const dec = async (buf) => { const p = PNG.sync.read(Buffer.from(buf)); return { width: p.width, height: p.height, data: p.data }; };
const t0 = Date.now();
const r = await encodeToKTX2(fs.readFileSync(inp), {
  isUASTC: mode !== 'etc1s',
  isNormalMap: mode === 'uastc-normal',
  isSetKTX2SRGBTransferFunc: !linear,
  isPerceptual: !linear,
  generateMipmap: true,
  needSupercompression: mode !== 'etc1s',   // zstd for UASTC
  enableRDO: mode !== 'etc1s',
  rdoQualityLevel: 1.0,
  qualityLevel: quality,
  imageDecoder: dec,
});
fs.writeFileSync(out, r);
console.log(JSON.stringify({ in: inp, out, mode, srgb: !linear, bytes: r.length, ms: Date.now() - t0 }));
