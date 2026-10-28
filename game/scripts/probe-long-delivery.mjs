// Manual physical delivery diagnostic. Source sandbox.env, start the local game/drafts, then run
// python3 scripts/dev/host_slots.py browser -- node game/scripts/probe-long-delivery.mjs
// Seeds a migrated 40-order save; uses real shop UI, physics/input and collision streaming.
// Auto-steering is diagnostic assistance: report obstacles and cancellations, never call it a clean player drive.
import fs from 'node:fs';
const repo = new URL('../..', import.meta.url).pathname.replace(/\/$/, '');
const {chromium}=await import(repo+'/game/node_modules/@playwright/test/index.mjs');
const {chromeExecutable,chromeArgs,chromeIgnoreDefaultArgs}=await import(repo+'/tools/dev/chrome-args.mjs');
const {isolateBrowser}=await import(repo+'/tools/dev/environment.mjs');
isolateBrowser();
const browser=await chromium.launch({executablePath:chromeExecutable(),headless:true,ignoreDefaultArgs:chromeIgnoreDefaultArgs,args:chromeArgs(['--enable-unsafe-webgpu','--use-angle=metal','--ignore-gpu-blocklist'])});
try {
const page=await browser.newPage({viewport:{width:1100,height:700}});
page.on('response',r=>{if(r.status()>=400 && !r.url().includes('/coarse/want'))console.log('HTTP',r.status(),r.url());});
page.on('pageerror',e=>console.log('PAGEERROR',e.message));
page.on('console',m=>{if(m.type()==='error')0;});
await page.addInitScript(()=>localStorage.setItem('turku3d.missions',JSON.stringify({version:3,phase:'at_pizzeria',money:1000,permit:10,plates:false,kidney:false,warning:false,vehicle:'car',delivery:40,pizzas:40,seed:Number(new URLSearchParams(location.search).get('probeSeed') ?? 24),delivered:[],order:[],lastAddress:null,stop:0,elapsed:0,par:0,firstPar:0})));
await page.goto('http://localhost:'+process.env.TURKU3D_PORT_BASE+'/?backend=webgpu&quality=low&probeSeed=24');
await page.waitForFunction(()=>window.__game?.ready,{},{timeout:180000}).catch(async e=>{console.log('TIMEOUT',await page.locator('body').innerText()); await page.screenshot({path:process.env.TURKU3D_WORK+'/reports/long-timeout.png'});throw e;});
console.log('READY');
await page.screenshot({path:process.env.TURKU3D_WORK+'/reports/long-start.png'});
await page.getByRole('option',{name:/^Continue/}).click();
await page.waitForFunction(()=>!window.__game.state().loading && !window.__game.state().startScreen,{},{timeout:180000});
console.log('PLAYING');
await page.screenshot({path:process.env.TURKU3D_WORK+'/reports/long-playing.png'});

await page.evaluate(async()=>{
 const g=window.__game, p={x:77.1,z:187.6};
 const h=await g.physics.raycast({x:p.x,y:40,z:p.z},{x:0,y:-1,z:0},80);
 if(!h)throw Error('pizzeria ground missing');
 g.physics.setCharacterEnabled(true,{x:p.x,y:h.point.y+.1,z:p.z});
});
await page.waitForTimeout(1000);
await page.keyboard.press('KeyE');
await page.waitForTimeout(500);
console.log('SHOP',await page.locator('#shop-menu').innerText());
await page.locator('#shop-menu .shop-option').filter({hasText:'Long-range delivery'}).click();
await page.keyboard.press('Enter');
await page.waitForFunction(()=>window.__game.missions.phase==='carrying',{},{timeout:10000});
console.log('ORDER',JSON.stringify(await page.evaluate(()=>window.__game.missions.snapshot())));
await page.screenshot({path:process.env.TURKU3D_WORK+'/reports/long-pickup.png'});
await page.evaluate(async()=>{
 const g=window.__game, v=g.state().vehicle, p={x:v.pos[0],z:v.pos[2]+2};
 const h=await g.physics.raycast({x:p.x,y:40,z:p.z},{x:0,y:-1,z:0},80);
 g.physics.setCharacterEnabled(true,{x:p.x,y:h.point.y+.1,z:p.z});
});
await page.waitForTimeout(1000);await page.keyboard.press('KeyF');
await page.waitForFunction(()=>window.__game.state().mode==='car',{},{timeout:5000});
await page.evaluate(()=>{
 const g=window.__game; const original=g.input.update.bind(g.input); let stuck=0,reverse=0,parking=false;
 g.driveEvidence={samples:[],start:g.state().feet,travelled:0};
 g.input.update=function(dt){
  original(dt);const s=g.state(),r=g.minimap.routes.route;
  if(g.missions.phase!=='carrying'||!r?.length)return;
  if(s.mode==='foot'){
   if(!parking)return;
   const t=g.missions.currentStop.point,dx=t.x-s.feet[0],dz=t.z-s.feet[2],d=Math.hypot(dx,dz),b=g.orbit.basis();
   this.actions.moveY=d>1.7?(dx*b.fx+dz*b.fz)/d:0;this.actions.moveX=d>1.7?(dx*b.rx+dz*b.rz)/d:0;
   if(d<2.7)this.edges.add('mission');return;
  }
  if(g.minimap.routes.length<28)parking=true;
  if(parking){this.actions.throttle=0;this.actions.brake=1;
   if(Math.abs(s.vehicle.forwardSpeed)<.4)this.edges.add('interact');return;}

  let goal=r[r.length-1],remaining=10+Math.abs(s.vehicle.forwardSpeed)*.45;
  for(let i=1;i<r.length;i++){
   const a=r[i-1],b=r[i],len=Math.hypot(b.x-a.x,b.z-a.z);
   if(remaining<=len){goal={x:a.x+(b.x-a.x)*remaining/len,z:a.z+(b.z-a.z)*remaining/len};break;}
   remaining-=len;
  }
  const q=s.vehicle.rot;const yaw=Math.atan2(2*(q.w*q.y+q.x*q.z),1-2*(q.y*q.y+q.z*q.z));
  const desired=Math.atan2(-(goal.z-s.feet[2]),goal.x-s.feet[0]);
  const error=Math.atan2(Math.sin(desired-yaw),Math.cos(desired-yaw));
  const speed=Math.abs(s.vehicle.forwardSpeed); let at=0,corner=false;
  for(let i=1;i+1<r.length;i++){
   at+=Math.hypot(r[i].x-r[i-1].x,r[i].z-r[i-1].z);if(at>35)break;
   const a=Math.atan2(r[i].z-r[i-1].z,r[i].x-r[i-1].x),b=Math.atan2(r[i+1].z-r[i].z,r[i+1].x-r[i].x);
   if(Math.abs(Math.atan2(Math.sin(b-a),Math.cos(b-a)))>.5){corner=true;break;}
  }
  const target=corner||Math.abs(error)>.5?4:Math.abs(error)>.2?7:10;
  this.actions.steer=Math.max(-1,Math.min(1,-error*3));this.actions.throttle=speed<target?0.8:0;
  this.actions.brake=speed>target+1?.4:0;
  stuck=speed<.5?stuck+dt:0;if(stuck>5){reverse=3;stuck=0;}
  if(reverse>0){reverse-=dt;this.actions.throttle=0;this.actions.brake=.6;this.actions.steer=-.3;}
  const e=g.driveEvidence,last=e.last;if(last)e.travelled+=Math.hypot(last[0]-s.feet[0],last[2]-s.feet[2]);e.last=s.feet;
 };
});
for(let i=0;i<100;i++){
 await page.waitForTimeout(5000);
 const s=await page.evaluate(()=>{const g=window.__game,s=g.state();return {feet:s.feet,speed:s.vehicle?.forwardSpeed,
 phase:g.missions.phase,timer:s.missions.elapsed,route:g.minimap.routes.length,travelled:g.driveEvidence.travelled,
 recovery:s.recovery,draft:g.coarse.status};});
 console.log('DRIVE',JSON.stringify(s));
 if(s.phase!=='carrying')break;

}
await page.screenshot({path:process.env.TURKU3D_WORK+'/reports/long-drive.png'});
const result=await page.evaluate(()=>({state:window.__game.state(),evidence:window.__game.driveEvidence}));
console.log('END',JSON.stringify(result));
if(result.state.missions.phase!=='delivered')process.exitCode=1;
} finally { await browser.close(); }
