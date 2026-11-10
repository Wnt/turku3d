// The one dynamic tile-service answer a published city.madekivi.fi needs: the distant-scenery catalogue
// (GET /coarse/scenery?tiles=a,b,c, game/src/world/screenshotScenery.ts), filtered from the catalogue export-oci.mjs
// wrote (<www>/coarse/scenery-catalogue.json, re-read when it changes). Tiles the export has no row for are `missing`,
// exactly as the scenery server answers squares without a source. Everything else the game reads is a file (Caddy).
//   node citylab-api.mjs --port 7783 --www /srv/turku3d/citylab/www
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const PORT = Number(arg('port', 7783));
const FILE = path.join(arg('www', '/srv/turku3d/citylab/www'), 'coarse', 'scenery-catalogue.json');
const TILE = /^t[024]_-?\d+_-?\d+$/;

let catalogue = { rows: new Map(), recipe: undefined, generation: undefined }, loadedMtime = 0;
function current() {
  try {
    const mtime = fs.statSync(FILE).mtimeMs;
    if (mtime !== loadedMtime) {
      const data = JSON.parse(fs.readFileSync(FILE, 'utf8'));
      catalogue = { rows: new Map(Object.entries(data.tiles)), recipe: data.recipe, generation: data.generation };
      loadedMtime = mtime;
      console.log(`catalogue: ${catalogue.rows.size} rows (${new Date(mtime).toISOString()})`);
    }
  } catch (e) { if (e.code !== 'ENOENT') console.error(`catalogue: ${e.message}`); }
  return catalogue;
}

const send = (res, status, body) => {
  const data = Buffer.from(JSON.stringify(body));
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': data.length, 'cache-control': 'no-cache' });
  res.end(data);
};

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://local');
  if (req.method !== 'GET' || url.pathname !== '/coarse/scenery') return send(res, 404, { state: 'missing' });
  const tiles = (url.searchParams.get('tiles') ?? '').split(',');
  if (tiles.length > 256 || !tiles.every(t => TILE.test(t))) return send(res, 400, { error: 'tiles' });
  const { rows, recipe, generation } = current(), found = [], missing = [];
  for (const t of new Set(tiles)) { const row = rows.get(t); if (row) found.push(row); else missing.push(t); }
  send(res, 200, { tiles: found, missing, recipe, generation });
}).listen(PORT, '127.0.0.1', () => { console.log(`citylab-api on 127.0.0.1:${PORT}, catalogue ${FILE}`); current(); });
