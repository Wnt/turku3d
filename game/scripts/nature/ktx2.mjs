// KTX2 (Basis) encoding of the nature textures, cached under the sources' build directory by content hash.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { encodeToKTX2 } from 'ktx2-encoder';
import { toRGBA8 } from './image.mjs';

/** ETC1S for colour (`srgb`), UASTC + zstd for `normal` (any linear data). */
export async function ktx2(img, { srgb, normal = false, quality = 200 }, cacheDir, log = () => {}) {
  const raw = toRGBA8(img);
  const key = createHash('sha256').update(raw).update(JSON.stringify({ srgb, normal, quality, w: img.w, h: img.h, v: 2 })).digest('hex');
  const file = path.join(cacheDir, `${key}.ktx2`);
  if (fs.existsSync(file)) return fs.readFileSync(file);
  const started = performance.now();
  const data = await encodeToKTX2(new Uint8Array(1), {
    isUASTC: normal, generateMipmap: true, isPerceptual: srgb, isSetKTX2SRGBTransferFunc: srgb, isNormalMap: normal,
    needSupercompression: normal, uastcLDRQualityLevel: 2, enableRDO: normal, rdoQualityLevel: 2, qualityLevel: quality, compressionLevel: 2,
    imageDecoder: async () => ({ width: img.w, height: img.h, data: new Uint8Array(raw.buffer, raw.byteOffset, raw.length) }),
  });
  fs.mkdirSync(cacheDir, { recursive: true });
  fs.writeFileSync(file, data);
  log(`encoded ${img.w}x${img.h} ${normal ? 'UASTC' : 'ETC1S'} ${(data.length / 1024).toFixed(0)} KiB in ${((performance.now() - started) / 1000).toFixed(1)} s`);
  return Buffer.from(data);
}
