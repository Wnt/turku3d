#!/usr/bin/env node
// Hot-read config: {service, version, scenery}. Vite keeps its existing /coarse proxy.
import http from 'node:http';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

export function createSharedTileProxy(configFile) {
  return http.createServer((req, res) => {
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
      const version = pinned ? pinned[1] : config.version;
      target = new URL(`/v/${encodeURIComponent(version)}/coarse${suffix}${url.search}`, config.service);
    }
    const upstream = http.request(target, { method: req.method, headers: { ...req.headers, host: target.host } }, reply => {
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
      if (!res.headersSent) res.writeHead(502, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ error: error.message }));
    });
    req.pipe(upstream);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const [port, config] = process.argv.slice(2);
  if (!port || !config) throw new Error('Usage: shared-tile-proxy.mjs PORT CONFIG.json');
  createSharedTileProxy(config).listen(Number(port), '127.0.0.1', () => console.log(`shared tiles proxy :${port}`));
}
