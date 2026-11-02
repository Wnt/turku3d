#!/usr/bin/env node
// Capture the loop poses through one existing CDP page. Images and references stay outside git.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execFileSync, spawnSync } from 'node:child_process';
const args = process.argv.slice(2);
const opt = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const root = path.join(os.homedir(), 'turku3d');
const cdp = opt('--cdp', 'http://127.0.0.1:5223');
const base = opt('--base', 'http://localhost:5220/');
const out = opt('--out', path.join(root, 'loops/integrator/iter-0'));
const cameras = JSON.parse(fs.readFileSync(opt('--cameras', path.join(root, 'data/research/turku3d-compare/cameras.json')))).cameras;
const refs = JSON.parse(fs.readFileSync(path.join(root, 'loops/refs.json')));
const only = opt('--only', Object.keys(refs).join(',')).split(',');
const times = opt('--at', '13:00,19:30,22:00').split(',');
const timeout = Number(opt('--timeout', '300')) * 1000;
const pages = await fetch(`${cdp}/json/list`, { signal: AbortSignal.timeout(15000) }).then(r => r.json());
const target = pages.find(p => p.type === 'page' && p.url.startsWith(base)) ?? pages.find(p => p.type === 'page' && p.url === 'about:blank');
if (!target) throw new Error('No integration or blank page in the specified CDP browser');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  const timer = setTimeout(() => { socket.close(); reject(new Error('CDP connection timed out')); }, 15000);
  socket.onopen = () => { clearTimeout(timer); resolve(); };
  socket.onerror = error => { clearTimeout(timer); reject(error); };
});
console.log(`Connected to integration page ${target.id}`);
let seq = 0;
const pending = new Map(), assets = new Map(), failures = [];
let lastAsset = 0;
const asset = url => /\.(glb|bin|col|ktx2|png|jpg)(\?|$)/.test(url);
socket.onmessage = ({ data }) => {
  const msg = JSON.parse(data);
  if (msg.id) {
    const p = pending.get(msg.id);
    if (!p) return;
    pending.delete(msg.id); clearTimeout(p.timer);
    msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result);
  }
  if (msg.method === 'Network.requestWillBeSent' && asset(msg.params.request.url)) {
    assets.set(msg.params.requestId, msg.params.request.url); lastAsset = Date.now();
  }
  if (msg.method === 'Network.responseReceived' && assets.has(msg.params.requestId) && msg.params.response.status >= 400)
    failures.push(`${msg.params.response.status} ${assets.get(msg.params.requestId)}`);
  if (['Network.loadingFinished', 'Network.loadingFailed'].includes(msg.method) && assets.has(msg.params.requestId)) {
    if (msg.method === 'Network.loadingFailed') failures.push(`${msg.params.errorText} ${assets.get(msg.params.requestId)}`);
    assets.delete(msg.params.requestId); lastAsset = Date.now();
  }
};
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, Math.min(timeout, 60000));
  pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params }));
});
const evaluate = async expression => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
};
const delay = ms => new Promise(r => setTimeout(r, ms));
fs.mkdirSync(out, { recursive: true });
const index = path.join(out, 'shots.json');
const previous = fs.existsSync(index) ? JSON.parse(fs.readFileSync(index)) : { shots: [] };
const shots = new Map(previous.shots.map(s => [s.file, s]));
const build = execFileSync('git', ['rev-parse', '--short=8', 'HEAD'], { encoding: 'utf8' }).trim();
const valid = s => s?.capture.qa_ready && !s.capture.qa_error && !s.capture.incomplete_tiles.length && !s.capture.failed_assets_final.length;
const save = () => {
  fs.writeFileSync(`${index}.tmp`, JSON.stringify({ generated: new Date().toISOString(), viewport: '1920x1200',
    device_scale_factor: 1, shots: [...shots.values()] }, null, 1) + '\n');
  fs.renameSync(`${index}.tmp`, index);
};
try {
  await send('Page.setWebLifecycleState', { state: 'active' });
  await send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1200, deviceScaleFactor: 1, mobile: false });
  const ordered = [...cameras.entries()].sort((a, b) =>
    Number(b[1].id.startsWith('pano-')) - Number(a[1].id.startsWith('pano-')));
  for (const [n, cam] of ordered) {
    if (!only.includes(cam.id)) continue;
    for (const at of times) {
      const file = `${String(n + 1).padStart(2, '0')}-${cam.id}-${at.replace(':', '')}.png`;
      if (valid(shots.get(file))) continue;
      const guard = path.join(root, 'tools/chrome-guard/guard.py');
      if (fs.existsSync(guard)) {
        const touched = spawnSync('python3', [guard, 'touch', opt('--guard-owner', 'integrator')], { encoding: 'utf8' });
        if (touched.status === 2) console.log(touched.stdout.trim());
      }
      failures.length = 0;
      const started = Date.now(), [E, N, H, yaw, pitch, fov] = cam.cam.split(',').map(Number);
      console.log(`Starting ${file}`);
      const [h, m] = at.split(':').map(Number);
      const pose = { E, N, H, yaw, pitch, fov };
      const url = `${base}?cam=${cam.cam}&time=${at}&quality=high&hud=0&traffic=0&walkers=0`;
      if (await evaluate('!!window.__qaMove')) {
        await evaluate(`window.__render.beforeFrame = undefined; window.__game.clock.simSeconds = 0;
          window.__render.setHour(${h + m / 60}); window.__qaMove(${JSON.stringify(pose)});`);
      } else {
        await send('Page.navigate', { url });
      }
      const ci = Math.floor((E - 23459500) / 250), cj = Math.floor((6704500 - N) / 250);
      const wanted = Array.from({ length: 9 }, (_, k) => `t0_${ci - 1 + k % 3}_${cj - 1 + Math.floor(k / 3)}`);
      let state;
      let throttled = false;
      let settleRetries = 0;
      while (Date.now() - started < timeout) {
        if (!throttled) throttled = await evaluate(`(() => {
          if (!window.__render) return false;
          let last = 0;
          window.__render.beforeFrame = () => {
            if (window.__qaReady) return true;
            const now = performance.now();
            if (now - last < 100) return false;
            last = now; return true;
          };
          return true;
        })()`);
        state = await evaluate(`(() => {
          const g = window.__game, env = window.__render?.environment;
          const tiles = ${JSON.stringify(wanted)}.map(id => { const e = g?.lights?.source?.entries.get(id);
            return {id, pass:e?.pass, full:e?.state === 'draft' && e?.pass === 'full' && !e?.preview && !!e?.model}; });
          return {ready:!!window.__qaReady,error:window.__qaError,tiles,qa:g?.qa, sun:env?.sunElevation,
            azimuth:env?.sunAzimuth, clock:g?.dayClock?.snapshot(), hour:g?.dayClock?.hour, pose:window.__qaPose,
            info:window.__qaInfo,night:g?.dayClock?.night};
        })()`);
        if (state.pose && state.hour !== undefined && (Math.abs(state.hour - (h + m / 60)) > 0.01 ||
          ['E', 'N', 'H', 'yaw', 'pitch', 'fov'].some(key => Math.abs(state.pose[key] - pose[key]) > 0.001))) {
          // A browser/HMR reload can restore the startup URL. Reapply the requested view before scoring it.
          await evaluate(`window.__render.setHour(${h + m / 60}); window.__qaMove(${JSON.stringify(pose)});`);
          throttled = false;
          continue;
        }
        if (state.error?.startsWith('timeout: tiles did not settle')) {
          // Keep already downloaded/compiled tiles while the capture's overall deadline permits a retry.
          // Full-pass and asset checks still apply; an incomplete view never becomes a valid baseline.
          console.log(`Retrying settle for ${file} with resident tiles`);
          settleRetries++;
          await evaluate(`window.__qaMove(${JSON.stringify(pose)})`);
          continue;
        }
        if (state.error) throw new Error(`${cam.id}@${at}: ${state.error}`);
        if (state.ready && state.tiles.every(t => t.full) && !assets.size && Date.now() - lastAsset > 2000) break;
        await delay(1000);
      }
      await delay(2000);
      await evaluate(`document.querySelectorAll('body > *:not(canvas)').forEach(e => e.style.setProperty('display','none','important'));`);
      const screenshot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
      await evaluate('window.__render.beforeFrame = () => false');
      fs.writeFileSync(path.join(out, file), Buffer.from(screenshot.data, 'base64'));
      const row = { file, time: at, type: cam.type, place: cam.place, camera_height_m: cam.camera_height_m,
        notes: cam.notes, url, build: `local/loop-integration@${build}`, capture: {
          wall_s: (Date.now() - started) / 1000, qa_ready: state.ready, qa_error: state.error ?? null,
          incomplete_tiles: state.tiles.filter(t => !t.full).map(t => t.id), failed_assets_final: [...new Set(failures)],
          tiles: state.tiles, sun_elevation_deg: state.sun, quality: state.info?.quality, backend: state.info?.backend,
          sun_azimuth_deg: state.azimuth, clock: state.clock,
          settle_retries: settleRetries,
          effective_hour: state.hour, effective_pose: state.pose,
          vis_tris: state.qa?.visTris, load_ms: state.qa?.loadMs, night_level: state.night } };
      shots.set(file, row); save();
      console.log(JSON.stringify({file,valid:valid(row),seconds:row.capture.wall_s}));
      if (!valid(row)) throw new Error(`Incomplete capture: ${file}; resume after resolving missing tiles/assets`);
    }
  }
} finally {
  // Freeze even when tile settling, screenshotting, or evaluation fails.
  await send('Page.setWebLifecycleState', { state: 'frozen' }).catch(error => console.error(error.message));
  socket.close();
}
