// Inert asset replay, never a scripted walk/drive or generator build. No request routing:
// Playwright routing disables HTTP caching and would invalidate this comparison.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, workRoot, port } from '../../tools/dev/environment.mjs';

isolateBrowser();
const baseline = process.argv.includes('--baseline');
const duration = baseline ? 0 : Number(process.env.REVISIT_SECONDS ?? 300);
assert(duration >= 0 && duration <= 300, 'Replay must fit the ten-minute post-deploy budget');
const output = path.join(reportsRoot, 'revisit-cache'); fs.mkdirSync(output, { recursive: true });
const publishedWorker = 'https://wnt.github.io/turku-game/game/isolation.js';
let worker;
const publishWait = performance.now();
do {
  worker = process.env.IMMUTABLE_WORKER ? fs.readFileSync(process.env.IMMUTABLE_WORKER, 'utf8')
    : await (await fetch(`${publishedWorker}?probe=${Date.now()}`, { signal: AbortSignal.timeout(5000), cache: 'no-store' })).text();
  if (baseline || worker.includes('turku3d-asset-index')) break;
  // Packaging/upload does not wait for GitHub Pages CDN propagation. This observation belongs
  // in the bounded post-deploy probe, never in deployment or the five-second smoke.
  if (performance.now() - publishWait >= 120_000) throw new Error('Pages still serves the old SW after 120 seconds');
  await new Promise(resolve => setTimeout(resolve, 2000));
} while (true);
assert(worker.includes('turku3d-immutable-v1'));
const base = 'https://turku.lab.madekivi.fi';
const root = `${base}/coarse/t0_0_0/`;
const fragment = await (await fetch(`${root}collision.json`)).json();
const visual = new URL(fragment.visual.t0_0_0.url, root).href;
const glb = await (await fetch(visual)).arrayBuffer();
const jsonSize = new DataView(glb).getUint32(12, true);
const json = JSON.parse(new TextDecoder().decode(new Uint8Array(glb, 20, jsonSize)));
const resources = [...(json.images ?? []), ...(json.buffers ?? [])].flatMap(item => item.uri ? [new URL(item.uri, visual).href] : []);
const tileSet = [...new Set([visual, ...resources, ...Object.values(fragment.collision).map(item => new URL(item.url, root).href)])];
const texture = resources.find(url => /\/shared\/.*\.ktx2$/.test(url)); assert(texture, 'Real tile needs shared KTX');
const logfile = path.join(os.homedir(), 'turku3d/profiling/evidence/cache-revisit/net.jsonl');
const captured = fs.existsSync(logfile) ? fs.readFileSync(logfile, 'utf8').trim().split('\n').flatMap(line => {
  try { return [JSON.parse(line)]; } catch { return []; }
}).filter(row => row.ev === 'req' && row.sw && row.status === 200 && /[a-f0-9]{64}\.(ktx2|bin|glb|col)$/.test(row.url)) : [];
const start = captured[0]?.t ?? 0;
const replay = captured.filter(row => row.t < start + 300_000).map(row => ({ url: new URL(row.url, base).href, at: (row.t - start) / 300_000 }));
const before = { requests: replay.length, unique: new Set(replay.map(row => row.url)).size,
  textures: replay.filter(row => row.url.endsWith('.ktx2')).length,
  uniqueTextures: new Set(replay.filter(row => row.url.endsWith('.ktx2')).map(row => row.url)).size };

const middleware = (req, res, next) => {
  if (['/cache/probe', '/http-probe'].includes(req.url)) { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><title>Asset cache probe</title>'); }
  else if (req.url?.startsWith('/cache/isolation.js')) { res.setHeader('Content-Type', 'application/javascript'); res.setHeader('Cache-Control', 'no-store'); res.end(worker); }
  else next();
};
const server = await createServer({ configFile: false, root: fileURLToPath(new URL('../', import.meta.url)),
  cacheDir: path.join(workRoot, 'cache/revisit-vite'), plugins: [{ name: 'inert-cache-probe', configureServer(server) { server.middlewares.use(middleware); } }],
  server: { host: '127.0.0.1', port: port(3), strictPort: true,
    headers: { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' } } });
await server.listen();
const local = `http://127.0.0.1:${port(3)}/cache/probe`;
let context;
process.on('SIGTERM', async () => { await context?.close(); await server.close(); process.exit(143); });
const result = { baseline, duration, publishWaitMs: performance.now() - publishWait, before, tileAssets: tileSet.length, publishedWorker };
const profile = path.join(process.env.TURKU3D_BROWSER_DIR, `revisit-${baseline ? 'before' : 'after'}-${Date.now()}`);
const launch = async () => chromium.launchPersistentContext(profile, {
  executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs, headless: true, args: chromeArgs(),
});
try {
  console.log(JSON.stringify({ stage: 'launch' }));
  context = await launch(); let page = await context.newPage();
  page.on('pageerror', error => console.log(String(error)));
  page.on('console', message => { if (message.type() === 'error') console.log(message.text()); });
  await page.goto(local);

  console.log(JSON.stringify({ stage: 'register' }));
  await page.evaluate(async () => {
    await navigator.serviceWorker.register('/cache/isolation.js', { scope: '/cache/', updateViaCache: 'none' });
    await Promise.race([navigator.serviceWorker.ready, new Promise((_, reject) => setTimeout(() => reject(new Error('SW ready timeout')), 10000))]);
    if (!navigator.serviceWorker.controller) await new Promise(resolve => navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true }));
  });
  const readTile = async () => page.evaluate(async urls => {
    const start = performance.now();
    const rows = await Promise.all(urls.map(async url => {
      const t = performance.now(), response = await fetch(url, { signal: AbortSignal.timeout(20_000) }), body = await response.arrayBuffer();
      return { url, status: response.status, bytes: body.byteLength, ms: performance.now() - t };
    }));
    if (rows.some(row => row.status !== 200 || !row.bytes)) throw new Error(JSON.stringify(rows));
    return { ms: performance.now() - start, bytes: rows.reduce((s, r) => s + r.bytes, 0), assets: rows.length };
  }, tileSet);
  const inventory = async () => page.evaluate(async baseline => {
    if (!baseline) return new Promise(resolve => {
      const channel = new MessageChannel(); channel.port1.onmessage = event => resolve(event.data);
      navigator.serviceWorker.controller.postMessage({ type: 'turku3d-cache-stats' }, [channel.port2]);
    });
    const cache = await caches.open('turku3d-immutable-v1'), keys = await cache.keys();
    const sizes = await Promise.all(keys.map(async key => Number((await cache.match(key)).headers.get('X-Turku3D-Cache-Bytes'))));
    return { entries: keys.length, bytes: sizes.reduce((sum, size) => sum + size, 0), budget: 192 * 1024 ** 2 };
  }, baseline);
  console.log(JSON.stringify({ stage: 'fill', tileAssets: tileSet.length }));
  result.coldFill = await readTile();
  await page.waitForTimeout(1000); result.warm = await readTile();
  // A real browser restart gives a cold SW with both browser caches retained.
  console.log(JSON.stringify({ stage: 'restart' }));
  await context.close(); context = await launch(); page = await context.newPage(); await page.goto(local);
  console.log(JSON.stringify({ stage: 'cold-worker' }));
  result.coldWorker = await readTile(); result.initialInventory = await inventory();
  const httpPage = await context.newPage(); await httpPage.goto(new URL('/http-probe', local).href);
  result.cachePaths = await httpPage.evaluate(async urls => {
    const cache = await caches.open('turku3d-immutable-v1');
    const measure = async load => {
      const start = performance.now();
      for (const url of urls) await (await load(url)).arrayBuffer();
      return performance.now() - start;
    };
    return { assets: urls.length,
      httpMs: await measure(url => fetch(url, { cache: 'force-cache' })),
      storageMs: await measure(url => cache.match(url)) };
  }, tileSet.slice(0, 50));
  await httpPage.close();
  if (!baseline) {
    result.client = await page.evaluate(async url => {
      const THREE = await import('/node_modules/three/build/three.webgpu.js');
      const { LoadingKtx } = await import('/src/world/loadingKtx.ts');
      const { shareModelTextures, releaseModelTextures } = await import('/src/world/sharedAssets.ts');
      const renderer = new THREE.WebGPURenderer({ forceWebGL: true }); await renderer.init();
      const loader = new LoadingKtx().setTranscoderPath('/basis/').detectSupport(renderer);
      const before = performance.getEntriesByName(url).length;
      const maps = await Promise.all(Array.from({ length: 22 }, () => loader.loadAsync(url)));
      const scenes = maps.map(map => {
        const scene = new THREE.Group(); scene.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial({ map })));
        shareModelTextures(scene); return scene;
      });
      const textures = scenes.map(scene => scene.children[0].material.map);
      renderer.initTexture(textures[0]);
      let disposed = 0; textures[0].addEventListener('dispose', () => disposed++);
      releaseModelTextures(scenes[0], new Set([textures[0]]));
      renderer.initTexture(textures[1]);
      const after = performance.getEntriesByName(url).length;
      for (const scene of scenes.slice(1)) releaseModelTextures(scene, new Set([scene.children[0].material.map]));
      loader.dispose(); renderer.dispose();
      return { consumers: 22, requests: after - before, gpuTextures: new Set(textures).size,
        decodedSources: new Set(maps.map(map => map.source)).size, disposed };
    }, texture);
    assert.equal(result.client.requests, 1); assert.equal(result.client.gpuTextures, 1);
    assert.equal(result.client.decodedSources, 1); assert.equal(result.client.disposed, 0);
    if (duration) {
      console.log(JSON.stringify({ stage: 'five-minute-replay', requests: replay.length, unique: before.unique }));
      result.session = await page.evaluate(async ({ replay, duration }) => {
        const { AssetCache, sharedAssetKey } = await import('/src/world/sharedAssets.ts');
        const bytes = new AssetCache(64 * 1024 ** 2, value => value.byteLength);
        const started = performance.now(), counts = new Map(), failures = [], unavailable = new Set();
        let index = 0;
        const read = async row => {
          const load = async () => {
            counts.set(row.url, (counts.get(row.url) ?? 0) + 1);
            const response = await fetch(row.url, { signal: AbortSignal.timeout(15_000) });
            // Captured old hashes can have been retired on OCI before this fresh-profile replay.
            if (response.status === 404) { unavailable.add(row.url); return new ArrayBuffer(0); }
            if (!response.ok) throw new Error(`${response.status} ${row.url}`);
            return response.arrayBuffer();
          };
          try { const key = sharedAssetKey(row.url); await (key ? bytes.get(key, load) : load()); }
          catch (error) { failures.push(String(error)); }
        };
        await Promise.all(Array.from({ length: 6 }, async () => {
          while (index < replay.length) {
            const row = replay[index++], due = row.at * duration * 1000;
            const wait = due - (performance.now() - started);
            if (wait > 0) await new Promise(resolve => setTimeout(resolve, wait));
            await read(row);
          }
        }));
        const wait = duration * 1000 - (performance.now() - started);
        if (wait > 0) await new Promise(resolve => setTimeout(resolve, wait));
        const textures = [...counts].filter(([url]) => url.endsWith('.ktx2'));
        return { ms: performance.now() - started, requests: [...counts.values()].reduce((a, b) => a + b, 0),
          unique: counts.size, textures: textures.reduce((sum, [, count]) => sum + count, 0),
          uniqueTextures: textures.length, unavailable: [...unavailable], failures };
      }, { replay, duration });
      await page.waitForTimeout(1000); result.afterFiveMinutes = await inventory();
      result.revisitWarm = await readTile();
      console.log(JSON.stringify({ stage: 'restart' }));
  await context.close(); context = await launch(); page = await context.newPage(); await page.goto(local);
      result.revisitColdWorker = await readTile();
      assert.equal(result.session.failures.length, 0, JSON.stringify(result.session.failures));
    }
    await context.setOffline(true); result.offline = await readTile();
  }
  console.log(JSON.stringify(result));
} finally {
  fs.writeFileSync(path.join(output, baseline ? 'before.json' : 'after.json'), JSON.stringify(result, null, 2));
  await context?.close(); await server.close();
}
