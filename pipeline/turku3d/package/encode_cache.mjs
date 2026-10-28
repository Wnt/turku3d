// Lossless mesh buffers are independent compilation units, shared by full and draft tiles.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

export function cachedEncoder(encoder, identity, directory) {
  const stats = { hits: 0, misses: 0 };
  return {
    stats,
    encoder: {
      ...encoder,
      encodeGltfBuffer(source, count, size, mode, version) {
        const root = directory();
        if (!root) return encoder.encodeGltfBuffer(source, count, size, mode, version);
        const key = createHash('sha256').update(identity)
          .update(JSON.stringify([count, size, mode, version ?? null]))
          .update(source).digest('hex');
        fs.mkdirSync(root, { recursive: true });
        const file = path.join(root, key);
        if (fs.existsSync(file)) {
          const data = fs.readFileSync(file);
          if (data.length >= 32 && createHash('sha256').update(data.subarray(32)).digest().equals(data.subarray(0, 32))) {
            stats.hits++;
            return data.subarray(32);
          }
        }
        stats.misses++;
        const data = encoder.encodeGltfBuffer(source, count, size, mode, version);
        const temp = `${file}.${process.pid}.tmp`;
        fs.writeFileSync(temp, Buffer.concat([createHash('sha256').update(data).digest(), data]));
        fs.renameSync(temp, file);
        return data;
      },
    },
  };
}
