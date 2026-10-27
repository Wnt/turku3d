// Vite dev-server plugin for the localhost game (README.md here): the routes serve-dist.mjs gives a production build,
// plus the localhost service's state.
//  GET  /tiles/*            the tile release the service selected (state.tiles.dir), else `tiles` (the sandbox dist)
//  GET  /tile-store/<key>/* one immutable tile store release (state.store), for in-place tile swaps
//  POST /telemetry          play telemetry (http.mjs)
//  GET  /__localhost/status the badge status: { service, commit, verification, tiles }; { service: false } without it
//  POST /__localhost/event  the game reports a loaded page, a hot swap or a tile release swap; appended to state.events
// A changed state file is pushed to open tabs as the custom HMR event `turku3d:status`.
// `vite preview` (the browser checks' server) gets POST /telemetry only, into the same directory.
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import { resolveUnder, serveFile, telemetryHandler } from '../http.mjs';

/** The part of the service state the game sees. */
export function publicStatus(state) {
  if (!state?.commit) return { service: false };
  return { service: true, commit: state.commit, verification: state.verification ?? { state: 'unverified' },
    tiles: state.tiles ? { key: state.tiles.key ?? null, source: state.tiles.source, building: state.tiles.building ?? null } : undefined };
}

function readState(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

const json = (res, status, body) => {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'Cross-Origin-Resource-Policy': 'cross-origin' });
  res.end(JSON.stringify(body));
};

/** `statePath`: the service's state.json (TURKU3D_LOCALHOST_STATE), or undefined for a plain `make game-dev`. */
export function localhostGame({ statePath, tiles, telemetryDir }) {
  const telemetry = telemetryHandler(telemetryDir);
  /** POST /telemetry on the dev and the preview server; whether it was this request. */
  const postTelemetry = (req, res, next) => {
    if (req.method !== 'POST' || (req.url ?? '/').split('?')[0] !== '/telemetry') return false;
    telemetry(req, res).catch(next);
    return true;
  };
  return {
    name: 'turku3d-localhost',
    apply: 'serve',
    configurePreviewServer(server) {
      server.middlewares.use((req, res, next) => { if (!postTelemetry(req, res, next)) next(); });
    },
    configureServer(server) {
      let state = statePath ? readState(statePath) : null;
      if (statePath) {
        fs.watchFile(statePath, { interval: 200 }, () => {
          state = readState(statePath);
          server.ws.send({ type: 'custom', event: 'turku3d:status', data: publicStatus(state) });
        });
        server.httpServer?.on('close', () => fs.unwatchFile(statePath));
      }
      server.middlewares.use((req, res, next) => {
        if (postTelemetry(req, res, next)) return;
        const url = req.url ?? '/';
        const p = url.split('?')[0];
        if (p === '/__localhost/status') return json(res, 200, publicStatus(state));
        if (req.method === 'POST' && p === '/__localhost/event') return void event(req, res, state).catch(next);
        if (req.method !== 'GET' && req.method !== 'HEAD') return next();
        let file = null;
        if (p.startsWith('/tiles/')) file = resolveUnder(state?.tiles?.dir ?? tiles, url.slice('/tiles'.length));
        else if (p.startsWith('/tile-store/') && state?.store) file = resolveUnder(state.store, url.slice('/tile-store'.length));
        else return next();
        if (!file) return json(res, 403, { error: 'forbidden' });
        // The release behind /tiles changes under an open tab: every request revalidates (ETag), so a reload never
        // mixes cached bytes of the previous release with the new manifest.
        serveFile(req, res, file, { cacheControl: 'no-cache' }).catch(next);
      });
    },
  };
}

async function event(req, res, state) {
  let body = '';
  for await (const chunk of req) body += chunk;
  let row;
  try { row = JSON.parse(body); } catch { return json(res, 400, { error: 'JSON body required' }); }
  if (!state?.events || !['loaded', 'hot-swap', 'tiles'].includes(row?.type)) return json(res, 204, {});
  await fsp.appendFile(state.events, JSON.stringify({ time: Date.now() / 1000, type: row.type, commit: state.commit }) + '\n');
  json(res, 204, {});
}
