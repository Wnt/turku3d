// Split oversized compressed payloads at buffer-view boundaries. No geometry or texture changes.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

export function splitVisualBuffers(filename, directory, limit = 4_000_000) {
  const bytes = fs.readFileSync(filename), jsonLength = bytes.readUInt32LE(12);
  const json = JSON.parse(bytes.subarray(20, 20 + jsonLength));
  const binary = bytes.subarray(28 + jsonLength);
  const references = [];
  for (const view of json.bufferViews ?? []) {
    if (view.buffer === 0 && view.byteLength) references.push(view);
    const compressed = view.extensions?.EXT_meshopt_compression;
    if (compressed?.buffer === 0 && compressed.byteLength) references.push(compressed);
  }
  const regions = [];
  for (const ref of [...references].sort((a,b) => (a.byteOffset ?? 0) - (b.byteOffset ?? 0))) {
    const start = ref.byteOffset ?? 0, end = start + ref.byteLength;
    const previous = regions.at(-1);
    if (previous && start < previous.end) previous.end = Math.max(previous.end,end);
    else regions.push({start,end});
  }
  const chunks = [];
  for (const region of regions) {
    if (region.end-region.start > limit) throw new Error('One encoded buffer view exceeds the split payload budget');
    let chunk = chunks.at(-1);
    const size = Math.ceil((region.end-region.start)/4)*4;
    if (!chunk || chunk.size + size > limit) { chunk={size:0,regions:[]};chunks.push(chunk); }
    region.chunk=chunks.length-1;region.offset=chunk.size;
    chunk.regions.push(region);chunk.size+=size;
  }
  const assets=[];
  fs.mkdirSync(directory,{recursive:true});
  for (const [i,chunk] of chunks.entries()) {
    const data=Buffer.alloc(chunk.size);
    for (const r of chunk.regions) binary.copy(data,r.offset,r.start,r.end);
    const hash=createHash('sha256').update(data).digest('hex'), name=hash+'.bin';
    const dest=path.join(directory,name);
    if (!fs.existsSync(dest)) { const temp=dest+`.${process.pid}.tmp`;fs.writeFileSync(temp,data);fs.renameSync(temp,dest); }
    const descriptor={byteLength:data.length,uri:`../shared/${name}`};
    chunk.buffer=i===0 ? 0 : json.buffers.length;
    if (i===0) json.buffers[0]=descriptor; else json.buffers.push(descriptor);
    assets.push(name);
  }
  for (const ref of references) {
    const start=ref.byteOffset ?? 0;
    const region=regions.find(r=>r.start<=start && r.end>=start+ref.byteLength);
    ref.buffer=chunks[region.chunk].buffer;ref.byteOffset=region.offset+start-region.start;
  }
  const encoded=Buffer.from(JSON.stringify(json)), padded=Buffer.alloc(Math.ceil(encoded.length/4)*4,0x20);
  encoded.copy(padded);
  const header=Buffer.from(bytes.subarray(0,20));
  header.writeUInt32LE(20+padded.length,8);header.writeUInt32LE(padded.length,12);
  fs.writeFileSync(filename,Buffer.concat([header,padded]));
  return assets;
}
