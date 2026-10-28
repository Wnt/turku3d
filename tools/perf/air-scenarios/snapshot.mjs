// Freeze already served draft payloads and referenced glTF images for a matched Air comparison.
import fs from 'node:fs/promises';
import path from 'node:path';
const [endpoint, output, ...tiles] = process.argv.slice(2);
if (!tiles.length) throw new Error('Usage: snapshot.mjs ENDPOINT OUTPUT TILE...');
const root = new URL(endpoint.endsWith('/') ? endpoint : endpoint + '/');
const saved = new Map();
async function save(url) {
  if (saved.has(url.href)) return saved.get(url.href);
  const task = (async () => {
    if (!url.href.startsWith(root.href)) throw new Error(`Outside snapshot endpoint: ${url}`);
    const response = await fetch(url);
    if (response.status !== 200) throw new Error(`${url}: ${response.status}`);
    const data = Buffer.from(await response.arrayBuffer());
    const file = path.resolve(output, decodeURIComponent(url.pathname.slice(root.pathname.length)));
    if (!file.startsWith(path.resolve(output) + path.sep)) throw new Error('Unsafe snapshot path');
    await fs.mkdir(path.dirname(file), { recursive: true }); await fs.writeFile(file, data);
    return data;
  })();
  saved.set(url.href, task); return task;
}
for (const tile of tiles) {
  const base = new URL(`${tile}/`, root);
  const manifest = JSON.parse(await save(new URL('manifest.json', base)));
  for (const entry of [...Object.values(manifest.visual), ...Object.values(manifest.collision), ...(manifest.roads ? [manifest.roads] : [])]) {
    const url = new URL(entry.url, base), data = await save(url);
    if (url.pathname.endsWith('.glb')) {
      const gltf = JSON.parse(data.subarray(20, 20 + data.readUInt32LE(12)).toString());
      for (const image of gltf.images ?? []) if (image.uri && !image.uri.startsWith('data:')) await save(new URL(image.uri, url));
    }
  }
  console.log(tile);
}
console.log(`Frozen ${saved.size} files in ${output}`);
