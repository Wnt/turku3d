import {chromeArgs,chromeIgnoreDefaultArgs,chromeExecutable} from '../dev/chrome-args.mjs';
import {chromium} from '../../game/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {isolateBrowser,port,workRoot} from '../dev/environment.mjs';
isolateBrowser();
const root=path.join(workRoot,'video');
const mode=process.argv[2]??'probe';
const context=await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR,'showcase-play'),{
 executablePath:chromeExecutable(),ignoreDefaultArgs:chromeIgnoreDefaultArgs,headless:true,viewport:{width:1920,height:1080},deviceScaleFactor:1,
 args:chromeArgs(['--enable-unsafe-webgpu','--use-angle=metal','--ignore-gpu-blocklist','--disable-background-timer-throttling'])});
await context.addInitScript(()=>{Object.defineProperty(navigator,'getGamepads',{value:()=>[]});localStorage.clear();});
const page=await context.newPage();
await page.route('**/*',r=>{const u=new URL(r.request().url());return ['localhost','127.0.0.1'].includes(u.hostname)&&[String(port(1)),String(port(4))].includes(u.port)?r.continue():r.abort();});
await page.routeWebSocket('**/*',ws=>{const u=new URL(ws.url());if(u.hostname==='127.0.0.1'&&u.port===String(port(4)))ws.connectToServer();else ws.close();});
const errors=[];page.on('pageerror',e=>{errors.push(e.message);console.log('PAGE ERROR',e.message);});
const snap=async name=>{await page.screenshot({path:path.join(root,'shots',`${name}.png`)});const s=await page.evaluate(()=>window.__game?.state?.());fs.writeFileSync(path.join(root,'shots',`${name}.json`),JSON.stringify(s,null,2));console.log(name,JSON.stringify({mode:s.mode,feet:s.feet,mission:s.missions.phase,models:s.visModels}));};
try {
 await page.goto(`http://localhost:${port(1)}/?backend=webgpu&quality=high&hud=0&time=2027-07-01T16:00:00%2B03:00&mp=ws://127.0.0.1:${port(4)}/`);
 await page.waitForFunction(()=>window.__game?.ready,null,{timeout:180000});
 console.log('Ready');
 await page.locator('[data-option="multiplayer"]').click();
 await page.waitForFunction(()=>!window.__game.state().startScreen&&!window.__game.state().loading,null,{timeout:120000});
 await page.waitForFunction(()=>window.__game.state().tilesSettled,null,{timeout:90000}).catch(()=>{});
 await page.addStyleTag({content:'#position-hud,#hud,#status-badges,#dev-layers-toggle,.controls-help,#audio-panel,#audio-toggle{display:none!important}'});
 await page.waitForTimeout(500);
 await snap('game-start');
 if(mode==='probe')process.exitCode=0;
 else {
  await page.evaluate(()=>{
   const w=window,r=w.__render.renderer,a=r._animation,loop=a.getAnimationLoop();
   w.__videoTime=performance.now();performance.now=()=>w.__videoTime;
   const physics=w.__game.physics,live=physics.updateInput.bind(physics);physics.updateInput=(c,v,running)=>{live(c,v,false);if(running)physics.step(1/30,c,v);};
   a.setAnimationLoop((...args)=>{if(!w.__permit)return;w.__permit=false;loop(...args);w.__done=true;});
  });
  async function frame(action={}) {
   await page.evaluate(action=>{const w=window,g=w.__game;g.input.keys.clear();for(const key of action.keys??[])g.input.keys.add(key);if(action.yaw!==undefined)g.orbit.yaw=action.yaw;w.__videoTime+=1000/30;w.__done=false;w.__permit=true;},action);
   await page.waitForFunction(()=>window.__done,null,{polling:'raf'});
   await page.evaluate(()=>window.__game.physics.stats());
  }
  async function capture(id,seconds,action) {
   const encoder=spawn('ffmpeg',['-v','error','-y','-f','image2pipe','-vcodec','mjpeg','-r','30','-i','pipe:0','-an','-c:v','libx264','-preset','fast','-crf','17','-pix_fmt','yuv420p','-profile:v','high','-threads','3',path.join(root,'shots',`${id}.mp4`)],{stdio:['pipe','inherit','inherit']});
   const motion=[];
   for(let f=0;f<seconds*30;f++) {
    await frame(typeof action==='function'?await action(f):action);
    if(f%30===0){const state=await page.evaluate(()=>window.__game.state());motion.push({frame:f,state,pose:{E:23459500+state.camera.pos[0],N:6704500-state.camera.pos[2],H:state.camera.pos[1]}});}
    const bytes=await page.screenshot({type:'jpeg',quality:95});if(!encoder.stdin.write(bytes))await once(encoder.stdin,'drain');
   }
   encoder.stdin.end();const [code]=await once(encoder,'exit');if(code)throw Error(`Encoder ${code}`);
   fs.writeFileSync(path.join(root,'shots',`${id}.json`),JSON.stringify({shot:{id,seconds,gameplay:true},info:{info:{backend:'WebGPU'}},motion,errors},null,2));console.log('Captured',id);
  }
  if(mode==='delivery') {
   async function place(x,z,knownY=null){await page.evaluate(async({x,z,knownY})=>{const g=window.__game,h=knownY===null?await g.physics.raycast({x,y:50,z},{x:0,y:-1,z:0},70):null;if(knownY===null&&!h)throw Error('No delivery ground');g.physics.setCharacterEnabled(true,{x,y:knownY??h.point.y+0.1,z});g.orbit.yaw=0;g.orbit.pitch=-.18;},{x,z,knownY});for(let i=0;i<40;i++)await frame();}
   async function interact(){await page.evaluate(()=>window.__game.input.tapped.add('KeyE'));for(let i=0;i<10;i++)await frame();}
   async function choose(id){await page.evaluate(id=>window.__game.shopMenu.chosen=id,id);for(let i=0;i<10;i++)await frame();}
   await place(290,210.84,1.2);await interact();await choose('rent_permit');await choose('leave');
   await place(77.1,187.6);await interact();await choose('take_order');
   const target=await page.evaluate(()=>window.__game.missions.snapshot().hud.targets.find(t=>t.kind==='dropoff').point);
   console.log('Delivery target',target);
   await place(target.x,target.z+2.5);
   const before=await page.evaluate(()=>window.__game.missions.snapshot().money);
   await capture('game_delivery',4,async f=>{if(f===50)await page.evaluate(()=>window.__game.input.tapped.add('KeyE'));return {keys:f<30?['KeyW']:[],yaw:f<50?0:(f-50)*.006};});
   const after=await page.evaluate(()=>window.__game.missions.snapshot().money);
   if(after<=before)throw Error('No completed delivery payout');
   await snap('delivery-result');console.log('Paid delivery',before,after);
  } else if(mode==='rounds') {
   await page.evaluate(()=>{const g=window.__game;g.multiplayerClient().round('propose');g.orbit.yaw=-1.07;g.orbit.pitch=-0.18;});
   for(let i=0;i<15;i++)await frame();
   await capture('rounds_vote',2,async f=>({yaw:-1.07+f*.003}));
   await page.evaluate(()=>window.__game.multiplayerClient().round('start',5));
   for(let i=0;i<5;i++)await frame();
   await capture('rounds_countdown',3,async f=>({yaw:-.9+f*.003}));
   await fetch(`http://127.0.0.1:${port(4)}/capture/advance?ms=310000`);
   for(let i=0;i<15;i++)await frame();
   await capture('rounds_scoreboard',2,{});
   await snap('rounds-result');
   const ids=['rounds_vote','rounds_countdown','rounds_scoreboard'];
   const listing=path.join(root,'shots/rounds-concat.txt');
   fs.writeFileSync(listing,ids.map(id=>`file '${path.join(root,'shots',id+'.mp4')}'`).join('\n'));
   const concat=spawn('ffmpeg',['-v','error','-y','-f','concat','-safe','0','-i',listing,'-c','copy',path.join(root,'shots/rounds_demo.mp4')],{stdio:'inherit'});
   const [code]=await once(concat,'exit');if(code)throw Error('Round montage failed');
   const clips=ids.map(id=>JSON.parse(fs.readFileSync(path.join(root,'shots',id+'.json'),'utf8')));
   fs.writeFileSync(path.join(root,'shots/rounds_demo.json'),JSON.stringify({shot:{id:'rounds_demo',seconds:7,localMultiplayer:true,clockAdvanced:true},info:clips[0].info,motion:clips.flatMap(c=>c.motion),errors:clips.flatMap(c=>c.errors)},null,2));
  } else {
  await page.evaluate(()=>{window.__game.physics.setCharacterEnabled(true,{x:290,y:1.2,z:210.84});window.__game.orbit.yaw=-1.2;});
  for(let i=0;i<45;i++)await frame();
  await page.evaluate(()=>window.__game.input.tapped.add('KeyE'));
  for(let i=0;i<10;i++)await frame();
  console.log('Market',await page.evaluate(()=>window.__game.state().shopMenu));
  await capture('game_market',3,async f=>{if(f===35)await page.evaluate(()=>window.__game.shopMenu.chosen='rent_permit');return {};});
  await page.evaluate(()=>window.__game.shopMenu.chosen='leave');
  for(let i=0;i<10;i++)await frame();
  await page.evaluate(async()=>{const g=window.__game,h=await g.physics.raycast({x:190,y:50,z:251},{x:0,y:-1,z:0},70);if(!h)throw Error('No walk ground');g.physics.setCharacterEnabled(true,{x:190,y:h.point.y+0.1,z:251});g.orbit.yaw=-1.07;g.orbit.pitch=-0.15;});
  for(let i=0;i<40;i++)await frame();
  await capture('game_walk',7,{keys:['KeyW'],yaw:-1.07});
  await page.evaluate(async()=>{const g=window.__game,h=await g.physics.raycast({x:171,y:50,z:260},{x:0,y:-1,z:0},70);if(!h)throw Error('No drive ground');g.physics.resetVehicle({x:171,y:h.point.y+0.75,z:260},0.5);g.physics.setCharacterEnabled(true,{x:170,y:h.point.y+0.1,z:258});});
  for(let i=0;i<40;i++)await frame();
  await page.evaluate(()=>window.__game.input.tapped.add('KeyF'));
  for(let i=0;i<20;i++)await frame();
  const driving=await page.evaluate(()=>window.__game.state().mode);console.log('Drive mode',driving);if(driving!=='car')throw Error('Did not enter car');
  await capture('game_drive',8,async f=>({keys:f<130?['KeyW']:[]}));
  await snap('game-after-drive');
  }
 }
} finally {await context.close();}
