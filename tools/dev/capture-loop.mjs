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
const loadedPages=new Set();
const pending = new Map(), assets = new Map(), failures = [];
const consoleErrors=[];
let lastAsset = 0;
const asset = url => /\.(glb|bin|col|ktx2|png|jpg)(\?|$)/.test(url);
socket.onmessage = ({ data }) => {
  const msg = JSON.parse(data);
  if(msg.method==='Page.lifecycleEvent'&&msg.params.name==='load')loadedPages.add(msg.params.loaderId);
  if(msg.method==='Runtime.exceptionThrown')consoleErrors.push(msg.params.exceptionDetails);
  if(msg.method==='Runtime.consoleAPICalled'&&msg.params.type==='error')consoleErrors.push(msg.params.args.map(a=>a.value??a.description));
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
  const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, Math.min(timeout, method === 'Runtime.evaluate' ? 180000 : 60000));
  pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params }));
});
const evaluate = async expression => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
};
const delay = ms => new Promise(r => setTimeout(r, ms));
fs.mkdirSync(out, { recursive: true });
if(args.includes('--fleet-inspect')){
  console.log(JSON.stringify(await evaluate(`window.fleetWorkshop.scene.children.map(o=>({name:o.name,type:o.type,count:o.count,visible:o.visible,pos:o.position.toArray()}))`)));
  socket.close();process.exit(0);
}
// Vehicle motion fixtures use the production physics and visual classes, with identical
// cached physical poses for the flattened baseline and articulated candidate.
if (args.includes('--fleet-motion')) {
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', {width:1280,height:720,deviceScaleFactor:1,mobile:false});
  await send('Page.enable');
  await send('Page.setLifecycleEventsEnabled',{enabled:true});
  await evaluate('window.fleetWorkshop=undefined');
  for(const url of ['about:blank',new URL('fleet-gallery.html',base).href]){
    const navigation=await send('Page.navigate',{url});
    for(let i=0;i<600&&!loadedPages.has(navigation.loaderId);i++)await delay(50);
    if(!loadedPages.has(navigation.loaderId))throw Error(`Workshop did not load: ${url}`);
  }
  for(let i=0;i<120&&!await evaluate('!!window.fleetWorkshop');i++)await delay(250);
  consoleErrors.length=0;
  const setup=await evaluate(`window.fleetWorkshop.motionSetup(${args.includes('--fleet-batch')||args.includes('--ground-motion')})`);
  if(setup.revision!==4)throw Error('Stale fleet workshop: wait for Vite HMR before capture');
  if(args.includes('--fleet-bench')){
    const bench=await evaluate('window.fleetWorkshop.batchBench()');
    fs.writeFileSync(path.join(out,'batch-bench.json'),JSON.stringify(bench,null,1));
    console.log(JSON.stringify(bench));socket.close();process.exit(0);
  }
  const frames=[];
  for(const articulated of [false,true]){
    if(args.includes('--ground-motion'))await evaluate('window.fleetWorkshop.motionSetup(true)');
    for(const index of [8,26,44,62,80,98]){
    const state=await evaluate(`window.fleetWorkshop.${args.includes('--ground-motion')?'groundFrame':'motionFrame'}(${index},${articulated})`);
    await delay(100);
    const shot=await send('Page.captureScreenshot',{format:'jpeg',quality:92,captureBeyondViewport:false});
    const file=`${articulated?'after':'before'}-${index}.jpg`;
    fs.writeFileSync(path.join(out,file),Buffer.from(shot.data,'base64'));frames.push({file,...state});
  }
  }
  fs.writeFileSync(path.join(out,'motion.json'),JSON.stringify({setup,frames,consoleErrors},null,1));
  console.log(JSON.stringify({setup,frames:frames.length,out}));
  socket.close();process.exit(0);
}
const index = path.join(out, 'shots.json');
const previous = fs.existsSync(index) ? JSON.parse(fs.readFileSync(index)) : { shots: [] };
const shots = new Map(previous.shots.map(s => [s.file, s]));
const build = execFileSync('git', ['rev-parse', '--short=8', 'HEAD'], { encoding: 'utf8' }).trim();
const scenerySettled = s => !s || (!s.cataloguePending && !s.pending && !s.errors && s.loaded === s.total);
const valid = s => s?.capture.vegetation_ready !== false && s?.capture.qa_ready && !s.capture.qa_error && !s.capture.incomplete_tiles.length
  && !s.capture.failed_assets_final.length && scenerySettled(s.capture.scenery);
const save = () => {
  fs.writeFileSync(`${index}.tmp`, JSON.stringify({ generated: new Date().toISOString(), viewport: '1280x720',
    device_scale_factor: 1, shots: [...shots.values()] }, null, 1) + '\n');
  fs.renameSync(`${index}.tmp`, index);
};
// --stream-flicker deliberately measures residency before full detail is published.
// One render at a time makes each PNG correspond to its recorded product/mesh state.
async function captureFlicker(file, pose) {
  const count = Number(opt('--flicker', '0'));
  if (!count) return;
  if (!Number.isInteger(count) || count < 4 || count > 64) throw new Error('--flicker must be 4..64');
  const dir = path.join(out, file.replace(/\.jpg$/, '-flicker'));
  fs.mkdirSync(dir, { recursive: true });
  const step = Number(opt('--micro-step', '.005'));
  if (!Number.isFinite(step) || step <= 0 || step > .05) throw new Error('--micro-step must be >0 and <=.05 metres');
  const frames = [];
  const original = await evaluate(`({position:window.__render.camera.position.toArray(),sim:window.__game.clock.simSeconds})`);
  try {
    for (const group of ['still', 'micro-left', 'micro-right']) {
      const dx = group === 'still' ? 0 : group === 'micro-left' ? -step : step;
      // Move on camera right in world space, without restarting tile settling.
      await evaluate(`(() => {const c=window.__render.camera;
        c.position.fromArray(${JSON.stringify(original.position)});
        c.position.addScaledVector(new c.position.constructor(1,0,0).applyQuaternion(c.quaternion),${dx});
        c.updateMatrixWorld();})()`);
      for (let frame = -8; frame < count; frame++) {
        await evaluate(`new Promise((resolve,reject) => {
          const r=window.__render, g=window.__game, previous=r.afterFrame;
          const timer=setTimeout(()=>{r.afterFrame=previous;r.beforeFrame=()=>false;reject(new Error('flicker frame timeout'));},15000);
          r.afterFrame=()=>{previous?.();r.beforeFrame=()=>false;r.afterFrame=previous;clearTimeout(timer);resolve(true);};
          g.clock.simSeconds=${original.sim}; r.beforeFrame=()=>true;
        })`);
        if (frame < 0) continue;
        const state = await evaluate(`(async () => ({frame:window.__game.clock.frames,
          camera:window.__render.camera.position.toArray(),pose:window.__qaPose,hour:window.__game.dayClock.hour,
          meshes:await window.__dev.geometryState(),
          products:[...window.__game.lights.source.entries].map(([id,e])=>({id,state:e.state,pass:e.pass,hash:e.visualHash,preview:e.preview??false}))}))()`);
        const png = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
        const name = `${group}-${String(frame).padStart(2, '0')}.png`;
        fs.writeFileSync(path.join(dir, name), Buffer.from(png.data, 'base64'));
        frames.push({file:name, group, dx_m:dx, ...state});
      }
    }
    if (failures.length || (assets.size && !args.includes('--stream-flicker'))) throw new Error('Assets changed/failed during flicker capture; retake after settling');
    fs.writeFileSync(path.join(dir, 'frames.json'), JSON.stringify({pose, viewport:[1280,720], micro_step_m:step,
      warmup_frames:8, frames, failed_assets:[...failures], classification:'heuristic; see flicker analyzer'}, null, 1));
    console.log(`Flicker sequence: ${frames.length} PNGs at ${dir}`);
  } finally {
    await evaluate(`window.__render.camera.position.fromArray(${JSON.stringify(original.position)});
      window.__render.camera.updateMatrixWorld();window.__render.beforeFrame=()=>false;`);
  }
}
if (args.includes('--stream-flicker')) {
  // Hold source GLBs, while collision previews and the cached scenery derivatives arrive normally.
  // This makes the cold-cache arrival window repeatable without changing any tile bytes.
  const held = [];
  const prior = socket.onmessage;
  socket.onmessage = event => {
    prior(event);
    const msg = JSON.parse(event.data);
    if (msg.method === 'Fetch.requestPaused') held.push(msg.params.requestId);
  };
  try {
    await send('Page.setWebLifecycleState', { state: 'active' });
    await send('Emulation.setFocusEmulationEnabled', { enabled: true });
    await send('Network.enable');
    await send('Network.setCacheDisabled', { cacheDisabled: true });
    await send('Network.clearBrowserCache');
    await send('Emulation.setDeviceMetricsOverride', { width:1280,height:720,deviceScaleFactor:1,mobile:false });
    await send('Fetch.enable', { patterns:[{urlPattern:'*/coarse/*/vis.*.glb',requestStage:'Request'}] });
    const cam = cameras.find(c => only.includes(c.id));
    const at = times[0];
    await send('Page.navigate', { url:`${base}?cam=${cam.cam}&time=${at}&quality=${opt('--quality','medium')}&aa=${opt('--aa','taa')}&hud=0&traffic=0&walkers=0` });
    const started = Date.now();
    let state;
    do {
      state = await evaluate(`(() => {const g=window.__game, c=g?.lights?.source; return {
        ready:!!window.__qaMove && !!g?.vegetation?.library && !g.vegetation.pending.size,
        previews:[...(c?.entries??[])].filter(([,e])=>e.model&&e.preview).map(([id])=>id),
        scenery:g?.scenery?.progress};})()`);
      if (state.ready && state.previews.length && state.scenery?.loaded >= Number(opt('--stream-loaded','40'))) break;
      await delay(500);
    } while (Date.now()-started < timeout);
    if (!state.ready || !state.previews.length) throw new Error(`No streaming window: ${JSON.stringify(state)}`);
    if (opt('--setup-file','')) await evaluate(fs.readFileSync(opt('--setup-file'),'utf8'));
    await evaluate(`window.__render.beforeFrame=()=>false; document.querySelectorAll('body > *:not(canvas)').forEach(e=>e.style.setProperty('display','none','important'));`);
    const [E,N,H,yaw,pitch,fov] = cam.cam.split(',').map(Number);
    fs.writeFileSync(path.join(out,'stream.json'),JSON.stringify({state,held:held.length,build,at,source:'cold HTTP/cache, full source GLBs held by CDP; collision and scenery unchanged'},null,1));
    await captureFlicker(`${cam.id}.jpg`,{E,N,H,yaw,pitch,fov});
    const jpeg = await send('Page.captureScreenshot',{format:'jpeg',quality:92,captureBeyondViewport:false});
    fs.writeFileSync(path.join(out,`${cam.id}.jpg`),Buffer.from(jpeg.data,'base64'));
  } finally {
    await send('Fetch.disable');
    await send('Network.setCacheDisabled',{cacheDisabled:false});
    await evaluate('window.__render.beforeFrame=undefined');
    socket.close();
  }
  process.exit(0);
}
// Play-mode evidence uses this same owned CDP session and screenshot transport.
if (opt('--navigate', '')) {
  await send('Page.setWebLifecycleState', {state:'active'});
  await send('Emulation.setFocusEmulationEnabled',{enabled:true});
  await send('Page.bringToFront');
  await send('Emulation.setDeviceMetricsOverride', {width:1280,height:720,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate', {url:opt('--navigate')});
  await delay(1500);
  const started=Date.now();
  while (!await evaluate('!!window.__game?.ready') && Date.now()-started<timeout) await delay(1000);
}
// Interleaved measurements tolerate other loops sharing the Mac; record load and residency explicitly.
if (args.includes('--vehicle-perf')) {
  const rows=[],startedAt=new Date().toISOString(),loadBefore=os.loadavg();
  try {
    await send('Page.setWebLifecycleState',{state:'active'});
    await send('Emulation.setFocusEmulationEnabled',{enabled:true});
    await send('Emulation.setDeviceMetricsOverride',{width:1280,height:720,deviceScaleFactor:1,mobile:false});
    await evaluate(`(() => {const c=window.__game.coarse;let release;const gate=new Promise(r=>release=r);
      window.__carLightStreamingHold={update:c.update,prepare:c.prepareModel,release};
      const prepare=c.prepareModel;c.update=()=>{};c.prepareModel=async function(...args){await gate;return prepare.apply(this,args)};})()`);
    for(let round=0;round<3;round++)for(const mode of [0,2]) {
      const row=await evaluate(`(async()=>{
        const g=window.__game,r=window.__render;g.vehicleSignals.lights=${mode};r.setHour(22);
        g.resolution.scale=1;g.resolution.update=()=>1;r.renderer.setPixelRatio(1);r.renderer.setSize(1280,720);
        const residency=()=>JSON.stringify([...g.coarse.entries].filter(([id,e])=>e.model).map(([id,e])=>[id,e.visualHash]).sort((a,b)=>a[0].localeCompare(b[0])));
        await new Promise(resolve=>setTimeout(resolve,350));const before=residency();r.perf.reset();
        const samples=[];let last=performance.now(),frames=0;
        await new Promise((resolve,reject)=>{const previous=r.afterFrame,timer=setTimeout(()=>{r.afterFrame=previous;reject(new Error('measurement timeout'))},20000);
          r.afterFrame=()=>{previous?.();const now=performance.now();if(frames++>5)samples.push(now-last);last=now;
            if(samples.length===60){r.afterFrame=previous;clearTimeout(timer);resolve();}};});
        await r.perf.flush();const mean=samples.reduce((a,b)=>a+b,0)/samples.length;
        return {round:${round},lights:${mode},pose:g.state().vehicle.pos,camera:r.camera.position.toArray(),hour:g.dayClock.hour,quality:g.params.quality,meanMs:mean,fps:1000/mean,perf:r.perf.snapshot(),residency:before,residencyStable:before===residency()};
      })()`);
      rows.push(row);console.log(JSON.stringify({round,mode,ms:row.meanMs,fps:row.fps,gpuMs:row.perf.gpuMs.p50}));
    }
    fs.writeFileSync(path.join(out,'frame-times-interleaved.json'),JSON.stringify({startedAt,finishedAt:new Date().toISOString(),loadBefore,loadAfter:os.loadavg(),uptimeSeconds:os.uptime(),rows},null,2)+'\n');
  } finally {
    await evaluate(`(() => {const h=window.__carLightStreamingHold;if(!h)return;
      Object.assign(window.__game.coarse,{update:h.update,prepareModel:h.prepare});h.release();delete window.__carLightStreamingHold;})()`).catch(()=>{});
    await send('Page.setWebLifecycleState',{state:'frozen'});socket.close();
  }
  process.exit(0);
}

// Same-pose car-light stills plus paired frame-delivery measurements, through the shared transport.
if (args.includes('--vehicle-stills')) {
  try {
    await send('Page.setWebLifecycleState',{state:'active'});
    await send('Emulation.setFocusEmulationEnabled',{enabled:true});
    await send('Page.bringToFront');
    await send('Emulation.setDeviceMetricsOverride',{width:1280,height:720,deviceScaleFactor:1,mobile:false});
    const measurements=[];
    for(const scale of args.includes('--stills-only')?[]:[.5,1]) for(const mode of [0,2]) {
      const row=await evaluate(`(async()=>{
        const g=window.__game,r=window.__render;g.vehicleSignals.lights=${mode};
        g.resolution.scale=${scale};g.resolution.update=()=>${scale};r.renderer.setPixelRatio(${scale});
        await new Promise(resolve=>setTimeout(resolve,800));r.perf.reset();
        const samples=[];let last=performance.now(),frames=0;
        await new Promise((resolve,reject)=>{const previous=r.afterFrame,timer=setTimeout(()=>{r.afterFrame=previous;reject(new Error('frame measurement timeout'))},20000);
          r.afterFrame=()=>{previous?.();const now=performance.now();if(frames++>5)samples.push(now-last);last=now;
            if(samples.length===90){r.afterFrame=previous;clearTimeout(timer);resolve();}};});
        await r.perf.flush();const sorted=[...samples].sort((a,b)=>a-b),mean=samples.reduce((a,b)=>a+b,0)/samples.length;
        return {scale:${scale},lights:${mode},meanMs:mean,fps:1000/mean,p50:sorted[45],p95:sorted[85],perf:r.perf.snapshot()};
      })()`);
      measurements.push(row);console.log(JSON.stringify({scale,mode,meanMs:row.meanMs,fps:row.fps}));
    }
    if(measurements.length) fs.writeFileSync(path.join(out,'frame-times.json'),JSON.stringify(measurements,null,2)+'\n');
    const shots=[];
    for(const [hour,mode,label] of [[22,0,'night-off'],[22,1,'night-driving'],[22,2,'night-long'],[13,1,'day-driving']]) {
      const state=await evaluate(`(async()=>{const g=window.__game,r=window.__render;
        g.vehicleSignals.lights=${mode};r.setHour(${hour});g.resolution.update=()=>1;g.resolution.scale=1;r.renderer.setPixelRatio(1);
        document.querySelectorAll('body > *:not(canvas)').forEach(e=>e.style.setProperty('display','none','important'));
        await new Promise(resolve=>setTimeout(resolve,1500));
        return {hour:g.dayClock.hour,mode:${mode},vehicle:g.state().vehicle.pos,camera:r.camera.position.toArray(),quaternion:r.camera.quaternion.toArray(),
          beams:g.vehicleBeams.lights.map(l=>({intensity:l.intensity,range:l.distance,decay:l.decay,depths:l.userData.depths})),
          tiles:[...g.coarse.entries].filter(([id,e])=>e.model).map(([id,e])=>({id,hash:e.visualHash,pass:e.pass})),errors:g.errors};})()`);
      const shot=await send('Page.captureScreenshot',{format:'jpeg',quality:92,captureBeyondViewport:false});
      fs.writeFileSync(path.join(out,label+'.jpg'),Buffer.from(shot.data,'base64'));shots.push({file:label+'.jpg',...state});
    }
    fs.writeFileSync(path.join(out,'vehicle-shots.json'),JSON.stringify(shots,null,2)+'\n');
    console.log('Saved four 1280x720 stills and frame-times.json');
  } finally {await send('Page.setWebLifecycleState',{state:'frozen'});socket.close();}
  process.exit(0);
}

// Record a real-time, 1280x720 play-mode signal cycle in the existing browser (no second renderer).
if (opt('--vehicle-video', '')) {
  try {
    await send('Page.setWebLifecycleState', {state:'active'});
    await send('Emulation.setFocusEmulationEnabled', {enabled:true});
    await send('Page.bringToFront');
    await send('Emulation.setDeviceMetricsOverride', {width:1280,height:720,deviceScaleFactor:1,mobile:false});
    const result=await evaluate(`(async()=>{
      const g=window.__game,r=window.__render;
      const key=(type,code)=>window.dispatchEvent(new KeyboardEvent(type,{code}));
      r.beforeFrame=undefined;r.setHour(22);
      if(g.state().paused){key('keydown','Escape');await new Promise(r=>setTimeout(r,150));key('keyup','Escape');}
      key('keyup','Space');
      g.input.bindings.throttle=[{kind:'key',code:'KeyW',scale:.08}];
      g.vehicleSignals.lights=0;
      await new Promise(r=>setTimeout(r,400));
      const canvas=r.renderer.domElement;
      if(canvas.width!==1280||canvas.height!==720)throw new Error('Video canvas must be 1280x720: '+canvas.width+'x'+canvas.height);
      const stream=canvas.captureStream(30),chunks=[],events=[];
      const recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8',videoBitsPerSecond:5000000});
      const ended=new Promise(resolve=>recorder.onstop=resolve);
      recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
      const at=performance.now(),start={...g.physics.latest.vehicle.pos};
      const cycle=mode=>{if(mode===0)g.vehicleSignals.lights=0;else{key('keydown','KeyL');setTimeout(()=>key('keyup','KeyL'),120);}events.push({seconds:(performance.now()-at)/1000,lights:mode});};
      cycle(0);recorder.start(250);key('keydown','KeyW');
      const timers=[setTimeout(()=>cycle(1),3000),setTimeout(()=>cycle(2),6500),
        setTimeout(()=>key('keydown','KeyG'),8000),setTimeout(()=>key('keyup','KeyG'),9000)];
      await new Promise(r=>setTimeout(r,10000));
      key('keyup','KeyW');key('keyup','KeyG');key('keydown','Space');
      timers.forEach(clearTimeout);recorder.stop();await ended;stream.getTracks().forEach(t=>t.stop());
      const bytes=new Uint8Array(await new Blob(chunks,{type:recorder.mimeType}).arrayBuffer());
      let raw='';for(let i=0;i<bytes.length;i+=32768)raw+=String.fromCharCode(...bytes.subarray(i,i+32768));
      return {video:btoa(raw),events,duration:(performance.now()-at)/1000,start,end:g.physics.latest.vehicle.pos,speed:g.physics.latest.vehicle.speed,errors:g.errors};
    })()`);
    const {video,...metadata}=result;
    fs.writeFileSync(opt('--vehicle-video'),Buffer.from(video,'base64'));
    fs.writeFileSync(opt('--vehicle-video')+'.json',JSON.stringify(metadata,null,2)+'\n');
    console.log(JSON.stringify(metadata));
  } finally {await send('Page.setWebLifecycleState',{state:'frozen'});socket.close();}
  process.exit(0);
}
if (opt('--eval-file', '')) {
  try {
    await send('Page.setWebLifecycleState', { state: 'active' });
    await send('Emulation.setFocusEmulationEnabled', { enabled: true });
    await send('Emulation.setDeviceMetricsOverride', {width:1280,height:720,deviceScaleFactor:1,mobile:false});
    const result = await evaluate(fs.readFileSync(opt('--eval-file'), 'utf8'));
    console.log(JSON.stringify(result));
    if(opt('--result-file','')) fs.writeFileSync(opt('--result-file'),JSON.stringify(result,null,2)+'\n');
    if(opt('--screenshot','')) {
      const shot=await send('Page.captureScreenshot',{format:'jpeg',quality:92,captureBeyondViewport:false});
      fs.writeFileSync(opt('--screenshot'),Buffer.from(shot.data,'base64'));
    }
  } finally {
    if (!args.includes('--keep-active')) await send('Page.setWebLifecycleState', { state: 'frozen' });
    socket.close();
  }
  process.exit(0);
}
if (args.includes('--inspect')) {
  console.log(JSON.stringify(await evaluate(`(() => {const g=window.__game; return {ready:window.__qaReady,error:window.__qaError,qa:g?.qa,play:g?.state?((s)=>({mode:s.mode,paused:s.paused,start:s.startScreen,loading:s.loading}))(g.state()):null,pose:window.__qaPose,params:g?.params,progress:g?.loadProgress?.(),frames:g?.clock?.frames,vegetationPending:g?.vegetation?.pending?.size,tiles:[...(g?.lights?.source?.entries??[])].map(([id,e])=>({id,state:e.state,pass:e.pass,hash:e.visualHash,model:!!e.model})),scenery:g?.scenery?.progress};})()`)));
  socket.close(); process.exit(0);
}
try {
  await send('Page.setWebLifecycleState', { state: 'active' });
  await send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });
  const ordered = [...cameras.entries()].sort((a, b) =>
    Number(b[1].id.startsWith('pano-')) - Number(a[1].id.startsWith('pano-')));
  for (const [n, cam] of ordered) {
    if (!only.includes(cam.id)) continue;
    for (const at of times) {
      const file = `${String(n + 1).padStart(2, '0')}-${cam.id}-${at.replace(':', '')}.jpg`;
      if (valid(shots.get(file)) && !args.includes('--flicker')) continue;
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
      const url = `${base}?cam=${cam.cam}&time=${at}&quality=${opt('--quality', 'high')}&aa=${opt('--aa', 'taa')}&hud=0&traffic=0&walkers=0`;
      if (await evaluate('!!window.__qaMove')) {
        await evaluate(`window.__render.beforeFrame = undefined; window.__game.clock.simSeconds = 0;
          window.__render.setHour(${h + m / 60}); window.__qaMove(${JSON.stringify(pose)});`);
      } else {
        await send('Page.navigate', { url });
      }
      const ci = Math.floor((E - 23459500) / 250), cj = Math.floor((6704500 - N) / 250);
      const wanted = Array.from({ length: 9 }, (_, k) => `t0_${ci - 1 + k % 3}_${cj - 1 + Math.floor(k / 3)}`);
      let compared = false;
      let state;
      let throttled = false;
      let settleRetries = 0;
      while (Date.now() - started < timeout) {
        if (!compared && await evaluate(`!!window.__game?.vegetation?.library && !window.__game.vegetation.pending.size && ${JSON.stringify(wanted)}.every(id => { const e=window.__game.lights?.source?.entries.get(id); return e?.model && e.pass==='full'; })`)) {
          if (opt('--setup-file', '')) await evaluate(fs.readFileSync(opt('--setup-file'), 'utf8'));
          compared = true;
        }
        if (!throttled) throttled = await evaluate(`(() => {
          if (!window.__render) return false;
          let last = 0;
          window.__render.beforeFrame = () => {
            const now = performance.now();
            if (now - last < 100) return false;
            last = now; return true;
          };
          return true;
        })()`);
        state = await evaluate(`(() => {
          const g = window.__game, env = window.__render?.environment;

          const tiles = ${JSON.stringify(wanted)}.map(id => { const e = g?.lights?.source?.entries.get(id);
            return {id, hash:e?.visualHash, pass:e?.pass, full:e?.state === 'draft' && e?.pass === 'full' && !e?.preview && !!e?.model}; });
          const vegetationReady = !!g?.vegetation?.library && !g.vegetation.pending.size && !g.vegetation.disposed;
          return {vegetationReady,canopyMode:window.__canopyCompare?.mode??'before',canopyTiles:window.__canopyCompare?.tiles,natureHash:g?.vegetation?.library?.manifest?.glb?.sha256,ready:!!window.__qaReady,error:window.__qaError,tiles,qa:g?.qa, sun:env?.sunElevation,
            azimuth:env?.sunAzimuth, clock:g?.dayClock?.snapshot(), hour:g?.dayClock?.hour, pose:window.__qaPose,
            info:window.__qaInfo,night:g?.dayClock?.night,scenery:g?.scenery?.progress};
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
        if (compared && state.vegetationReady && state.ready && state.tiles.every(t => t.full) && scenerySettled(state.scenery)
          && !assets.size && Date.now() - lastAsset > 2000) break;
        await delay(1000);
      }
      await delay(2000);
      await evaluate(`document.querySelectorAll('body > *:not(canvas)').forEach(e => e.style.setProperty('display','none','important'));`);
      const screenshot = await send('Page.captureScreenshot', { format: 'jpeg', quality: 92, captureBeyondViewport: false });
      await evaluate('window.__render.beforeFrame = () => false');
      fs.writeFileSync(path.join(out, file), Buffer.from(screenshot.data, 'base64'));
      const row = { file, time: at, type: cam.type, place: cam.place, camera_height_m: cam.camera_height_m,
        notes: cam.notes, url, build: `${execFileSync('git', ['branch', '--show-current'], {encoding:'utf8'}).trim()}@${build}`, capture: {
          vegetation_ready:state.vegetationReady, canopy_mode:state.canopyMode, canopy_tiles:state.canopyTiles, nature_hash:state.natureHash, comparison_variant: opt('--variant', 'after'), comparison_baseline: opt('--comparison', 'same-build'), wall_s: (Date.now() - started) / 1000, qa_ready: state.ready, qa_error: state.error ?? null,
          incomplete_tiles: state.tiles.filter(t => !t.full).map(t => t.id), failed_assets_final: [...new Set(failures)],
          tiles: state.tiles, sun_elevation_deg: state.sun, quality: state.info?.quality, backend: state.info?.backend,
          sun_azimuth_deg: state.azimuth, clock: state.clock,
          settle_retries: settleRetries,
          effective_hour: state.hour, effective_pose: state.pose,
          scenery: state.scenery,
          vis_tris: state.qa?.visTris, load_ms: state.qa?.loadMs, night_level: state.night } };
      shots.set(file, row); save();
      console.log(JSON.stringify({file,valid:valid(row),seconds:row.capture.wall_s}));
      if (!valid(row)) throw new Error(`Incomplete capture: ${file}; resume after resolving missing tiles/assets`);
      await captureFlicker(file, pose);
    }
  }
} finally {
  // Freeze even when tile settling, screenshotting, or evaluation fails.
  await send('Page.setWebLifecycleState', { state: 'frozen' }).catch(error => console.error(error.message));
  socket.close();
}
