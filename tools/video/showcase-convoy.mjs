// Three real multiplayer clients with independently stepped Rapier cars, on an owned server.
import {chromeArgs,chromeIgnoreDefaultArgs,chromeExecutable} from '../dev/chrome-args.mjs';
import {chromium} from '../../game/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';import path from 'node:path';import {spawn} from 'node:child_process';import {once} from 'node:events';
import {isolateBrowser,port,workRoot} from '../dev/environment.mjs';
isolateBrowser();const root=path.join(workRoot,'video');
const context=await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR,'showcase-convoy'),{
 executablePath:chromeExecutable(),ignoreDefaultArgs:chromeIgnoreDefaultArgs,headless:true,viewport:{width:1920,height:1080},deviceScaleFactor:1,
 args:chromeArgs(['--enable-unsafe-webgpu','--use-angle=metal','--ignore-gpu-blocklist','--disable-background-timer-throttling','--disable-renderer-backgrounding'])});
await context.addInitScript(()=>{Object.defineProperty(navigator,'getGamepads',{value:()=>[]});localStorage.setItem('turku3d.position-hud.v1','off');});
const pages=[],errors=[];
try {
 for(let i=0;i<3;i++){
  const page=await context.newPage();pages.push(page);if(i)await page.setViewportSize({width:640,height:360});
  page.on('pageerror',e=>errors.push({client:i,error:e.message}));
  await page.route('**/*',r=>{const u=new URL(r.request().url());return ['127.0.0.1','localhost'].includes(u.hostname)&&[String(port(1)),String(port(4))].includes(u.port)?r.continue():r.abort();});
  await page.routeWebSocket('**/*',ws=>{const u=new URL(ws.url());if(u.hostname==='127.0.0.1'&&u.port===String(port(4)))ws.connectToServer();else ws.close();});
  await page.goto(`http://localhost:${port(1)}/?backend=webgpu&quality=${i?'low':'high'}&hud=0&time=2027-07-01T16:00:00%2B03:00&mp=ws://127.0.0.1:${port(4)}/`);
 }
 await Promise.all(pages.map(async(page,i)=>{
  await page.waitForFunction(()=>window.__game?.ready,null,{timeout:180000});
  await page.locator('[data-option="multiplayer"]').click();
  await page.waitForFunction(()=>!window.__game.state().startScreen&&!window.__game.state().loading,null,{timeout:120000});
  await page.waitForFunction(()=>window.__game.state().tilesSettled,null,{timeout:90000});
  await page.addStyleTag({content:'#position-hud,#hud,#status-badges,#dev-layer-toggle,.controls-help,#audio-panel,#audio-toggle{display:none!important}'});
  await page.evaluate(()=>{const w=window,g=w.__game,a=w.__render.renderer._animation,loop=a.getAnimationLoop();
   w.__videoTime=performance.now();performance.now=()=>w.__videoTime;w.__permit=false;
   const live=g.physics.updateInput.bind(g.physics);g.physics.updateInput=(c,v,running)=>{live(c,v,false);if(running)g.physics.step(1/30,c,v);};
   a.setAnimationLoop((...args)=>{if(!w.__permit)return;w.__permit=false;loop(...args);w.__done=true;});
  });console.log('Client ready',i);
 }));
 async function frame(driving=false){
  await Promise.all(pages.map((p,i)=>p.evaluate(({driving,i})=>{const w=window,g=w.__game;g.input.focused=true;g.input.keys.clear();if(driving)g.input.keys.add('KeyW');w.__videoTime+=1000/30;w.__done=false;w.__permit=true;},{driving,i})));
  await Promise.all(pages.map(p=>p.waitForFunction(()=>window.__done,null,{polling:'raf'})));
  await Promise.all(pages.map(p=>p.evaluate(()=>window.__game.physics.stats())));
 }
 // Straight Auransilta crossing, back to front: 18 m and 36 m gaps.
 await Promise.all(pages.map((p,i)=>p.evaluate(async i=>{const g=window.__game,x=292+i*10,z=210+i*14.4;
  const h=await g.physics.raycast({x,y:60,z},{x:0,y:-1,z:0},80);if(!h)throw Error('Missing bridge collision');
  g.physics.resetVehicle({x,y:h.point.y+.75,z},-.964);
  g.physics.setCharacterEnabled(true,{x:x-1.8,y:h.point.y+.1,z:z+1.2});
 },i)));
 for(let f=0;f<60;f++)await frame();
 await Promise.all(pages.map(p=>p.evaluate(()=>window.__game.input.tapped.add('KeyF'))));
 for(let f=0;f<45;f++)await frame();
 const before=await Promise.all(pages.map(p=>p.evaluate(()=>window.__game.state())));
 console.log('Convoy seats',JSON.stringify(before.map(s=>({mode:s.mode,car:s.vehicle?.pos,players:s.multiplayer.players,nick:s.multiplayer.nick}))));
 if(before.some(s=>s.mode!=='car'))throw Error('Every convoy client must own and drive its car');
 const encoder=spawn('ffmpeg',['-v','error','-y','-f','image2pipe','-vcodec','mjpeg','-r','30','-i','pipe:0','-an','-c:v','libx264','-preset','fast','-crf','17','-pix_fmt','yuv420p','-profile:v','high','-threads','3',path.join(root,'shots/convoy.mp4')],{stdio:['pipe','inherit','inherit']});
 const motion=[];
 await pages[0].bringToFront();
 for(let f=0;f<300;f++){
  await frame(f<150);
  if(f%15===0){const states=await Promise.all(pages.map(p=>p.evaluate(()=>window.__game.state())));const c=states[0].camera.pos;motion.push({frame:f,pose:{E:23459500+c[0],N:6704500-c[2],H:c[1]},states});}
  const bytes=await pages[0].screenshot({type:'jpeg',quality:95});if(!encoder.stdin.write(bytes))await once(encoder.stdin,'drain');
 }
 encoder.stdin.end();const [code]=await once(encoder,'exit');if(code)throw Error('Encoder failed');
 await pages[0].screenshot({path:path.join(root,'review/convoy-final.png')});
 fs.writeFileSync(path.join(root,'shots/convoy.json'),JSON.stringify({shot:{id:'convoy',seconds:10,clients:3,physicsDrive:true,localMultiplayer:true},info:{info:{backend:'WebGPU'}},motion,errors},null,2));
 console.log('Captured real three-client convoy',motion.at(-1).states.map(s=>({mode:s.mode,speed:s.vehicle?.speed,pos:s.vehicle?.pos,remotes:s.multiplayer.remoteVisuals.length})));
}finally{await context.close();}
