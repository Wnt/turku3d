// The one dynamic tile-service answer a published city.madekivi.fi needs: the distant-scenery catalogue
// (GET /coarse/scenery?tiles=a,b,c, game/src/world/screenshotScenery.ts; another city's at
// /cities/<name>/coarse/scenery), filtered from the catalogue export-oci.mjs wrote under that mount
// (<www>/<mount>/scenery-catalogue.json, re-read when it changes). Tiles the export has no row for are `missing`,
// exactly as the scenery server answers squares without a source; a mount without a catalogue answers 404, as a city
// service without scenery does. Everything else the game reads is a file (Caddy).
//   node citylab-api.mjs --port 7783 --www /srv/turku3d/citylab/www
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const PORT = Number(arg('port', 7783));
const WWW = arg('www', '/srv/turku3d/citylab/www');
const TILE = /^t[024]_-?\d+_-?\d+$/;
const ROUTE = /^\/((?:cities\/[a-z]{2,24}\/)?coarse)\/scenery$/;

const catalogues = new Map();  // mount -> { rows, recipe, generation, mtime }
function current(mount) {
  const file = path.join(WWW, mount, 'scenery-catalogue.json');
  let known = catalogues.get(mount);
  try {
    const mtime = fs.statSync(file).mtimeMs;
    if (!known || mtime !== known.mtime) {
      const data = JSON.parse(fs.readFileSync(file, 'utf8'));
      known = { rows: new Map(Object.entries(data.tiles)), recipe: data.recipe, generation: data.generation, mtime };
      catalogues.set(mount, known);
      console.log(`${mount}: ${known.rows.size} rows (${new Date(mtime).toISOString()})`);
    }
  } catch (e) { if (e.code !== 'ENOENT') console.error(`${mount}: ${e.message}`); else return null; }
  return known ?? null;
}

const send = (res, status, body) => {
  const data = Buffer.from(JSON.stringify(body));
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': data.length, 'cache-control': 'no-cache' });
  res.end(data);
};

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://local'), route = ROUTE.exec(url.pathname);
  if (req.method !== 'GET' || !route) return send(res, 404, { error: 'not found' });
  const tiles = (url.searchParams.get('tiles') ?? '').split(',');
  if (tiles.length > 256 || !tiles.every(t => TILE.test(t))) return send(res, 400, { error: 'tiles' });
  const catalogue = current(route[1]);
  if (!catalogue) return send(res, 404, { error: 'not found' });
  const found = [], missing = [];
  for (const t of new Set(tiles)) { const row = catalogue.rows.get(t); if (row) found.push(row); else missing.push(t); }
  send(res, 200, { tiles: found, missing, recipe: catalogue.recipe, generation: catalogue.generation });
}).listen(PORT, '127.0.0.1', () => { console.log(`citylab-api on 127.0.0.1:${PORT}, catalogues under ${WWW}`); current('coarse'); });
