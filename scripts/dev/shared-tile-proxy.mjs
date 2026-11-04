#!/usr/bin/env node
// Hot-read config: {service, version, scenery, prefer?}. Vite keeps its existing /coarse proxy.
//
// `prefer` lists newer versions (migrated aliases such as a city pass's `train1`) tried per tile before `version`:
// a tile's manifest or collision fragment comes from the first of them that has finished that tile (its ready.json,
// which the farm writes last and atomically), otherwise from `version`, the pin, exactly as without `prefer`. A
// preferred version is only read, never asked to build or migrate; status, demand and every other route go to the
// pin. Each fragment names its payloads by revision (/coarse/_v/<sha>/), so neighbouring tiles of different versions
// never share a cache entry, and a tile switches by itself once its newer version is finished.
import http from 'node:http';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const FRAGMENT = /^\/coarse\/(t0_-?\d{1,5}_-?\d{1,5})\/(?:manifest|collision)\.json$/;

/** True when `version` has finished `tile` (a read of its ready.json; nothing is queued). */
function finished(service, version, tile) {
  const url = new URL(`/v/${encodeURIComponent(version)}/coarse/${tile}/ready.json`, service);
  return new Promise(resolve => {
    const probe = http.request(url, { method: 'HEAD', timeout: 5000 }, reply => { reply.resume(); resolve(reply.statusCode === 200); });
    probe.on('timeout', () => probe.destroy());
    probe.on('error', () => resolve(false));
    probe.end();
  });
}

/** The version serving this request: per tile the first preferred version that finished it, else the pin. */
async function versionFor(config, pathname) {
  const tile = FRAGMENT.exec(pathname)?.[1];
  const prefer = Array.isArray(config.prefer) ? config.prefer.filter(v => typeof v === 'string' && v !== config.version) : [];
  if (tile) for (const version of prefer) if (await finished(config.service, version, tile)) return version;
  return config.version;
}

export function createSharedTileProxy(configFile) {
  return http.createServer(async (req, res) => {
    let config;
    try { config = JSON.parse(fs.readFileSync(configFile, 'utf8')); }
    catch (error) { res.writeHead(503); res.end(JSON.stringify({ error: error.message })); return; }
    const url = new URL(req.url, 'http://local');
    const scenery = /^\/coarse\/scenery(?:\/|$)/.test(url.pathname);
    const pinned = /^\/coarse\/_v\/([a-f0-9]{40,64})(\/.*)$/.exec(url.pathname);
    let target;
    if (scenery) {
      if (!config.scenery) { res.writeHead(404); res.end(); return; }
      target = new URL(req.url, config.scenery);
    } else {
      const suffix = pinned ? pinned[2] : url.pathname.replace(/^\/coarse/, '');
      const version = pinned ? pinned[1] : await versionFor(config, url.pathname);
      target = new URL(`/v/${encodeURIComponent(version)}/coarse${suffix}${url.search}`, config.service);
    }
    const forward = (attempt = 0) => {
      const retry = () => req.method === 'GET' && attempt < 3 && !res.headersSent && !res.destroyed;
      const upstream = http.request(target, { method: req.method, headers: { ...req.headers, host: target.host } }, reply => {
        if ([502, 503, 504].includes(reply.statusCode) && retry()) {
          reply.resume();
          setTimeout(() => forward(attempt + 1), 100 * (attempt + 1));
          return;
        }
        // Freeze all payload references to the manifest's revision, even if its alias advances.
        const manifest = !scenery && /\/(?:manifest|collision)\.json$/.test(url.pathname) && reply.statusCode === 200;
        if (manifest) {
          const chunks = [];
          reply.on('data', chunk => chunks.push(chunk));
          reply.on('end', () => {
            const body = Buffer.from(Buffer.concat(chunks).toString().replaceAll(/\/v\/([a-f0-9]{40,64})\/coarse\//g, '/coarse/_v/$1/'));
            res.writeHead(reply.statusCode, { ...reply.headers, 'content-length': body.length });
            res.end(body);
          });
        } else { res.writeHead(reply.statusCode, reply.headers); reply.pipe(res); }
      });
      upstream.on('error', error => {
        if (retry()) {
          setTimeout(() => forward(attempt + 1), 100 * (attempt + 1));
          return;
        }
        if (!res.headersSent) res.writeHead(502, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ error: error.message }));
      });
      if (req.method === 'GET') upstream.end();
      else req.pipe(upstream);
    };
    forward();
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const [port, config] = process.argv.slice(2);
  if (!port || !config) throw new Error('Usage: shared-tile-proxy.mjs PORT CONFIG.json');
  createSharedTileProxy(config).listen(Number(port), '127.0.0.1', () => console.log(`shared tiles proxy :${port}`));
}
