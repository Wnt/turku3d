#!/usr/bin/env node
// Capture traffic in one existing, guarded QA session. Freeze it again on success and on failure.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
const args = process.argv.slice(2), opt = (key, fallback) => args.includes(key) ? args[args.indexOf(key) + 1] : fallback;
const out = path.resolve(opt('--out', `${process.env.HOME}/turku3d/loops/traffic/capture`));
const mode = opt('--mode', 'flow'), start = Number(opt('--start', '60'));
const offsets = opt('--samples', '0,3,6,9').split(',').map(Number);
const cdp = opt('--cdp', 'http://127.0.0.1:5917');
const pages = await fetch(`${cdp}/json/list`).then(r => r.json());
const target = pages.find(t => t.type === 'page' && t.url.startsWith(opt('--base', 'http://127.0.0.1:5910')));
if (!target) throw new Error('No traffic page in the specified CDP browser');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let id = 0;
const pending = new Map();
socket.onmessage = ({ data }) => {
  const msg = JSON.parse(data), p = pending.get(msg.id);
  if (!p) return;
  pending.delete(msg.id); clearTimeout(p.timer);
  msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result);
};
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const key = ++id, timer = setTimeout(() => reject(new Error(`CDP timeout ${method}`)), 60000);
  pending.set(key, { resolve, reject, timer }); socket.send(JSON.stringify({ id: key, method, params }));
});
const evaluate = async expression => {
  const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
};
const shots = [];
fs.mkdirSync(out, { recursive: true });
try {
  const guard = `${process.env.HOME}/turku3d/tools/chrome-guard/guard.py`;
  const touch = spawnSync('python3', [guard, 'touch', 'traffic'], { encoding: 'utf8' });
  if (touch.status === 2) console.log(touch.stdout.trim());
  await send('Page.setWebLifecycleState', { state: 'active' });
  await send('Emulation.setFocusEmulationEnabled', { enabled: true });
  const deadline = Date.now() + 120000;
  while (!await evaluate('!!window.__game?.lights?.source && !!window.__qaMove')) {
    if (Date.now() > deadline) throw new Error('QA world did not initialize');
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  if (!await evaluate('!!window.__game?.trafficPreview')) {
    await evaluate('(async()=>{const m=await import("/src/traffic/preview.ts");return m.startTrafficPreview()})()');
  }
  if (args.includes('--patch')) {
    await evaluate(`(async()=>{const m=await import('/src/traffic/flow.ts?traffic=${Date.now()}');window.__game.trafficPreview.replace(m.TrafficFlow)})()`);
  }
  if (args.includes('--pose')) {
    const [E, N, H, yaw, pitch, fov] = opt('--pose').split(',').map(Number);
    await evaluate(`window.__qaMove(${JSON.stringify({ E, N, H, yaw, pitch, fov })})`);
  }
  const settle = Date.now() + 60000;
  while (!await evaluate('!!window.__qaReady')) {
    const error = await evaluate('window.__qaError ?? null');
    if (error) throw new Error(`QA error: ${error}`);
    if (Date.now() > settle) throw new Error('Camera did not settle');
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  await evaluate(`window.__game.trafficPreview.play(false);window.__game.trafficPreview.setMode(${JSON.stringify(mode)});window.__game.trafficPreview.signals.root.visible=${mode === "flow"};`);
  for (const offset of offsets) {
    const time = start + offset;
    const snapshot = await evaluate(`window.__game.trafficPreview.seek(${time}); window.__game.trafficPreview.snapshot()`);
    await evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
    const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    const file = `${mode}-${time.toFixed(1)}.png`;
    fs.writeFileSync(path.join(out, file), Buffer.from(shot.data, 'base64'));
    shots.push({ file, snapshot }); console.log(`${file}: ${snapshot.drawn} cars`);
  }
  if (args.includes('--clip')) {
    const duration = Number(opt('--clip', '8'));
    const clip = await evaluate(`(async()=>{
      const preview=window.__game.trafficPreview, canvas=document.querySelector('canvas');
      const stream=canvas.captureStream(30), chunks=[];
      const recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp9',videoBitsPerSecond:6000000});
      recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
      const done=new Promise(resolve=>recorder.onstop=resolve);
      const frames=window.__game.clock.frames, started=performance.now();
      preview.play(true);recorder.start();await new Promise(r=>setTimeout(r,${duration * 1000}));recorder.stop();await done;preview.play(false);
      const blob=new Blob(chunks,{type:recorder.mimeType});
      const data=await new Promise(resolve=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.readAsDataURL(blob)});
      stream.getTracks().forEach(t=>t.stop());
      return {data,seconds:(performance.now()-started)/1000,frames:window.__game.clock.frames-frames,snapshot:preview.snapshot()};
    })()`);
    fs.writeFileSync(path.join(out, `${mode}.webm`), Buffer.from(clip.data, 'base64'));
    const { data: _data, ...metrics } = clip;
    fs.writeFileSync(path.join(out, 'clip.json'), JSON.stringify(metrics, null, 2));
    console.log(`${mode}.webm: ${(clip.frames / clip.seconds).toFixed(1)} FPS`);
  }
  fs.writeFileSync(path.join(out, 'shots.json'), JSON.stringify({ mode, shots }, null, 2));
} finally {
  await send('Page.setWebLifecycleState', { state: 'frozen' }).catch(() => {});
  socket.close();
}
