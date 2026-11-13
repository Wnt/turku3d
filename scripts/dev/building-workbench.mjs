#!/usr/bin/env node
// The building workbench's browser side: one generated building in the game's renderer beside its reference photo.
//
//   scripts/dev/building-workbench.mjs serve                     start the workbench dev server (sandbox port +3)
//   scripts/dev/building-workbench.mjs shot NAME [--out FILE.jpg] [--weather overcast] [--time 14:00] [--keep]
//   scripts/dev/building-workbench.mjs release                   close the browser and give the host lease back
//
// `shot` shows $TURKU3D_WORK/workbench/NAME (built by `python -m turku3d.world.kinds.building.workbench build`) from
// the camera of its reference view and saves photo and render side by side. It takes a short host lease
// (~/turku3d/tools/host-lease/lease.py, host mac) for the sandbox's preview browser and gives it back afterwards;
// `--keep` holds browser and lease for a series of shots, which then take about a second each. End a series with
// `release`. Source ../sandbox.env first.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { port, workRoot } from '../../tools/dev/environment.mjs';
import { previewState, touchPreview } from './preview-session.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const argv = process.argv.slice(2), command = argv[0];
const opt = (name, fallback) => argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback;
const base = `http://localhost:${port(3)}`;
const lease = path.join(os.homedir(), 'turku3d/tools/host-lease/lease.py');
const holder = process.env.TURKU3D_SANDBOX ?? 'workbench';
const session = path.join(repo, 'scripts/dev/preview-session.mjs');

async function up() {
  try { return (await fetch(`${base}/building-workbench.html`, { signal: AbortSignal.timeout(1500) })).ok; } catch { return false; }
}

async function serve() {
  if (await up()) return;
  const log = path.join(workRoot, 'workbench/vite.log');
  fs.mkdirSync(path.dirname(log), { recursive: true });
  const out = fs.openSync(log, 'a');
  spawn(path.join(repo, 'game/node_modules/.bin/vite'), ['--config', 'vite.workbench.config.ts', '--host', '127.0.0.1'],
    { cwd: path.join(repo, 'game'), detached: true, stdio: ['ignore', out, out], env: process.env }).unref();
  for (let k = 0; k < 100; k++) { if (await up()) return; await new Promise(r => setTimeout(r, 200)); }
  throw new Error(`the workbench dev server did not start; see ${log}`);
}

function acquire() {
  for (let attempt = 0; attempt < 30; attempt++) {
    const result = spawnSync('python3', [lease, 'acquire', 'mac', holder, '--kind', 'func', '--ttl', '20m'], { encoding: 'utf8' });
    if (result.status === 0) return;
    if (result.status !== 3) throw new Error(`host lease failed: ${result.stdout}${result.stderr}`);
    spawnSync('sleep', ['10']);
  }
  throw new Error('no browser slot on the Mac within five minutes');
}

function release() {
  spawnSync(process.execPath, [session, 'stop'], { stdio: 'inherit', env: process.env });
  spawnSync('python3', [lease, 'release', holder, 'mac'], { stdio: 'inherit' });
}

async function shot() {
  const name = argv[1];
  if (!name || name.startsWith('--')) throw new Error('usage: building-workbench.mjs shot NAME [--out FILE.jpg]');
  const started = Date.now();
  await serve();
  const params = new URLSearchParams({ name, shot: '1' });
  for (const key of ['weather', 'time', 'quality']) if (opt(`--${key}`)) params.set(key, opt(`--${key}`));
  const url = `${base}/building-workbench.html?${params}`;
  let state = previewState();
  const running = state && (() => { try { process.kill(state.pid, 0); return true; } catch { return false; } })();
  if (running) {
    const touched = spawnSync('python3', [lease, 'touch', holder, 'mac'], { encoding: 'utf8' });
    if (touched.status === 4) acquire();
  } else {
    acquire();
    state = JSON.parse(execFileSync(process.execPath, [session, 'start', '--url', 'about:blank', '--idle-minutes', '10'], { encoding: 'utf8', env: process.env }).trim().split('\n').pop());
  }
  touchPreview();
  const { chromium } = createRequire(path.join(repo, 'game/package.json'))('playwright');
  const browser = await chromium.connectOverCDP(state.cdp);
  try {
    const context = browser.contexts()[0];
    const page = context.pages()[0] ?? await context.newPage();
    const meta = JSON.parse(fs.readFileSync(path.join(workRoot, 'workbench', name, 'meta.json'), 'utf8'));
    const width = meta.view?.width ?? 960, height = meta.view?.height ?? 720;
    await page.setViewportSize({ width: width * (meta.view ? 2 : 1), height });
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    if (page.url() !== url) {
      await page.goto(url, { waitUntil: 'load' });
      await page.waitForFunction(() => !!globalThis.__workbench, null, { timeout: 90000 }).catch(error => {
        throw new Error(`the workbench page did not start: ${error.message}\n${errors.join('\n')}`);
      });
    }
    const refreshed = await page.evaluate(frames => globalThis.__workbench.refresh(frames), Number(opt('--settle', '40')));
    const out = path.resolve(opt('--out', path.join(workRoot, 'workbench', name, 'pair.jpg')));
    fs.mkdirSync(path.dirname(out), { recursive: true });
    await page.screenshot({ path: out, type: out.endsWith('.png') ? 'png' : 'jpeg', ...(out.endsWith('.png') ? {} : { quality: 90 }) });
    console.log(JSON.stringify({ out, revision: refreshed.revision, changed: refreshed.changed, draws: refreshed.draws, refreshMs: refreshed.ms, totalMs: Date.now() - started, errors: errors.slice(0, 5) }));
  } finally {
    await browser.close();
    if (!argv.includes('--keep')) release();
  }
}

if (command === 'serve') { await serve(); console.log(`${base}/building-workbench.html?name=NAME`); }
else if (command === 'shot') await shot();
else if (command === 'release') release();
else { console.error('usage: building-workbench.mjs serve | shot NAME [--out FILE] [--keep] | release'); process.exitCode = 2; }
