// Real client decoding, styling, GPU preparation and rendered tile publication against deployed OCI.
// The camera follows a fixed-speed corridor; physics residency is measured separately from planned crossings.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import WebSocket from '../../server/node_modules/ws/wrapper.mjs';
import { createServer } from '../../server/http.js';
import { ScoreStore } from '../../server/store.js';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, port } from '../../tools/dev/environment.mjs';

isolateBrowser();
const [label = 'arrival', secondsArg = '120', speedArg = '50'] = process.argv.slice(2);
const seconds = Number(secondsArg), speed = Number(speedArg);
const out = path.join(reportsRoot, 'tile-arrival', label);
fs.mkdirSync(out, { recursive:true });
const room = createServer({ store:new ScoreStore(path.join(out, 'scores')) });
await new Promise(resolve => room.server.listen(port(4), '127.0.0.1', resolve));
const context = await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR, `arrival-${label}`), {
  executablePath:chromeExecutable(), ignoreDefaultArgs:chromeIgnoreDefaultArgs, headless:true,
  viewport:{width:1280,height:720}, args:chromeArgs(['--enable-unsafe-webgpu','--use-angle=metal','--ignore-gpu-blocklist']),
});
const hardStop = setTimeout(() => { void context.close(); }, (seconds + 200) * 1000);
try {
  const page = await context.newPage();
  await page.routeWebSocket('wss://arrival.invalid/', socket => {
    const upstream = new WebSocket(`ws://127.0.0.1:${port(4)}/`, { origin:'https://wnt.github.io' });
    const queued=[];
    socket.onMessage(message => { if (upstream.readyState === WebSocket.OPEN) upstream.send(message); else queued.push(message); });
    upstream.on('open', () => { for (const message of queued) upstream.send(message); queued.length=0; });
    upstream.on('message',(data,binary)=>socket.send(binary ? data : data.toString()));
    upstream.on('error',()=>socket.close()); upstream.on('close',()=>socket.close()); socket.onClose(()=>upstream.close());
  });
  await page.route('https://arrival.invalid/**', async route => {
    const url=new URL(route.request().url());
    const response=await route.fetch({url:`http://127.0.0.1:${port(4)}${url.pathname}${url.search}`});
    await route.fulfill({response});
  });
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.clear(); sessionStorage.clear();
    const original=window.fetch.bind(window);
    window.__arrivalRequests={};
    window.fetch=(input, init)=>{
      const url=typeof input==='string' ? input : input.url ?? String(input);
      const tile=/\/coarse\/(t0_-?\d+_-?\d+)\/(?:manifest|collision)\.json/.exec(url)?.[1];
      if (tile) window.__arrivalRequests[tile] ??= performance.now();
      return original(input,init);
    };
  });
  await page.goto('https://wnt.github.io/turku-game/game/?mp=wss%3A%2F%2Farrival.invalid%2F', {waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>window.__game?.ready, null, {timeout:150000});
  await page.screenshot({path:path.join(out,'ready.png')});
  console.log(JSON.stringify({label,stage:'ready'}));
  await page.locator('[data-option="multiplayer"]').click();
  await page.evaluate(({speed})=>{
    const game=window.__game, coarse=game.coarse, camera=window.__render.camera;
    const scene=game.tiles.models.values().next().value.parent;
    const started=performance.now();
    // Yesterday's retained seven-crossing drive is on this same corridor. The
    // full path includes the cold navigation to its start; request clocks retain it.
    const state=window.__arrival={started,speed,tiles:{},frames:[],samples:[],pending:new Map()};
    const request=(id)=>state.tiles[id] ??= {request:window.__arrivalRequests[id] ?? performance.now(),basic:null,full:null,arrival:null};
    const apply=coarse.applyFragment.bind(coarse);
    coarse.applyFragment=async (id,entry,fragment)=>{
      request(id);
      await apply(id,entry,fragment);
      if(entry.model && entry.visualHash===fragment.visual[id].sha256)
        state.pending.set(id,fragment.pass_kind ?? (fragment.format ? 'full' : 'first'));
    };
    for(const [id,e] of coarse.entries) if(e.model){
      const row=request(id);row.basic=started;
      let basic=false;e.model.traverse(n=>{basic ||= n.userData.basic===true;});
      if(!basic) row.full=started;
    }
    let previous=started;
    const after=window.__render.afterFrame;
    window.__render.afterFrame=()=>{
      after?.();
      const now=performance.now();state.frames.push(now-previous);previous=now;
      for(const [id,pass] of state.pending){const row=request(id);row.basic ??= now;if(pass==='full')row.full ??= now;}
      state.pending.clear();
    };
    const update=coarse.update.bind(coarse), stream=game.streamer.update.bind(game.streamer);
    const pose=()=>{const t=(performance.now()-started)/1000;return {x:-375+t*speed/3.6,z:-375};};
    coarse.setRouteHint(null);
    coarse.update=()=>{
      const p=pose(),now=performance.now(),id=`t0_${Math.floor(p.x/250)}_${Math.floor(p.z/250)}`;
      request(id).arrival ??= now;
      update(p.x,p.z,now);
      if(!state.samples.length || now-state.samples.at(-1).at>=250)
        state.samples.push({at:now,x:p.x,z:p.z,supported:game.streamer.supports(p.x,p.z),tile:id});
    };
    game.streamer.update=()=>{const p=pose();stream(p.x,p.z,false,speed/3.6,0);};
    const before=scene.onBeforeRender;
    scene.onBeforeRender=function(...args){before.apply(this,args);if(args[2]!==camera)return;
      const p=pose();camera.position.set(p.x-6,speed>100 ? 70 : 9,p.z+3);camera.lookAt(p.x+25,5,p.z);camera.updateMatrixWorld();};
  },{speed});
  console.log(JSON.stringify({label,stage:'traversing',speed_kmh:speed,seconds}));
  await page.waitForTimeout(seconds*1000);
  const result=await page.evaluate(()=>{
    const p=window.__arrival,now=performance.now();
    for(const [id,at] of Object.entries(window.__arrivalRequests))p.tiles[id] ??= {request:at,basic:null,full:null,arrival:null};
    const crossings=Object.entries(p.tiles).filter(([,r])=>r.arrival!==null && r.arrival>p.started+1000).map(([tile,r])=>({tile,
      request_seconds:(r.request-p.started)/1000,arrival_seconds:(r.arrival-p.started)/1000,
      basic_seconds:r.basic===null ? null : (r.basic-r.request)/1000,
      full_seconds:r.full===null ? null : (r.full-r.request)/1000,
      edge_wait_seconds:r.basic===null ? null : Math.max(0,(r.basic-r.arrival)/1000),
      full_delay_seconds:r.full===null ? null : Math.max(0,(r.full-r.arrival)/1000)}));
    return {speed_kmh:p.speed,seconds:(now-p.started)/1000,crossings,tiles:p.tiles,
      first_before_arrival:crossings.filter(r=>r.edge_wait_seconds===0).length,
      full_by_arrival_plus_5:crossings.filter(r=>r.full_delay_seconds!==null && r.full_delay_seconds<=5).length,
      unsupported_seconds:p.samples.filter(r=>!r.supported).length*.25,
      samples:p.samples,frames:p.frames,errors:window.__game.state().errors};
  });
  result.page_errors=errors;
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2));
  await page.screenshot({path:path.join(out,'end.png')});
  console.log(JSON.stringify({report:out,speed_kmh:speed,crossings:result.crossings,
    first_before_arrival:result.first_before_arrival,full_by_arrival_plus_5:result.full_by_arrival_plus_5,
    unsupported_seconds:result.unsupported_seconds,errors:[...errors,...result.errors]}));
  if(errors.length || result.errors.length) throw new Error('Client errors during arrival traversal');
} finally {clearTimeout(hardStop);await context.close();await room.close();}
