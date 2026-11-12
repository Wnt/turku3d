#!/usr/bin/env node
// The campaign-owned preview browser: one long-lived Chrome for Testing with its own profile and CDP port, kept
// alive while work is active and closed by its watchdog after a bounded idle time. Experiments and captures
// (scripts/dev/scene-capture.mjs) connect to it instead of launching a browser per capture.
//
//   scripts/dev/preview-session.mjs start [--url URL] [--headed] [--idle-minutes 45]
//   scripts/dev/preview-session.mjs status | touch | stop
//
// Source ../sandbox.env first: the profile lives under TURKU3D_BROWSER_DIR, never in a personal Chrome profile. The CDP
// port is assigned by the OS (every sandbox port already has a test owner) and recorded in session.json.
// Performance measurements still take an exclusive host lease (scripts/dev/host_slots.py); this session is for
// looking, comparing and capturing.
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromeArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { port, workRoot } from '../../tools/dev/environment.mjs';

const args = process.argv.slice(2), command = args[0] ?? 'status';
const opt = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const root = path.join(workRoot, 'preview');
const stateFile = path.join(root, 'session.json'), touchFile = path.join(root, 'touched');

export function previewState() {
  try { return JSON.parse(fs.readFileSync(stateFile, 'utf8')); } catch { return null; }
}
const alive = pid => { try { process.kill(pid, 0); return true; } catch { return false; } };
/** Marks the session as in use; the watchdog's idle clock restarts. */
export function touchPreview() {
  fs.mkdirSync(root, { recursive: true });
  fs.writeFileSync(touchFile, new Date().toISOString() + '\n');
}
function stopBrowser(state, reason) {
  if (state?.pid && alive(state.pid)) {
    try { process.kill(-state.pid, 'SIGTERM'); } catch { try { process.kill(state.pid, 'SIGTERM'); } catch { /* gone */ } }
  }
  fs.rmSync(stateFile, { force: true });
  console.log(`preview session stopped (${reason})`);
}

/** Chrome writes the port it bound for --remote-debugging-port=0 into the profile's DevToolsActivePort. */
async function cdpReady(profile, since, deadline) {
  const file = path.join(profile, 'DevToolsActivePort');
  while (Date.now() < deadline) {
    try {
      if (fs.statSync(file).mtimeMs >= since) {
        const cdp = `http://127.0.0.1:${Number(fs.readFileSync(file, 'utf8').split('\n')[0])}`;
        if ((await fetch(`${cdp}/json/version`, { signal: AbortSignal.timeout(1000) })).ok) return cdp;
      }
    } catch { /* starting */ }
    await new Promise(r => setTimeout(r, 200));
  }
  return null;
}

async function start() {
  if (!process.env.TURKU3D_PORT_BASE) throw new Error('Source ../sandbox.env first: the preview session needs its own TURKU3D_PORT_BASE');
  const running = previewState();
  if (running && alive(running.pid)) { touchPreview(); console.log(JSON.stringify({ ...running, reused: true })); return; }
  const profile = path.join(process.env.TURKU3D_BROWSER_DIR ?? path.join(workRoot, 'browser'), 'preview');
  const url = opt('--url', `http://localhost:${port()}/?solo=1`);
  const idleMinutes = Number(opt('--idle-minutes', '45'));
  if (!(idleMinutes > 0 && idleMinutes <= 120)) throw new Error('--idle-minutes must be 1..120');
  fs.mkdirSync(profile, { recursive: true }); fs.mkdirSync(root, { recursive: true });
  const launch = chromeArgs([...(args.includes('--headed') ? [] : ['--headless=new']), '--enable-unsafe-webgpu', '--use-angle=metal', '--mute-audio',
    '--ignore-gpu-blocklist', '--no-first-run', '--no-default-browser-check', '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows', '--window-size=1440,960',
    `--user-data-dir=${profile}`, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1', url]);
  const log = fs.openSync(path.join(root, 'browser.log'), 'a'), launched = Date.now() - 1000;
  const child = spawn(chromeExecutable(), launch, { detached: true, stdio: ['ignore', log, log] });
  child.unref();
  const cdp = await cdpReady(profile, launched, Date.now() + 20000);
  if (!cdp) { stopBrowser({ pid: child.pid }, 'CDP did not come up'); process.exitCode = 1; return; }
  const state = { pid: child.pid, cdp, profile, url, idleMinutes, started: new Date().toISOString() };
  fs.writeFileSync(stateFile, JSON.stringify(state, null, 1) + '\n');
  touchPreview();
  spawn(process.execPath, [fileURLToPath(import.meta.url), 'watch'], { detached: true, stdio: 'ignore', env: process.env }).unref();
  console.log(JSON.stringify(state));
}

/** Watchdog: closes the browser after idleMinutes without a touch, and forgets a browser that exited. */
async function watch() {
  for (;;) {
    await new Promise(r => setTimeout(r, 30000));
    const state = previewState();
    if (!state) return;
    if (!alive(state.pid)) { fs.rmSync(stateFile, { force: true }); return; }
    const touched = fs.existsSync(touchFile) ? fs.statSync(touchFile).mtimeMs : Date.parse(state.started);
    if (Date.now() - touched > state.idleMinutes * 60000) { stopBrowser(state, `idle ${state.idleMinutes} min`); return; }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (command === 'start') await start();
  else if (command === 'watch') await watch();
  else if (command === 'touch') touchPreview();
  else if (command === 'stop') stopBrowser(previewState(), 'requested');
  else if (command === 'status') {
    const state = previewState();
    const touched = fs.existsSync(touchFile) ? fs.statSync(touchFile).mtime.toISOString() : null;
    console.log(JSON.stringify(state && alive(state.pid) ? { ...state, touched } : { running: false }));
  } else { console.error('usage: preview-session.mjs start|status|touch|stop'); process.exitCode = 2; }
}
