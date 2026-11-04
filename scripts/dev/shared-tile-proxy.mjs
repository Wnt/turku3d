#!/usr/bin/env node
// Hot-read config: {service, version, scenery, follow?, exclude?}. Vite keeps its existing /coarse proxy.
//
// `follow` names an alias family on the farm (`"train"`: train1, train2, ...). Each tile's manifest or collision
// fragment comes from the newest member that has finished that tile, else from `version`, the pin, exactly as
// without `follow`. The chain is the farm's aliases `<follow><N>`, highest N first, re-read every ALIASES_MS, so a
// new train joins without a deploy; `exclude` drops named aliases (Jonni's call).
// * Finished: the version's ready.json for the tile, which the farm writes last and atomically, after the product is
//   in place and validated. A half-written tile has none.
// * Compatible: a member serves a tile only when its ready.json names the same base generation as the pin's (the
//   ground, roads and buildings the migrations edit). A train on a structurally different generation is never mixed
//   in; switching to it is a repin.
// * A member is only read, never asked to build or migrate; status, demand, want and navigation go to the pin.
// * Each fragment names its payloads by revision (/coarse/_v/<sha>/), so tiles of different versions never share a
//   cache entry. Every player resolves through the same farm files, so a tile switches for all of them together.
//
// A finished fragment is the same for every player and every distance, so it is kept FRAGMENT_MS per tile and served
// with an ETag (304 for an unchanged poll); one whose tile is being rebuilt is kept UPGRADING_MS. The farm answers fragments one at a time (one Python process): without
// this, every player's resident-tile polls queue in front of the tiles someone is driving towards. A player's own tile
// (d < 1) still reaches the farm, which tracks where players stand. Concurrent identical misses share one request.
import crypto from 'node:crypto';
import http from 'node:http';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const FRAGMENT = /^\/coarse\/(t0_-?\d{1,5}_-?\d{1,5})\/(?:manifest|collision)\.json$/;
const ALIASES_MS = 30_000;
const FRAGMENT_MS = 15_000;
const UPGRADING_MS = 3_000;  // a served fragment whose tile is being rebuilt (stale, queued) is polled for its upgrade
const FRAGMENTS_MAX = 4000;

function getJson(url) {
  return new Promise(resolve => {
    const request = http.get(url, { timeout: 5000 }, reply => {
      const chunks = [];
      reply.on('data', chunk => chunks.push(chunk));
      reply.on('end', () => {
        if (reply.statusCode !== 200) { resolve(null); return; }
        try { resolve(JSON.parse(Buffer.concat(chunks).toString())); } catch { resolve(null); }
      });
      reply.on('error', () => resolve(null));
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(null));
  });
}

/** The tile's completion receipt for a version (nothing is queued), or null while it is not finished. */
const receipt = (service, version, tile) => getJson(new URL(`/v/${encodeURIComponent(version)}/coarse/${tile}/ready.json`, service));
const baseOf = ready => ready?.base_generation ?? ready?.generation;

/** The farm's `<family><N>` aliases, newest (highest N) first. */
export function chainOf(aliases, family, exclude = []) {
  const member = new RegExp(`^${family.replace(/[^a-z0-9-]/g, '')}(\\d+)$`);
  return Object.keys(aliases ?? {}).map(name => [name, member.exec(name)?.[1]]).filter(([name, n]) => n && !exclude.includes(name))
    .sort((a, b) => Number(b[1]) - Number(a[1])).map(([name]) => name);
}

function resolver() {
  const aliases = { service: null, at: 0, names: {}, refreshing: null };
  const pinBase = new Map();  // pin -> its base generation, learnt from its receipts
  const known = async service => {
    if (aliases.service !== service || Date.now() - aliases.at > ALIASES_MS) {
      aliases.refreshing ??= getJson(new URL('/status', service)).then(status => {
        if (status?.aliases) Object.assign(aliases, { service, at: Date.now(), names: status.aliases });
      }).finally(() => { aliases.refreshing = null; });
      if (aliases.service !== service) await aliases.refreshing;  // first answer; later refreshes run behind requests
    }
    return aliases.service === service ? aliases.names : {};
  };
  /** The version serving this request: per tile the newest compatible chain member that finished it, else the pin. */
  return async (config, pathname) => {
    const tile = FRAGMENT.exec(pathname)?.[1];
    if (!tile || typeof config.follow !== 'string' || !config.follow) return config.version;
    const chain = chainOf(await known(config.service), config.follow, Array.isArray(config.exclude) ? config.exclude : [])
      .filter(name => name !== config.version);
    if (!chain.length) return config.version;
    const versions = [config.version, ...chain];
    // One farm request for the whole chain; a farm without /receipts is asked per version.
    const batch = await getJson(new URL(`/receipts?tile=${tile}&v=${versions.map(encodeURIComponent).join(',')}`, config.service));
    const [pin, ...members] = batch ? versions.map(v => batch[v] ?? null)
      : await Promise.all(versions.map(v => receipt(config.service, v, tile)));
    if (baseOf(pin)) pinBase.set(config.version, baseOf(pin));
    const base = pinBase.get(config.version);
    if (!base) return config.version;  // nothing known to be compatible yet
    const index = members.findIndex(ready => ready && baseOf(ready) === base);
    return index < 0 ? config.version : chain[index];
  };
}

/** Fragments by config and path (FRAGMENT_MS when finished, UPGRADING_MS while upgrading), plus misses in flight. */
function fragmentCache() {
  const done = new Map(), pending = new Map();
  const sweep = now => { for (const [key, hit] of done) if (hit.until <= now || done.size > FRAGMENTS_MAX) done.delete(key); else break; };
  return {
    get(key) {
      const hit = done.get(key);
      return hit && hit.until > Date.now() ? hit : pending.get(key);
    },
    /** `load()` resolves to {status, headers, body, finished}; errors and 202s are never kept. */
    join(key, load) {
      const promise = load().then(answer => {
        const keep = answer.finished ? FRAGMENT_MS : answer.status === 200 ? UPGRADING_MS : 0;
        if (keep) { sweep(Date.now()); done.delete(key); done.set(key, { ...answer, until: Date.now() + keep }); }
        return answer;
      }).finally(() => pending.delete(key));
      pending.set(key, promise);
      return promise;
    },
  };
}

/** The farm's answer is final for this revision: built and current (a queued, stale or migrating one is not kept). */
function finished(body) {
  try {
    const value = JSON.parse(body);
    return value?.coarse === true && value.stale === false && (value.queue_state ?? 'built') === 'built';
  } catch { return false; }
}

const etagOf = body => `W/"${crypto.createHash('sha1').update(body).digest('base64url')}"`;

function sendFragment(req, res, { status, headers, body }) {
  const etag = etagOf(body);
  if (status === 200 && req.headers['if-none-match'] === etag) { res.writeHead(304, { etag, 'cache-control': headers['cache-control'] ?? 'no-cache' }); res.end(); return; }
  res.writeHead(status, { ...headers, 'content-length': body.length, ...(status === 200 ? { etag } : {}) });
  res.end(req.method === 'HEAD' ? undefined : body);
}

export function createSharedTileProxy(configFile) {
  const versionFor = resolver();
  const fragments = fragmentCache();
  return http.createServer(async (req, res) => {
    let config;
    try { config = JSON.parse(fs.readFileSync(configFile, 'utf8')); }
    catch (error) { res.writeHead(503); res.end(JSON.stringify({ error: error.message })); return; }
    const url = new URL(req.url, 'http://local');
    const scenery = /^\/coarse\/scenery(?:\/|$)/.test(url.pathname);
    const pinned = /^\/coarse\/_v\/([a-f0-9]{40,64})(\/.*)$/.exec(url.pathname);
    const distance = Number(url.searchParams.get('d') ?? Infinity);
    const cached = !scenery && !pinned && req.method === 'GET' && FRAGMENT.test(url.pathname) && !(distance < 1);
    const key = cached && JSON.stringify([config.service, config.version, config.follow, config.exclude, url.pathname]);
    const hit = key && fragments.get(key);
    if (hit) { sendFragment(req, res, await hit); return; }
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
            const text = Buffer.concat(chunks).toString();
            const body = Buffer.from(text.replaceAll(/\/v\/([a-f0-9]{40,64})\/coarse\//g, '/coarse/_v/$1/'));
            const { date, connection, 'keep-alive': _, 'content-length': __, ...headers } = reply.headers;
            const answer = { status: reply.statusCode, headers, body, finished: finished(text) };
            if (done) done(answer); else sendFragment(req, res, answer);
          });
        } else if (done) {
          // Not a fragment the cache keeps (an error, a 202 while queued): answer it as is.
          const chunks = [];
          reply.on('data', chunk => chunks.push(chunk));
          reply.on('end', () => {
            const { date, connection, 'keep-alive': _, 'content-length': __, ...headers } = reply.headers;
            done({ status: reply.statusCode, headers, body: Buffer.concat(chunks), finished: false });
          });
        } else { res.writeHead(reply.statusCode, reply.headers); reply.pipe(res); }
      });
      upstream.on('error', error => {
        if (retry()) {
          setTimeout(() => forward(attempt + 1), 100 * (attempt + 1));
          return;
        }
        const body = Buffer.from(JSON.stringify({ error: error.message }));
        if (done) { done({ status: 502, headers: { 'content-type': 'application/json' }, body, finished: false }); return; }
        if (!res.headersSent) res.writeHead(502, { 'content-type': 'application/json' });
        res.end(body);
      });
      if (req.method === 'GET') upstream.end();
      else req.pipe(upstream);
    };
    // Fragments are collected whole (their payload URLs are rewritten) and shared by concurrent identical requests.
    let done = null;
    if (key) {
      sendFragment(req, res, await fragments.join(key, () => new Promise(resolve => { done = resolve; forward(); })));
      return;
    }
    forward();
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const [port, config] = process.argv.slice(2);
  if (!port || !config) throw new Error('Usage: shared-tile-proxy.mjs PORT CONFIG.json');
  createSharedTileProxy(config).listen(Number(port), '127.0.0.1', () => console.log(`shared tiles proxy :${port}`));
}
