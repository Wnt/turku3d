// Remote Air diagnostic only. Never imported by production. air-session owns the exclusive remote Chrome.
import { chromium } from '../../../game/node_modules/@playwright/test/index.mjs';
import { execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
const label = process.argv[2];
if (!['before','after','controller'].includes(label)) throw new Error('Usage: physics-latency.mjs before|after|controller');
const task = process.env.TURKU3D_SANDBOX, port = Number(process.env.TURKU3D_PORT_BASE);
const out = path.join(process.env.TURKU3D_WORK,'reports/latency/air',label);
fs.mkdirSync(out,{recursive:true});
if(fs.existsSync(path.join(out,'STOP')))fs.unlinkSync(path.join(out,'STOP'));
const air = `${process.env.HOME}/turku3d/profiling/air-session`;
const budget=JSON.parse(execFileSync('python3',['-c',"import sys,json; sys.path.insert(0,'scripts/dev'); import browser_budget as b; a=b.read(sys.argv[1]); print(json.dumps({'refusal':b.refusal(a)}))",task],{encoding:'utf8'}));
if(budget.refusal)throw new Error(budget.refusal);
const originals = new Map();
let browser, recorder, acquiredAt, repeatTimer;
try {
 if (label === 'before') {
  // Restore the actual current main implementation, preserving this branch in memory for finally.
  const files=execFileSync('git',['diff','--name-only','main','HEAD','--','game'],{encoding:'utf8'}).trim().split('\n');
  for(const file of files) {
   let source;
   try { source=execFileSync('git',['show',`main:${file}`],{encoding:'utf8',stdio:['ignore','pipe','ignore']}); } catch { continue; }
   originals.set(file,fs.readFileSync(file));
   if(file==='game/src/physics/worker.ts')source=source.replace("post({ type: 'state',","post({ type: 'state', publishedAt: performance.timeOrigin + performance.now(),");
   fs.writeFileSync(file,source);
  }
 }
 const session=JSON.parse(execFileSync(air,['start',task,'--game-port',String(port),'--cdp-port',String(port+8),'--url','/?test'],{encoding:'utf8',timeout:90000}));
 acquiredAt=Date.now();
 console.log('Air session',JSON.stringify(session));
 browser=await chromium.connectOverCDP(session.cdp);
 const context=browser.contexts()[0],page=context.pages()[0];
 if(label==='controller')await page.addInitScript(()=>{
  window.__airPad={axes:[0,0,0,0],buttons:Array(17).fill(0)};
  Object.defineProperty(navigator,'getGamepads',{value:()=>[{id:'Xbox virtual Air diagnostic',index:0,connected:true,mapping:'standard',timestamp:performance.now(),axes:window.__airPad.axes,buttons:window.__airPad.buttons.map(value=>({value,pressed:value>.5,touched:value>0}))}]});
 });
 page.on('pageerror',e=>console.log('PAGE',e.message));
 await page.addInitScript(() => {
  window.__air = { rows:[],keys:[] };
  const add=EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener=function(type,fn,opts){
   if((type==='keydown'||type==='keyup')&&typeof fn==='function') {
    const original=fn;
    fn=function(e){const t=performance.now();try{return original.call(this,e);}finally{window.__air.keys.push({t,type,repeat:e.repeat,ms:performance.now()-t});}};
   }
   return add.call(this,type,fn,opts);
  };
 });
 // Install recorder hooks before workers are constructed, using its normal navigation mode.
 recorder=spawn(process.execPath,[`${process.env.HOME}/turku3d/profiling/record.mjs`,session.cdp,session.url,'360',out],{stdio:'inherit'});
 await page.waitForFunction(()=>window.__game?.ready,null,{timeout:240000});
 await page.getByRole('option',{name:'New game'}).click();
 await page.getByRole('button',{name:'Skip',exact:true}).click();
 await page.waitForFunction(()=>!window.__game.state().paused,null,{timeout:60000});
 await page.waitForTimeout(3000);
 await page.evaluate(async()=>{
  const g=window.__game,p=g.physics,v=p.latest.vehicle;
  p.setCharacterEnabled(true,{x:v.pos.x-2,y:v.pos.y-.7,z:v.pos.z});
  await p.stats();
 });
 await page.waitForTimeout(1000);
 await page.keyboard.press('KeyF');
 await page.waitForFunction(()=>window.__game.state().mode==='car',null,{timeout:60000});
 await page.evaluate(()=>{
  const g=window.__game;window.__air.start=g.physics.latest.vehicle;
  let last=performance.now();
  function sample(){const n=performance.now(),p=g.physics,s=p.latest;
   window.__air.rows.push({t:n,frame:n-last,tick:s?.tick,age:s?.publishedAt?performance.timeOrigin+n-s.publishedAt:null,
    stepMs:s?.stepMs,bytes:p.stateBytes,transport:p.transport,heap:performance.memory?.usedJSHeapSize,
    speed:s?.vehicle?.speed,phase:window.__air.phase??'idle'});
   last=n;requestAnimationFrame(sample);
  }requestAnimationFrame(sample);
 });
 const cdp=await context.newCDPSession(page);
 const key=async(code,down,repeat=false)=>cdp.send('Input.dispatchKeyEvent',{type:down?'keyDown':'keyUp',code:`Key${code.toUpperCase()}`,key:code,windowsVirtualKeyCode:code.toUpperCase().charCodeAt(0),autoRepeat:repeat});
 const phase=async(name,held,seconds)=>{
  await page.evaluate(name=>{window.__air.phase=name;},name);
  if(label==='controller')await page.evaluate(held=>{window.__airPad.axes[0]=held.includes('a')?-1:held.includes('d')?1:0;window.__airPad.buttons[7]=held.includes('w')?1:0;},held);
  else for(const k of held)await key(k,true);
  if(held.length&&label!=='controller')repeatTimer=setInterval(()=>{void key(held.at(-1),true,true).catch(()=>{});},1000/30);
  await page.waitForTimeout(seconds*1000);
  clearInterval(repeatTimer);
  if(label==='controller')await page.evaluate(()=>{window.__airPad.axes[0]=0;window.__airPad.buttons[7]=0;});
  else for(const k of held)await key(k,false);
 };
 await phase('idle',[],10);
 await phase('W',['w'],20);
 await page.evaluate(async()=>{
  const {start}=window.__air;const p=window.__game.physics;
  p.resetVehicle({x:start.pos.x,y:start.pos.y+.1,z:start.pos.z},2*Math.atan2(start.rot.y,start.rot.w));await p.stats();
 });
 await phase('settle',[],2);
 await phase('W+A-standstill',['w','a'],20);
 await phase('W+D-driving',['w','d'],20);
 await phase('released',[],10);
 const result=await page.evaluate(()=>({rows:window.__air.rows,keys:window.__air.keys,
  viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},isolated:crossOriginIsolated,errors:window.__game.errors}));
 fs.writeFileSync(path.join(out,'state-age.json'),JSON.stringify(result));
 const groups={};
 for(const row of result.rows){const g=groups[row.phase]??=[];g.push(row);}
 const stats=values=>{const v=values.filter(Number.isFinite).sort((a,b)=>a-b);return{count:v.length,p50:v[Math.floor(v.length*.5)],p95:v[Math.floor(v.length*.95)],max:v.at(-1)};};
 const summary={label,main:execFileSync('git',['rev-parse','main'],{encoding:'utf8'}).trim(),head:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),viewport:result.viewport,isolated:result.isolated,
  phases:Object.fromEntries(Object.entries(groups).map(([phase,r])=>[phase,{frame:stats(r.map(x=>x.frame)),age:stats(r.map(x=>x.age)),step:stats(r.map(x=>x.stepMs)),bytes:stats(r.map(x=>x.bytes)),ticks:r.at(-1).tick-r[0].tick,heapGrowth:r.at(-1).heap-r[0].heap}])),
  keyCallbacks:stats(result.keys.map(k=>k.ms)),errors:result.errors};
 fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(summary,null,2));console.log(JSON.stringify(summary,null,2));
 await page.screenshot({path:path.join(out,'air.png')});
 if(!result.rows.length||result.rows.at(-1).tick===result.rows[0].tick)throw new Error('Physics never advanced');
} finally {
 clearInterval(repeatTimer);
 fs.writeFileSync(path.join(out,'STOP'),'');
 if(recorder?.exitCode===null&&recorder?.signalCode===null)await new Promise(resolve=>recorder.once('exit',resolve));
 await browser?.close();
 try{
  if(acquiredAt){
   execFileSync(air,['stop',task],{stdio:'inherit',timeout:60000});
   execFileSync('python3',['-c',"import sys; sys.path.insert(0,'scripts/dev'); import browser_budget as b; b.debit(sys.argv[1],sys.argv[3:],float(sys.argv[2]),1)",task,String((Date.now()-acquiredAt)/1000),'node','tools/perf/air-scenarios/physics-latency.mjs',label],{stdio:'inherit'});
  }
 }finally{for(const [file,content]of originals)fs.writeFileSync(file,content);}
}
