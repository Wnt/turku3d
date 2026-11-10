// Headless check of the built soundboard through the Chrome DevTools Protocol (no dependencies; Node 22+).
//   BROWSER_CDP=http://127.0.0.1:PORT node check.mjs BOARD_DIR [SCREENSHOT.png] [--url URL]
// Serves BOARD_DIR on an ephemeral localhost port (or opens --url), loads the page, decodes every audio file, starts
// the live tyre synthesiser with a real click, ticks a pick, and reports page errors. Exit 1 on any failure.
import http from 'node:http';
import { readFileSync, writeFileSync, existsSync, statSync } from 'node:fs';
import path from 'node:path';

const args = process.argv.slice(2);
const urlAt = args.indexOf('--url');
const remote = urlAt >= 0 ? args.splice(urlAt, 2)[1] : null;
const [dir, shot] = args;
const cdp = process.env.BROWSER_CDP;
if (!cdp || !dir) throw new Error('usage: BROWSER_CDP=... node check.mjs BOARD_DIR [SCREENSHOT.png] [--url URL]');
const TYPES = { '.html': 'text/html; charset=utf-8', '.ogg': 'audio/ogg', '.png': 'image/png', '.js': 'text/javascript', '.mp3': 'audio/mpeg' };
const server = http.createServer((req, res) => {
  const file = path.join(dir, decodeURIComponent(new URL(req.url, 'http://x').pathname).replace(/\/$/, '/index.html'));
  if (!file.startsWith(path.resolve(dir)) || !existsSync(file) || !statSync(file).isFile()) { res.writeHead(404).end(); return; }
  res.writeHead(200, { 'content-type': TYPES[path.extname(file)] ?? 'application/octet-stream' }).end(readFileSync(file));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const pageUrl = remote ?? `http://127.0.0.1:${server.address().port}/`;

const target = await (await fetch(`${cdp}/json/new?about:blank`, { method: 'PUT' })).json();
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let next = 0;
const waiting = new Map(), problems = [], external = [];
socket.onmessage = event => {
  const message = JSON.parse(event.data);
  if (message.id && waiting.has(message.id)) { const { resolve, reject } = waiting.get(message.id); waiting.delete(message.id); message.error ? reject(new Error(message.error.message)) : resolve(message.result); return; }
  if (message.method === 'Runtime.exceptionThrown') problems.push('exception: ' + (message.params.exceptionDetails.exception?.description ?? message.params.exceptionDetails.text));
  if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') problems.push('console.error: ' + message.params.args.map(a => a.value ?? a.description).join(' '));
  if (message.method === 'Network.requestWillBeSent') { const u = message.params.request.url; if (!u.startsWith(new URL(pageUrl).origin) && !u.startsWith('data:') && !u.startsWith('blob:') && u !== 'about:blank') external.push(u); }
  if (message.method === 'Network.loadingFailed' && !message.params.canceled) problems.push('request failed: ' + message.params.errorText);
};
const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++next; waiting.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
const evaluate = async expression => {
  const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, userGesture: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
  return result.result.value;
};
await send('Runtime.enable'); await send('Network.enable'); await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1180, height: 1500, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: pageUrl });
await new Promise(resolve => setTimeout(resolve, 1500));

const summary = await evaluate(`(async () => {
  const items = Object.values(DATA.index);
  const context = new OfflineAudioContext(2, 4800, 48000);
  const decoded = [];
  for (const item of items) {
    try {
      const response = await fetch(item.file);
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const buffer = await context.decodeAudioData(await response.arrayBuffer());
      if (item.loop && item.loop[1] > buffer.duration + 0.001) throw new Error('loop end beyond the file');
      if (Math.abs(buffer.duration - (item.loop && item.loop[1] > 0 ? item.loop[1] + item.loop[0] : item.seconds)) > 0.05) throw new Error('duration ' + buffer.duration + ' vs ' + item.seconds);
      decoded.push(item.id);
    } catch (error) { console.error(item.id + ': ' + error.message); }
  }
  return { items: items.length, decoded: decoded.length, cards: document.querySelectorAll('.card').length, checkboxes: document.querySelectorAll('.card input[type=checkbox]').length,
    images: [...document.images].length };
})()`);
console.log('page', JSON.stringify(summary));
if (summary.decoded !== summary.items) problems.push(`decoded ${summary.decoded} of ${summary.items}`);

// Picks: tick the first two candidates, copy, read back the stored list.
const picks = await evaluate(`(() => { localStorage.clear(); const boxes = [...document.querySelectorAll('.card input[type=checkbox]')].slice(0, 2); for (const b of boxes) b.click();
  return { stored: JSON.parse(localStorage.getItem('sounds-fable-picks-v1')), text: document.getElementById('pickText').value, count: document.getElementById('pickCount').textContent }; })()`);
console.log('picks', JSON.stringify(picks));
if (picks.stored.length !== 2 || picks.text.split('\n').length !== 2) problems.push('picks did not register');
await evaluate(`(() => { for (const b of [...document.querySelectorAll('.card input[type=checkbox]')].slice(0, 2)) b.click(); })()`);

// Play one loop and the live tyre synthesiser; measure the output through an analyser tap.
const live = await evaluate(`(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const out = {};
  const card = document.querySelector('.card .play'); card.click(); await wait(900);
  out.contextState = context.state; out.playing = !!document.querySelector('.card.playing'); card.click();
  const root = document.getElementById('tyre-live');
  if (root) {
    root.querySelector('[data-a=power]').click(); await wait(1200);
    const slider = root.querySelector('[data-k=slide]'); slider.value = 6; slider.dispatchEvent(new Event('input'));
    await wait(600);
    out.tyreOn = root.querySelector('[data-a=power]').classList.contains('on');
    root.querySelector('[data-a=power]').click();
  }
  for (const name of Object.keys(window.__liveChecks ?? {})) out[name] = await window.__liveChecks[name]();
  return out;
})()`);
console.log('live', JSON.stringify(live));
if (!live.playing) problems.push('first card did not start playing');
if (live.tyreOn === false) problems.push('tyre synthesiser did not start');
for (const name of ['tyrePeak', 'musicPeak', 'gullPeak']) if (name in live && !(live[name] > 0.01)) problems.push(`${name} is silent (${live[name]})`);
if (external.length) problems.push('external requests: ' + [...new Set(external)].join(', '));
if (shot) {
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  writeFileSync(shot, Buffer.from(data, 'base64'));
}
await send('Page.close').catch(() => {});
socket.close(); server.close();
if (problems.length) { console.log('PROBLEMS\n  ' + problems.join('\n  ')); process.exit(1); }
console.log('OK: no page errors, no external requests');
