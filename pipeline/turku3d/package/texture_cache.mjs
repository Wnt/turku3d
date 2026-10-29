// Retain the encoded inputs of two successful packages, independent of geometry edits.
import fs from 'node:fs';
import path from 'node:path';

export function useTextureCache(request, file) {
  (request.usedTextureCache ??= new Set()).add(path.relative(request.textureCache, file));
  return file;
}

export function retainTextureCache(request) {
  const pointer = path.join(request.textureCache, '.retained.json');
  const previous = fs.existsSync(pointer) ? JSON.parse(fs.readFileSync(pointer, 'utf8')) : [];
  const current = [...(request.usedTextureCache ?? [])].sort();
  const generations = [current, ...previous.filter(row => JSON.stringify(row) !== JSON.stringify(current))].slice(0, 2);
  const keep = new Set(generations.flat());
  for (const name of fs.readdirSync(request.textureCache, { recursive:true })) {
    // Include mesh buffers; interrupted codec temporaries are disposable.
    if (name.startsWith('.') || fs.statSync(path.join(request.textureCache, name)).isDirectory() || keep.has(name)) continue;
    fs.unlinkSync(path.join(request.textureCache, name));
  }
  const temp = `${pointer}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(generations));
  fs.renameSync(temp, pointer);
}
