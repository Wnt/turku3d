// Replays recorded real-physics takes (trailer-record.mjs) at exact clip times, with optional placement transforms
// for several cars from one performance, and gives the camera an anchor that follows a car.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
const takesDir=process.env.TRAILER_TAKES??path.join(os.homedir(),'turku3d/video/2026-11-11-trailer/takes');
const cache=new Map(),clocks=new Map();
/**
 * Puts a take on the physics clock. The recorder stamped every sample with its own wall-clock frame time but stored the
 * physics worker's latest state, which only changes on whole 1/60 s ticks: stamp and pose disagree by up to ~40 ms
 * (0.5-0.9 m at 60-90 km/h), so a replay on the recorded stamps moves stop-go. The ticks each sample advanced follow
 * from the take itself (distance moved / speed); repeated states are dropped. The new clock keeps the take's mean
 * offset, so car times in the shot definitions stay valid.
 */
export function retime(samples,tick=1/60){
 const speed=v=>Math.hypot(v.linvel?.x??0,v.linvel?.y??0,v.linvel?.z??0),kept=[{ticks:0,wall:samples[0].t,v:samples[0].v}];
 let ticks=0,dropped=0,fallback=0;
 for(let i=1;i<samples.length;i++){
  const a=samples[i-1],b=samples[i],wall=Math.max(1,Math.round((b.t-a.t)/tick)),v=(speed(a.v)+speed(b.v))/2;
  let n=wall;
  // Slow or ambiguous steps (a reset, a collision) keep the wall-clock step: their position error is centimetres.
  if(v>2){const e=Math.hypot(b.v.pos.x-a.v.pos.x,b.v.pos.y-a.v.pos.y,b.v.pos.z-a.v.pos.z)/v/tick,r=Math.round(e);if(Math.abs(e-r)<.25&&Math.abs(r-wall)<=3)n=r;else fallback++;}
  if(n===0){dropped++;continue;}
  ticks+=n;kept.push({ticks,wall:b.t,v:b.v});
 }
 const offset=kept.reduce((s,k)=>s+k.wall-k.ticks*tick,0)/kept.length,out=kept.map(k=>({t:offset+k.ticks*tick,v:k.v}));
 const error=kept.map(k=>k.wall-offset-k.ticks*tick);
 return{samples:out,clock:{tick,samples:samples.length,dropped,fallback,physicsSeconds:ticks*tick,wallSeconds:samples.at(-1).t-samples[0].t,
  stampErrorMs:{rms:+(Math.sqrt(error.reduce((s,e)=>s+e*e,0)/error.length)*1e3).toFixed(1),max:+(Math.max(...error.map(Math.abs))*1e3).toFixed(1)}}};
}
export function loadTake(id){
 if(!cache.has(id)){const r=retime(JSON.parse(fs.readFileSync(path.join(takesDir,`${id}.json`))).samples);cache.set(id,r.samples);clocks.set(id,r.clock);}
 return cache.get(id);
}
/** How a take's recorded time stamps related to its physics clock (for the sidecar). */
export const takeClock=id=>(loadTake(id),clocks.get(id));
const lerp=(a,b,f)=>a+(b-a)*f,v3=(a,b,f)=>({x:lerp(a.x,b.x,f),y:lerp(a.y,b.y,f),z:lerp(a.z,b.z,f)});
function slerp(a,b,f){
 let d=a.x*b.x+a.y*b.y+a.z*b.z+a.w*b.w;const s=d<0?-1:1;d=Math.abs(d);
 let ka=1-f,kb=f;if(d<.9995){const th=Math.acos(d),sn=Math.sin(th);ka=Math.sin((1-f)*th)/sn;kb=Math.sin(f*th)/sn;}
 const q={x:ka*a.x+kb*s*b.x,y:ka*a.y+kb*s*b.y,z:ka*a.z+kb*s*b.z,w:ka*a.w+kb*s*b.w},n=Math.hypot(q.x,q.y,q.z,q.w);
 return{x:q.x/n,y:q.y/n,z:q.z/n,w:q.w/n};
}
function sampleAt(samples,t){
 if(t<=samples[0].t)return structuredClone(samples[0].v);
 if(t>=samples.at(-1).t)return structuredClone(samples.at(-1).v);
 let lo=0,hi=samples.length-1;while(hi-lo>1){const m=(lo+hi)>>1;if(samples[m].t<=t)lo=m;else hi=m;}
 const a=samples[lo],b=samples[hi],f=(t-a.t)/(b.t-a.t),v=structuredClone(a.v);
 v.pos=v3(a.v.pos,b.v.pos,f);v.rot=slerp(a.v.rot,b.v.rot,f);v.speed=lerp(a.v.speed??0,b.v.speed??0,f);
 v.wheels=a.v.wheels.map((w,i)=>{const o=b.v.wheels[i];return{...w,pos:v3(w.pos,o.pos,f),steer:lerp(w.steer,o.steer,f),rotation:lerp(w.rotation,o.rotation,f),suspension:lerp(w.suspension,o.suspension,f),
  tyre:w.tyre&&o.tyre?{...w.tyre,point:v3(w.tyre.point,o.tyre.point,f)}:w.tyre};});
 return v;
}
const yawQuat=a=>({x:0,y:Math.sin(a/2),z:0,w:Math.cos(a/2)});
const mul=(a,b)=>({x:a.w*b.x+a.x*b.w+a.y*b.z-a.z*b.y,y:a.w*b.y-a.x*b.z+a.y*b.w+a.z*b.x,z:a.w*b.z+a.x*b.y-a.y*b.x+a.z*b.w,w:a.w*b.w-a.x*b.x-a.y*b.y-a.z*b.z});
/** A car's state at clip time (seconds). `car`: {take,t0,speed,rotate,pivot:[E,N],move:[dE,dN],lights,damage}.
 *  The recorded crash damage is dropped (a clean body) unless `car.damage` is true. */
export function carAt(car,seconds){
 const samples=loadTake(car.take),v=sampleAt(samples,(car.t0??0)+seconds*(car.rate??1));
 if(car.rotate||car.move){
  // Rotate about a vertical axis through the pivot (compass degrees clockwise), then translate.
  const a=-(car.rotate??0)*Math.PI/180,c=Math.cos(a),s=Math.sin(a),px=(car.pivot?.[0]??23459500)-23459500,pz=6704500-(car.pivot?.[1]??6704500);
  const dx=car.move?.[0]??0,dz=-(car.move?.[1]??0);
  const point=p=>{const x=p.x-px,z=p.z-pz;return{x:px+x*c+z*s+dx,y:p.y,z:pz-x*s+z*c+dz};};
  // Height on the new spot: `slope` [dH per m east, dH per m north] fitted to the take's own ground (preferred),
  // else the page corrects by the streamed ground under both spots (v.moved).
  const old=v.pos;v.pos=point(old);v.rot=mul(yawQuat(a),v.rot);
  if(car.slope)v.pos.y=old.y+car.slope[0]*(v.pos.x-old.x)-car.slope[1]*(v.pos.z-old.z);else v.moved={from:{x:old.x,z:old.z}};
 }
 if(car.lights!=null)v.lights=car.lights;
 if(!car.damage)delete v.damage;
 return v;
}
/** Camera anchor on a car: its position (box-filtered over `smooth` s) and travel heading (compass degrees). */
export function anchorAt(car,seconds,follow={}){
 const smooth=follow.smooth??.25,n=5;let x=0,y=0,z=0;
 for(let k=0;k<n;k++){const p=carAt(car,seconds+(k/(n-1)-.5)*2*smooth).pos;x+=p.x/n;y+=p.y/n;z+=p.z/n;}
 const span=follow.headingSpan??.5,a=carAt(car,seconds-span).pos,b=carAt(car,seconds+span).pos;
 let heading;
 if(Math.hypot(b.x-a.x,b.z-a.z)>.5&&follow.heading!=='body')heading=Math.atan2(b.x-a.x,-(b.z-a.z));
 else{const q=carAt(car,seconds).rot,fx=1-2*(q.y*q.y+q.z*q.z),fz=2*(q.x*q.z-q.w*q.y);heading=Math.atan2(fx,-fz);}
 return{E:x+23459500,N:6704500-z,H:y,heading:heading*180/Math.PI};
}
