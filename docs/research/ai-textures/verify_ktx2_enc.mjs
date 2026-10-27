import { encodeToKTX2 } from 'ktx2-encoder';
import { PNG } from 'pngjs';
import fs from 'fs';
const [,, inp, out, mode] = process.argv;
const dec = async (buf) => { const p = PNG.sync.read(Buffer.from(buf)); return { width: p.width, height: p.height, data: p.data }; };
const t0 = Date.now();
const r = await encodeToKTX2(fs.readFileSync(inp), { isUASTC: mode === 'uastc', generateMipmap: true, qualityLevel: 128, isNormalMap: mode === 'uastc', imageDecoder: dec });
fs.writeFileSync(out, r); console.log(out, r.length, 'bytes', Date.now() - t0, 'ms');
