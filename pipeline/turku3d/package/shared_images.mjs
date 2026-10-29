import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

const align4 = n => (n + 3) & ~3;

// GLB permits external images. Keep meshopt buffer views intact while removing
// library image payloads, which would otherwise be repeated in every tile.
export function externalizeLibraryImages(filename, sharedHashes, sharedDir) {
  const glb = fs.readFileSync(filename);
  const jsonSize = glb.readUInt32LE(12);
  const json = JSON.parse(glb.subarray(20, 20 + jsonSize).toString());
  const binHead = 20 + jsonSize;
  const bin = glb.subarray(binHead + 8);
  const removed = new Set();
  const assets = [];
  for (const image of json.images ?? []) {
    if (image.bufferView === undefined) continue;
    const view = json.bufferViews[image.bufferView];
    const data = bin.subarray(view.byteOffset ?? 0, (view.byteOffset ?? 0) + view.byteLength);
    const hash = createHash('sha256').update(data).digest('hex');
    if (!sharedHashes.has(hash)) continue;
    const name = `${hash}.ktx2`;
    fs.mkdirSync(sharedDir, { recursive: true });
    const destination = path.join(sharedDir, name);
    if (!fs.existsSync(destination)) {
      const temp = destination + `.${process.pid}.tmp`;
      fs.writeFileSync(temp, data);
      fs.renameSync(temp, destination);
    }
    image.uri = `../shared/${name}`;
    delete image.bufferView;
    removed.add(json.bufferViews.indexOf(view));
    assets.push(name);
  }
  if (!removed.size) return [];
  const ranges = [...removed].map(i => {
    const view = json.bufferViews[i];
    if (view.buffer !== 0) throw new Error('Library image is not in the GLB buffer');
    return [view.byteOffset ?? 0, align4((view.byteOffset ?? 0) + view.byteLength)];
  }).sort((a, b) => a[0] - b[0]);
  const shifted = offset => offset - ranges.reduce((n, [start, end]) => n + (end <= offset ? end - start : 0), 0);
  const chunks = [];
  let cursor = 0;
  for (const [start, end] of ranges) {
    if (start < cursor) throw new Error('Overlapping library images');
    chunks.push(bin.subarray(cursor, start));
    cursor = end;
  }
  chunks.push(bin.subarray(cursor));
  const newBin = Buffer.concat(chunks);
  for (let i = 0; i < json.bufferViews.length; i++) {
    const view = json.bufferViews[i];
    if (removed.has(i)) { view.byteOffset = 0; view.byteLength = 0; }
    else if (view.buffer === 0) view.byteOffset = shifted(view.byteOffset ?? 0);
    const meshopt = view.extensions?.EXT_meshopt_compression;
    if (meshopt?.buffer === 0) meshopt.byteOffset = shifted(meshopt.byteOffset ?? 0);
  }
  json.buffers[0].byteLength = newBin.length;
  const jsonData = Buffer.from(JSON.stringify(json));
  const jsonPadding = Buffer.alloc(align4(jsonData.length) - jsonData.length, 0x20);
  const header = Buffer.alloc(20);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(20 + jsonData.length + jsonPadding.length + 8 + newBin.length, 8);
  header.writeUInt32LE(jsonData.length + jsonPadding.length, 12);
  header.writeUInt32LE(0x4e4f534a, 16);
  const binHeader = Buffer.alloc(8);
  binHeader.writeUInt32LE(newBin.length, 0);
  binHeader.writeUInt32LE(0x004e4942, 4);
  fs.writeFileSync(filename, Buffer.concat([header, jsonData, jsonPadding, binHeader, newBin]));
  return assets;
}
