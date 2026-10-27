// node --test tools/dev/localhost/localhost.test.mjs: verdicts, affected tiles, the dev-server routes, the
// service following a branch in a throwaway git repository and supervising a fake coarse service (no Vite, no real
// data, no real coarse service).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { verdict } from './status.mjs';
import { affectedTiles, needsQuickBuild } from './tiles.mjs';
import { localhostGame, publicStatus } from './vitePlugin.mjs';
import { CoarseService } from './coarse.mjs';
import { LocalhostGame } from './service.mjs';

// a <- b <- c <- d (linear history)
const order = ['a', 'b', 'c', 'd'];
const isAncestor = (x, y) => order.indexOf(x) <= order.indexOf(y);

test('verdict: live, green, tests red, skipped, red with and without the culprit, data-red, running', () => {
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
  // Published with failing advisory checks: tests red, also once the commit is live.
  const advisory = { ...green, advisory: [{ stage: 'chrome', specs: ['webgpu:a.spec.ts'] }, { stage: 'chrome' }, { stage: 'facade-ground-audit' }] };
  for (const live of ['a', 'd']) {
    assert.deepEqual(verdict({ head: 'c', live, rows: [advisory], running: null, isAncestor }),
      { state: 'tests-red', stage: 'chrome, facade-ground-audit', detail: 'integration d.1' });
  }
});

test('affected tiles: the union of the landings\' tiles, or every listed tile when one is unknown', () => {
  const listed = ['t0_0_0', 't0_0_1', 't0_1_0'];
  assert.deepEqual(affectedTiles([{ tiles: ['t0_0_1'] }, { tiles: ['t0_0_1', 't0_1_0', 't0_9_9'] }], listed), ['t0_0_1', 't0_1_0']);
  assert.deepEqual(affectedTiles([{ tiles: [] }, { name: 'old row' }], listed), listed);
  assert.deepEqual(affectedTiles([], listed), listed);
  assert.deepEqual(affectedTiles([null], listed), listed);
});

test('a quick build runs for new tile code or published facades and layers, not for a list change alone', () => {
  const base = { commit: 'c' };
  assert.equal(needsQuickBuild(base, 'd', []), true);
  assert.equal(needsQuickBuild(base, 'c', ['facade-approvals']), true);
  assert.equal(needsQuickBuild(base, 'c', ['layers', 'playable-tiles']), true);
  assert.equal(needsQuickBuild(base, 'c', ['playable-tiles']), false);
  assert.equal(needsQuickBuild(base, 'c', []), false);
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
    tiles: { dir: release, key: 'k1', source: 'integrator', building: null }, coarse: { commit: 'c'.repeat(40), state: 'running' } };
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

/** A free localhost TCP port. */
const freePort = () => new Promise((resolve) => {
  const s = net.createServer().listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => resolve(port)); });
});
const until = async (predicate, ms = 10_000) => {
  const end = Date.now() + ms;
  while (!(await predicate())) {
    if (Date.now() > end) throw new Error('timed out');
    await new Promise(r => setTimeout(r, 25));
  }
};
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };

/** A CoarseService running the fake service (fixtures/fakeCoarse.mjs) in `dir`. */
async function fakeCoarse(dir, extra = {}) {
  const rows = [];
  const service = new CoarseService({ command: () => ({ cmd: process.execPath,
    args: [path.join(path.dirname(new URL(import.meta.url).pathname), 'fixtures/fakeCoarse.mjs'), dir, '--port', String(service.port)],
    cwd: dir, env: process.env }), head: () => 'c'.repeat(40), host: '127.0.0.1', port: await freePort(),
  logFile: path.join(dir, 'coarse.log'), log: (row) => rows.push(row), pollMs: 25, backoffMs: 100, ...extra });
  const starts = () => fs.readFileSync(path.join(dir, 'starts.jsonl'), 'utf8').split('\n').filter(Boolean).map(JSON.parse);
  const set = (name, value) => fs.writeFileSync(path.join(dir, name), JSON.stringify(value));
  return { service, rows, starts, set };
}

test('coarse supervisor: a restart drains running builds first, stops the build processes, restarts after a crash', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'localhost-coarse-'));
  const { service, rows, starts, set } = await fakeCoarse(dir, { drainMs: 5000 });
  t.after(() => { service.stop(); fs.rmSync(dir, { recursive: true, force: true }); });
  await service.start();
  assert.deepEqual(service.status, { commit: 'c'.repeat(40), state: 'running' });
  const [first] = starts();

  // A build runs: the restart waits for it (the service starts no new ones after /coarse/drain).
  set('building.json', ['t0_1_0']);
  const restarted = service.restart();
  await until(() => fs.existsSync(path.join(dir, 'drained')));
  await new Promise(r => setTimeout(r, 300));
  assert.equal(service.status.state, 'draining');
  assert.ok(alive(first.pid));
  set('building.json', []);
  await restarted;
  assert.equal(service.status.state, 'running');
  const second = starts()[1];
  assert.notEqual(second.pid, first.pid);
  assert.ok(!alive(first.pid) && !alive(first.build)); // its process group went with it
  assert.deepEqual(rows.map(r => r.state), ['starting', 'running', 'draining', 'restarting', 'starting', 'running']);

  // A crash is restarted after the backoff.
  process.kill(second.pid, 'SIGKILL');
  await until(() => starts().length === 3 && service.status.state === 'running');
  assert.equal(rows.find(r => r.state === 'exited').signal, 'SIGKILL');
  process.kill(second.build, 'SIGTERM');
});

test('coarse supervisor: the drain waits at most drainMs; a held port is retried, not fought over', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'localhost-coarse-'));
  const { service, starts, set } = await fakeCoarse(dir, { drainMs: 400 });
  t.after(() => { service.stop(); fs.rmSync(dir, { recursive: true, force: true }); });
  await service.start();
  set('building.json', ['t0_1_0']); // a build that never ends
  const began = Date.now();
  await service.restart();
  const waited = Date.now() - began;
  // The build never ends, so any finished restart proved the bound; the restart itself (a new child) is slow under load.
  assert.ok(waited >= 400 && waited < 30000, `restart took ${waited} ms`);
  assert.equal(starts().length, 2);
  service.stop();
  await until(() => starts().every(({ pid, build }) => !alive(pid) && !alive(build)));

  // Another process (a hand-started coarse-tiles) holds the port: the supervisor waits for it instead of crashing.
  const held = net.createServer().listen(service.port, '127.0.0.1');
  await new Promise(r => held.once('listening', r));
  const other = (await fakeCoarse(dir, { port: service.port })).service;
  await other.start();
  assert.equal(other.status.state, 'port in use');
  assert.equal(other.child, null);
  other.stop();
  held.close();
});

test('the service restarts the coarse service when a landing changes a file it loaded, not otherwise', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'localhost-follow-coarse-'));
  const origin = path.join(dir, 'origin'), registry = path.join(dir, 'registry'), published = path.join(dir, 'data');
  const git = (cwd, ...args) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
  for (const d of [origin, registry, published, path.join(dir, 'fake')]) fs.mkdirSync(d);
  git(origin, 'init', '-q', '-b', 'main');
  const commit = (file, text) => {
    fs.mkdirSync(path.dirname(path.join(origin, file)), { recursive: true });
    fs.writeFileSync(path.join(origin, file), text);
    git(origin, 'add', '-A'); git(origin, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', file);
    return git(origin, 'rev-parse', 'HEAD');
  };
  const first = commit('pipeline/turku3d/coarse/service.py', 'one');
  const worktree = path.join(dir, 'worktree');
  git(origin, 'worktree', 'add', '-q', '--detach', worktree, 'main');
  const service = new LocalhostGame({ repo: origin, branch: 'main', worktree, registry, published, store: path.join(published, 'tile-store'),
    stateDir: path.join(dir, 'state'), telemetryDir: path.join(dir, 'telemetry'), host: '127.0.0.1', port: 0, pollMs: 1000, build: false,
    coarse: false });
  service.tiles = { selection: null, select: async () => null, stop() {} };
  fs.mkdirSync(service.o.stateDir, { recursive: true });
  service.current = first;
  const fake = await fakeCoarse(path.join(dir, 'fake'), { head: () => service.current, onState: () => { void service.write(); },
    log: (row) => service.log(row) });
  service.coarse = fake.service;
  t.after(() => { service.stop(); for (const { build } of fake.starts()) if (alive(build)) process.kill(build, 'SIGTERM');
    fs.rmSync(dir, { recursive: true, force: true }); });
  fake.set('modules.json', ['pipeline/turku3d/coarse/service.py', 'pipeline/turku3d/config.py']);
  await service.coarse.start();

  const docs = commit('docs/notes.md', 'unrelated');
  await service.tick();
  assert.equal(git(worktree, 'rev-parse', 'HEAD'), docs);
  assert.equal(service.coarse.restarting, null);
  const module = commit('pipeline/turku3d/config.py', 'loaded by the service');
  await service.tick();
  await service.coarse.restarting;
  assert.equal(fake.starts().length, 2);
  await service.writing;
  const state = JSON.parse(fs.readFileSync(path.join(service.o.stateDir, 'state.json'), 'utf8'));
  assert.deepEqual(state.coarse, { commit: module, state: 'running' });
  assert.deepEqual(publicStatus(state).coarse, state.coarse);
  const events = fs.readFileSync(service.events, 'utf8').split('\n').filter(Boolean).map(JSON.parse).filter(r => r.type === 'coarse');
  assert.deepEqual(events.slice(-2).map(r => [r.commit, r.state]), [[module, 'starting'], [module, 'running']]);
  // The launcher restarts it whatever the service reports loading.
  commit('tools/dev/coarse-tiles', '#!/bin/sh');
  await service.tick();
  await service.coarse.restarting;
  assert.equal(fake.starts().length, 3);
  await service.writing;
});

test('a dev-server change before the first start or during a restart starts exactly one server', () => {
  const service = Object.create(LocalhostGame.prototype);
  let started = 0;
  service.startVite = () => { started += 1; service.vite = { once() {}, kill() {} }; };
  service.vite = null;
  service.restartVite(); // follow() on startup, before start() launches the server
  assert.equal(started, 0);
  service.startVite();
  let exit;
  service.vite = { once: (_, f) => { exit = f; }, kill() {} };
  service.restartVite();
  service.restartVite(); // a second change while the old server is still exiting
  exit();
  assert.equal(started, 2);
});
