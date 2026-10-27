// node --test tools/dev/localhost/localhost.test.mjs: verdicts, affected tiles, the dev-server routes and the
// service following a branch in a throwaway git repository (no Vite, no real data).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { verdict } from './status.mjs';
import { affectedTiles } from './tiles.mjs';
import { localhostGame, publicStatus } from './vitePlugin.mjs';
import { LocalhostGame } from './service.mjs';

// a <- b <- c <- d (linear history)
const order = ['a', 'b', 'c', 'd'];
const isAncestor = (x, y) => order.indexOf(x) <= order.indexOf(y);

test('verdict: live, green, skipped, red with and without the culprit, data-red, running', () => {
  assert.equal(verdict({ head: 'b', live: 'c', rows: [], running: null, isAncestor }).state, 'verified');
  assert.equal(verdict({ head: 'c', live: 'a', rows: [], running: null, isAncestor }).state, 'unverified');
  assert.deepEqual(verdict({ head: 'c', live: 'a', rows: [], running: { to: 'd', stage: 'chrome' }, isAncestor }),
    { state: 'unverified', detail: 'verifying: chrome' });
  const green = { id: 'd.1', from: 'a', to: 'd', result: 'green' };
  assert.equal(verdict({ head: 'c', live: 'a', rows: [green], running: null, isAncestor }).state, 'verified');
  assert.equal(verdict({ head: 'c', live: 'a', rows: [{ ...green, result: 'skipped' }], running: null, isAncestor }).detail, 'no live change');
  const red = { id: 'd.1', from: 'a', to: 'd', result: 'red', culprit: 'c', culprit_task: 'stairs', failure: { stage: 'chrome' } };
  assert.deepEqual(verdict({ head: 'd', live: 'a', rows: [red], running: null, isAncestor }),
    { state: 'failed', stage: 'chrome', detail: 'culprit c (stairs)' });
  assert.equal(verdict({ head: 'b', live: 'a', rows: [red], running: null, isAncestor }).state, 'unverified');
  const dataRed = { from: 'a', to: 'd', result: 'data-red', failure: { stage: 'tile-build' }, data_cause: 'data-root' };
  assert.equal(verdict({ head: 'd', live: 'a', rows: [dataRed], running: null, isAncestor }).stage, 'tile-build (data root)');
  // The newest row covering head decides: a red batch later rebuilt green.
  assert.equal(verdict({ head: 'd', live: 'a', rows: [red, green], running: null, isAncestor }).state, 'verified');
});

test('affected tiles: the union of the landings\' tiles, or every listed tile when one is unknown', () => {
  const listed = ['t0_0_0', 't0_0_1', 't0_1_0'];
  assert.deepEqual(affectedTiles([{ tiles: ['t0_0_1'] }, { tiles: ['t0_0_1', 't0_1_0', 't0_9_9'] }], listed), ['t0_0_1', 't0_1_0']);
  assert.deepEqual(affectedTiles([{ tiles: [] }, { name: 'old row' }], listed), listed);
  assert.deepEqual(affectedTiles([], listed), listed);
  assert.deepEqual(affectedTiles([null], listed), listed);
});

test('dev-server routes: status, tiles from the selected release, events, telemetry; preview: telemetry', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'localhost-plugin-'));
  const release = path.join(dir, 'release'), fallback = path.join(dir, 'fallback');
  for (const [root, body] of [[release, 'R'], [fallback, 'F']]) {
    fs.mkdirSync(path.join(root, 'collision'), { recursive: true });
    fs.writeFileSync(path.join(root, 'collision/c_0_0.col'), body);
  }
  const statePath = path.join(dir, 'state.json'), events = path.join(dir, 'events.jsonl');
  const handlers = [], sent = [];
  const server = { middlewares: { use: h => handlers.push(h) }, ws: { send: m => sent.push(m) }, httpServer: null };
  const serve = (state) => {
    if (state) fs.writeFileSync(statePath, JSON.stringify(state));
    handlers.length = 0;
    localhostGame({ statePath: state ? statePath : undefined, tiles: fallback, telemetryDir: path.join(dir, 'telemetry'),
      candidates: path.join(dir, 'candidates') }).configureServer(server);
    return http.createServer((req, res) => handlers[0](req, res, () => { res.writeHead(418); res.end(); }));
  };
  const request = (srv, p, init) => new Promise((resolve) => srv.listen(0, '127.0.0.1', async () => {
    const r = await fetch(`http://127.0.0.1:${srv.address().port}${p}`, init);
    const body = r.status === 204 ? '' : await r.text();
    srv.close(); resolve({ status: r.status, body, cache: r.headers.get('cache-control') });
  }));
  t.after(() => { fs.unwatchFile(statePath); fs.rmSync(dir, { recursive: true, force: true }); });

  assert.deepEqual(JSON.parse((await request(serve(null), '/__localhost/status')).body), { service: false });
  assert.equal((await request(serve(null), '/tiles/collision/c_0_0.col')).body, 'F');
  assert.equal((await request(serve(null), '/src/main.ts')).status, 418); // Vite's own routes pass through
  assert.match((await request(serve(null), '/facades/candidates/')).body, /No candidates kept yet/);
  const state = { commit: 'c'.repeat(40), verification: { state: 'failed', stage: 'chrome' }, events, store: dir,
    tiles: { dir: release, key: 'k1', source: 'integrator', building: null } };
  const tiles = await request(serve(state), '/tiles/collision/c_0_0.col');
  assert.equal(tiles.body, 'R');
  assert.equal(tiles.cache, 'no-cache');
  assert.equal((await request(serve(state), '/tile-store/release/collision/c_0_0.col')).body, 'R');
  const traversal = serve(state);
  const status = await new Promise((resolve) => traversal.listen(0, '127.0.0.1', () => {
    http.get({ host: '127.0.0.1', port: traversal.address().port, path: '/tiles/../fallback/collision/c_0_0.col' },
      (r) => { r.resume(); traversal.close(); resolve(r.statusCode); });
  }));
  assert.equal(status, 404); // the path is clamped under the release, never outside it
  assert.deepEqual(JSON.parse((await request(serve(state), '/__localhost/status')).body), publicStatus(state));
  assert.equal((await request(serve(state), '/__localhost/event', { method: 'POST', body: '{"type":"hot-swap"}' })).status, 204);
  assert.equal(JSON.parse(fs.readFileSync(events, 'utf8')).type, 'hot-swap');
  assert.equal((await request(serve(state), '/telemetry', { method: 'POST', body: '{"t":1}\n' })).status, 204);
  assert.equal(fs.readdirSync(path.join(dir, 'telemetry')).length, 1);

  // vite preview (the browser checks' server): POST /telemetry into the same directory, everything else passes.
  handlers.length = 0;
  localhostGame({ tiles: fallback, telemetryDir: path.join(dir, 'preview-telemetry') }).configurePreviewServer(server);
  const preview = () => http.createServer((req, res) => handlers[0](req, res, () => { res.writeHead(418); res.end(); }));
  assert.equal((await request(preview(), '/telemetry', { method: 'POST', body: '{"t":2}\n' })).status, 204);
  assert.equal(fs.readdirSync(path.join(dir, 'preview-telemetry')).length, 1);
  assert.equal((await request(preview(), '/tiles/collision/c_0_0.col')).status, 418);
});

test('the service follows the branch: checkout, ledger time, verdict in state.json', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'localhost-service-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const origin = path.join(dir, 'origin'), registry = path.join(dir, 'registry'), published = path.join(dir, 'data');
  const git = (cwd, ...args) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
  fs.mkdirSync(origin); fs.mkdirSync(registry); fs.mkdirSync(published);
  git(origin, 'init', '-q', '-b', 'main');
  const commit = (file, text) => {
    fs.writeFileSync(path.join(origin, file), text);
    git(origin, 'add', '-A'); git(origin, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', file);
    return git(origin, 'rev-parse', 'HEAD');
  };
  const first = commit('a.txt', 'one');
  const worktree = path.join(dir, 'worktree');
  git(origin, 'worktree', 'add', '-q', '--detach', worktree, 'main');
  const service = new LocalhostGame({ repo: origin, branch: 'main', worktree, registry, published, store: path.join(published, 'tile-store'),
    stateDir: path.join(dir, 'state'), telemetryDir: path.join(dir, 'telemetry'), host: '127.0.0.1', port: 0, pollMs: 1000, build: false });
  service.tiles = { selection: { dir: path.join(published, 'live/tiles'), key: null, source: 'live' }, select: async () => service.tiles.selection, stop() {} };
  fs.mkdirSync(service.o.stateDir, { recursive: true });
  service.current = first;
  const second = commit('b.txt', 'two');
  fs.writeFileSync(path.join(registry, 'landed.jsonl'), JSON.stringify({ name: 'b', commit: second, time: 123 }) + '\n');
  fs.writeFileSync(path.join(registry, 'integrations.jsonl'),
    JSON.stringify({ id: 'x.1', from: first, to: second, result: 'red', culprit: second, failure: { stage: 'chrome' } }) + '\n');
  await service.tick();
  await service.selecting;
  await service.writing;
  assert.equal(git(worktree, 'rev-parse', 'HEAD'), second);
  assert.ok(fs.existsSync(path.join(worktree, 'b.txt')));
  const state = JSON.parse(fs.readFileSync(path.join(service.o.stateDir, 'state.json'), 'utf8'));
  assert.equal(state.commit, second);
  assert.deepEqual(state.verification, { state: 'failed', stage: 'chrome', detail: `culprit ${second.slice(0, 7)}` });
  const checkout = fs.readFileSync(service.events, 'utf8').split('\n').filter(Boolean).map(JSON.parse).find(r => r.type === 'checkout');
  assert.deepEqual([checkout.commit, checkout.landed, checkout.task], [second, 123, 'b']);
  service.stop();
});
