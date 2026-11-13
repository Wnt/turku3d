#!/usr/bin/env node
// Builds and captures a view set whose areas are their own locations (a random street set) and leaves one capture
// directory a judge can read (docs/design/world-pipeline.md, section 7).
//
//   node scripts/dev/world-set.mjs --views FILE --out DIR [--baseline DIR]... [--build-host local|air|osg]
//     [--jobs N] [--data REL]... [--capture-host auto|mac|air|mac,air] [--background 400] [--only AREA,AREA]
//     [--build-only | --skip-build] [--drop] [--label TEXT] [--ready-timeout 600]
//
// Every registry `area` of the set's views is one location: its query is the circle that covers the area's cameras
// and what they look at (as `world build --area` does for a pilot area). First every location is built with
// `world build --at E,N --radius R --name AREA` on the build host, --jobs at a time (`scripts/dev/on-host`: the
// store merges back; --data REL names what the host needs from $TURKU3D_DATA besides the stored descriptions).
// Then each is captured with `scene-capture.mjs --world AREA --area AREA` in a leased browser (`auto`: the Mac
// when a slot is free, else the Air; `mac,air`: one browser on each at once, the manifest says which shot what). --build-only stops after the builds, so the points of one set can be built on
// several hosts at once into the same DIR; --skip-build then captures what is built and reads the build logs
// already in DIR. --drop removes the set's manifests and meshes from this store when everything is captured.
// DIR gets current/<view>.png, manifest.json (every view, and per location its query, build and capture hosts,
// timings and failures), compare.jpg/html (photo | baselines | new world), and the per-location captures and
// logs under locations/ and logs/. A location that fails to build or load is reported; the rest continues.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { comparable, compare, readViews } from './scene-capture.mjs';
import { port, worldStore } from '../../tools/dev/environment.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const argv = process.argv.slice(2);
const opt = (name, fallback) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const many = name => argv.flatMap((a, i) => a === name ? [argv[i + 1]] : []);
const home = process.env.HOME ?? os.homedir();
const LEASES = path.join(home, 'turku3d/tools/host-lease');

/** The circle that covers an area's cameras and the points 40 m ahead of them, with 90 m around (driver.area_of_views). */
export function queryOf(views) {
  const points = views.flatMap(v => {
    const { E, N, yaw } = v.pose, turn = yaw * Math.PI / 180;
    return [[E, N], [E + 40 * Math.sin(turn), N + 40 * Math.cos(turn)]];
  });
  const mid = k => (Math.min(...points.map(p => p[k])) + Math.max(...points.map(p => p[k]))) / 2;
  const e = Math.round(mid(0) * 10) / 10, n = Math.round(mid(1) * 10) / 10;
  const reach = Math.max(...points.map(p => Math.hypot(p[0] - e, p[1] - n)));
  return { e, n, radius: Math.ceil((reach + 90) / 10) * 10 };
}

/** What a build log says: the host of its `on-host` line and the totals `world build` printed. */
function buildOf(log, seconds = null, exit = null) {
  if (!fs.existsSync(log)) return null;
  const text = fs.readFileSync(log, 'utf8');
  const total = /total\s+([\d.]+) s: (\d+) entities, (\d+) triangles/.exec(text);
  const failed = [...text.matchAll(/^\s*FAILED (\S+): (.*)$/gm)].map(m => `${m[1]}: ${m[2]}`.slice(0, 200));
  return { host: /^\$ scripts\/dev\/on-host (\S+)/m.exec(text)?.[1] ?? 'local', seconds, exit: exit ?? (total ? 0 : 1),
    buildSeconds: total ? Number(total[1]) : null, entities: total ? Number(total[2]) : null, triangles: total ? Number(total[3]) : null, failed };
}

/** Runs a command with its output in a log file; resolves its exit code and wall seconds. */
function run(command, args, log, options = {}) {
  return new Promise(resolve => {
    const started = Date.now(), file = fs.openSync(log, options.fresh ? 'w' : 'a');
    delete options.fresh;
    fs.writeSync(file, `$ ${[command, ...args].join(' ')}\n`);
    const child = spawn(command, args, { cwd: repo, stdio: ['ignore', file, file], ...options });
    child.on('close', code => { fs.closeSync(file); resolve({ code: code ?? 1, seconds: Math.round((Date.now() - started) / 100) / 10 }); });
    child.on('error', () => { fs.closeSync(file); resolve({ code: 1, seconds: 0 }); });
  });
}

/** The Mac when one of its browser slots is free and nobody queues for it, else the Air. */
function captureHost() {
  try {
    const status = execFileSync('python3', [path.join(LEASES, 'lease.py'), 'status'], { encoding: 'utf8', timeout: 30000 });
    const mac = status.split(/^== /m).find(block => block.startsWith('mac')) ?? '';
    const slots = /func cap (\d+), (\d+)\/\d+ slots used/.exec(mac);
    return slots && Number(slots[2]) < Number(slots[1]) && !/^\s*queued /m.test(mac) ? 'mac' : 'air';
  } catch { return 'air'; }
}

async function devServer(base, log) {
  const up = async () => { try { return (await fetch(base, { signal: AbortSignal.timeout(3000) })).ok; } catch { return false; } };
  if (await up()) return null;
  const file = fs.openSync(log, 'a');
  const child = spawn('npx', ['vite', '--host', '127.0.0.1'], { cwd: path.join(repo, 'game'), stdio: ['ignore', file, file] });
  for (let i = 0; i < 60 && !(await up()); i++) await new Promise(r => setTimeout(r, 500));
  if (!(await up())) { child.kill(); throw new Error(`the dev server did not answer at ${base} (see ${log})`); }
  return child;
}

async function main() {
  const viewsFile = path.resolve(opt('--views'));
  const views = readViews(viewsFile);
  const out = path.resolve(opt('--out'));
  const only = opt('--only', '').split(',').filter(Boolean);
  const buildHost = opt('--build-host', 'local'), wantedCapture = opt('--capture-host', 'auto');
  const background = Number(opt('--background', 400)), readyTimeout = Number(opt('--ready-timeout', 600));
  const baselines = many('--baseline').map(dir => path.resolve(dir));
  const sandbox = process.env.TURKU3D_SANDBOX;
  if (!sandbox) throw new Error('source ../sandbox.env first (TURKU3D_SANDBOX names the lease)');
  const areas = new Map();
  for (const view of views.views) {
    if (!view.area || !view.pose) throw new Error(`view ${view.id} has no registry area or pose: a set's views come from reference photos`);
    if (!only.length || only.includes(view.area)) areas.set(view.area, [...areas.get(view.area) ?? [], view]);
  }
  if (!areas.size) throw new Error('no area of the set is selected');
  for (const dir of ['current', 'locations', 'logs']) fs.mkdirSync(path.join(out, dir), { recursive: true });
  const base = `http://localhost:${port()}/`, started = new Date();
  const manifest = { schema: 'turku-capture/1', label: opt('--label', `world set ${path.basename(path.dirname(viewsFile))}`), started: started.toISOString(),
    base, mode: 'render', entry: 'render', settings: null, viewsFile: path.relative(repo, viewsFile), views: [], variants: [], checks: {},
    errors: [], consoleErrors: [], failedRequests: [], set: { background, locations: [] }, source: null };
  const write = () => fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify(manifest, null, 1) + '\n');
  const rows = [...areas].map(([area, mine]) => ({ area, views: mine.map(v => v.id), query: queryOf(mine), build: null, capture: null, failure: null }));
  manifest.set.locations = rows;
  const buildLog = area => path.join(out, `logs/build-${area}.log`);
  if (!argv.includes('--skip-build')) {
    const data = ['world/descriptions', ...many('--data')].flatMap(rel => ['--data', rel]);
    // One call first copies the checkout and the data, so the parallel calls after it find nothing left to copy.
    if (buildHost !== 'local') await run('scripts/dev/on-host', [buildHost, ...data, '--', 'python', '-c', 'pass'], path.join(out, `logs/prime-${buildHost}.log`), { fresh: true });
    const queue = [...rows];
    await Promise.all(Array.from({ length: Math.max(1, Number(opt('--jobs', 1))) }, async (_, worker) => {
      // A dozen ssh connections opened in one instant are partly refused by the host: start the workers apart.
      await new Promise(r => setTimeout(r, worker * 4000));
      for (let row = queue.shift(); row; row = queue.shift()) {
        const { query, area } = row;
        const build = ['python', '-m', 'turku3d.world', 'build', '--at', `${query.e},${query.n}`, '--radius', String(query.radius), '--background', String(background), '--name', area];
        const once = () => buildHost === 'local' ? run('bash', ['pipeline/env/run.sh', ...build], buildLog(area), { fresh: true })
          : run('scripts/dev/on-host', [buildHost, ...data, '--', ...build], buildLog(area), { fresh: true });
        let done = await once();
        // A call that fails at once did not reach the build (a refused connection): one more try.
        if (done.code && done.seconds < 20) { await new Promise(r => setTimeout(r, 5000)); done = await once(); }
        row.build = buildOf(buildLog(area), done.seconds, done.code);
        console.error(`[world-set] built ${area} on ${buildHost}: exit ${done.code}, ${done.seconds} s`);
      }
    }));
  }
  if (argv.includes('--build-only')) {
    console.log(JSON.stringify({ out, built: rows.map(row => ({ area: row.area, ...row.build })) }));
    if (rows.some(row => row.build?.exit)) process.exitCode = 1;
    return;
  }
  const server = await devServer(base, path.join(out, 'logs/vite.log'));
  try {
    const pending = [...rows];
    await Promise.all(wantedCapture.split(',').map(async wanted => { for (let row = pending.shift(); row; row = pending.shift()) {
      const { area } = row;
      row.build ??= buildOf(buildLog(area));
      if (row.build?.exit) { row.failure = `build exited ${row.build.exit} (logs/build-${area}.log)`; write(); continue; }
      if (!fs.existsSync(path.join(worldStore, 'locations', `${area}.json`))) { row.failure = 'no manifest of the location in this store after the build'; write(); continue; }
      const host = wanted === 'auto' ? captureHost() : wanted, dir = path.join(out, 'locations', area);
      const session = ['run', host, host === 'mac' ? sandbox : `${sandbox}-${host}`, '--kind', 'func', '--window-size', '1920,1080', '--load-timeout', String(readyTimeout + 100),
        ...(host === 'mac' ? [] : ['--headless', '--forward', String(port())]), '--',
        'node', 'scripts/dev/scene-capture.mjs', '--mode', 'render', '--world', area, '--area', area, '--views', viewsFile, '--base', base,
        ...baselines.flatMap(b => ['--baseline', b]), '--ready-timeout', String(readyTimeout), '--out', dir, '--label', `${manifest.label}: ${area}`];
      const shot = await run('python3', [path.join(LEASES, 'session.py'), ...session], path.join(out, `logs/capture-${area}.log`), { fresh: true });
      const part = fs.existsSync(path.join(dir, 'manifest.json')) ? JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8')) : null;
      row.capture = { host, seconds: shot.seconds, exit: shot.code, loadMs: part?.timings?.loadMs ?? null, world: part?.world ? { revision: part.world.revision, built: part.world.built, drawn: part.world.drawn } : null };
      if (!part || part.failure || !part.views?.length) row.failure = `capture failed: ${String(part?.failure ?? `exit ${shot.code}`).split('\n')[0]} (logs/capture-${area}.log)`;
      else {
        manifest.settings ??= part.settings; manifest.source ??= part.source;
        for (const view of part.views) {
          fs.copyFileSync(path.join(dir, view.file), path.join(out, view.file));
          manifest.views.push({ ...view, world: area, host });
        }
        for (const key of ['errors', 'consoleErrors', 'failedRequests']) manifest[key].push(...(part[key] ?? []));
      }
      console.error(`[world-set] captured ${area} on ${host}: ${row.failure ?? `${part.views.length} views, load ${part.timings?.loadMs} ms`}`);
      write();
    } }));
    if (argv.includes('--drop')) {
      // Only what a capture needs stays on this machine: everything is captured, the set's manifests and meshes go.
      for (const row of rows) fs.rmSync(path.join(worldStore, 'locations', `${row.area}.json`), { force: true });
      await run('bash', ['pipeline/env/run.sh', 'python', '-m', 'turku3d.world', 'gc'], path.join(out, 'logs/drop.log'), { fresh: true });
    }
  } finally {
    server?.kill();
    manifest.finished = new Date().toISOString();
    manifest.timings = { totalMs: Date.now() - started.getTime() };
    manifest.settings ??= { quality: views.quality, time: views.time, weather: views.weather, viewport: views.viewport, renderScale: views.renderScale };
    manifest.baselines = baselines.map(dir => {
      const other = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
      return { dir, label: other.label, source: other.source, ...comparable(manifest, other) };
    });
    manifest.views.sort((a, b) => views.views.findIndex(v => v.id === a.id) - views.views.findIndex(v => v.id === b.id));
    const failed = manifest.set.locations.filter(row => row.failure);
    if (failed.length) manifest.failure = `${failed.length} of ${manifest.set.locations.length} locations failed: ${failed.map(row => `${row.area}: ${row.failure}`).join('; ')}`;
    write();
    if (manifest.views.length) compare(out, manifest);
  }
  console.log(JSON.stringify({ out, views: manifest.views.length, locations: manifest.set.locations.map(row => ({ area: row.area, build: row.build?.seconds ?? null, capture: row.capture?.seconds ?? null, host: row.capture?.host ?? null, failure: row.failure })) }));
  if (manifest.failure) process.exitCode = 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
