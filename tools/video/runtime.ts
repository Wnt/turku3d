/** Capture tools only: reuse the game's assets, physics and music without changing gameplay. */
import * as THREE from '../../game/node_modules/three/build/three.webgpu.js';
import { CarVisual } from '../../game/src/actors/car';
import { PhysicsClient } from '../../game/src/physics/client';
import { MusicSystem } from '../../game/src/audio/music';

const w = window as any;
export function curves(points: number[][], targets: number[][]) {
  const path = new THREE.CatmullRomCurve3(points.map(p => new THREE.Vector3(...p)), false, 'catmullrom', 0.35);
  const aim = new THREE.CatmullRomCurve3(targets.map(p => new THREE.Vector3(...p)), false, 'catmullrom', 0.35);
  return (u: number) => {
    const t = 0.15*u + 0.85*(u*u*(3-2*u));
    const p = path.getPoint(t), a = aim.getPoint(t), d = a.sub(p);
    return { E:23459500+p.x, N:6704500-p.z, H:p.y,
      yaw:Math.atan2(d.x,-d.z)*180/Math.PI, pitch:Math.atan2(d.y,Math.hypot(d.x,d.z))*180/Math.PI, fov:55 };
  };
}
export async function car(route: number[][], speed = 7) {
  const scene = w.__game.tiles.tiles.group.parent;
  const visual = new CarVisual(scene);
  await visual.load('/assets/car/sedan.glb');
  const physics = new PhysicsClient();
  await physics.ready;
  const manifest = await (await fetch('/tiles/manifest.json')).json();
  physics.setKnown(Object.keys(manifest.collision), []);
  const ids = Object.keys(manifest.collision).filter(id => {
    const [,i,j] = id.split('_').map(Number);
    return route.some(p => Math.hypot(i*62.5+31.25-p[0], j*62.5+31.25-p[2]) < 150);
  });
  for (const id of ids) {
    const item = manifest.collision[id];
    await physics.loadTile(id, await (await fetch('/tiles/'+item.url)).arrayBuffer());
  }
  physics.step(1/30,{vx:0,vz:0,jump:false},null);
  await physics.stats();
  const start=route[0], next=route[1];
  const hit=await physics.raycast({x:start[0],y:60,z:start[2]}, {x:0,y:-1,z:0},100);
  if (!hit) throw new Error('No collision at scripted drive start');
  const yaw=-Math.atan2(next[2]-start[2],next[0]-start[0]);
  physics.spawnVehicle({x:start[0],y:hit.point.y+0.9,z:start[2]},yaw);
  physics.driveVehicle('player');
  const step=async (input: any) => {
    physics.step(1/30,{vx:0,vz:0,jump:false},input);
    await physics.stats();
    const s=physics.latest!.vehicle!;
    visual.update(s);
    return s;
  };
  for(let i=0;i<90;i++) await step({throttle:0,steer:0,brake:0,handbrake:true});
  let target=1;
  return async () => {
    const s=physics.latest!.vehicle!;
    while(target<route.length-1 && Math.hypot(route[target][0]-s.pos.x,route[target][2]-s.pos.z)<8) target++;
    const to=route[target];
    const forward=new THREE.Vector3(1,0,0).applyQuaternion(new THREE.Quaternion(s.rot.x,s.rot.y,s.rot.z,s.rot.w));
    const desired=Math.atan2(-(to[2]-s.pos.z),to[0]-s.pos.x);
    const heading=Math.atan2(-forward.z,forward.x);
    const error=Math.atan2(Math.sin(desired-heading),Math.cos(desired-heading));
    return step({throttle:s.speed<speed ? 0.33 : 0,steer:Math.max(-0.45,Math.min(0.45,-error*1.7)),brake:s.speed>speed+1 ? 0.2 : 0,handbrake:false});
  };
}
export async function music(seconds: number) {
  const context = new OfflineAudioContext(2, Math.ceil(seconds*48000), 48000);
  let now=0;
  const clock = new Proxy(context, {get(target, key) {
    if(key==='currentTime')return now;
    if(key==='state')return 'running';
    if(key==='resume')return () => Promise.resolve();
    const value=Reflect.get(target,key,target);
    return typeof value==='function' ? value.bind(target) : value;
  }});
  const engine=new MusicSystem(clock as unknown as AudioContext);
  engine.selectTrack(4); engine.start(); engine.stop();
  for(now=0;now<seconds;now+=0.05)engine.tick();
  const rendered=await context.startRendering();
  const size=rendered.length*4;
  const bytes=new Uint8Array(44+size), view=new DataView(bytes.buffer);
  const str=(offset:number,value:string)=>[...value].forEach((c,i)=>view.setUint8(offset+i,c.charCodeAt(0)));
  str(0,'RIFF');view.setUint32(4,size+36,true);str(8,'WAVE');str(12,'fmt ');view.setUint32(16,16,true);
  view.setUint16(20,1,true);view.setUint16(22,2,true);view.setUint32(24,48000,true);view.setUint32(28,192000,true);
  view.setUint16(32,4,true);view.setUint16(34,16,true);str(36,'data');view.setUint32(40,size,true);
  const left=rendered.getChannelData(0),right=rendered.getChannelData(1);
  for(let i=0;i<rendered.length;i++){view.setInt16(44+i*4,Math.max(-32767,Math.min(32767,left[i]*32767)),true);view.setInt16(46+i*4,Math.max(-32767,Math.min(32767,right[i]*32767)),true);}
  let binary='';for(let i=0;i<bytes.length;i+=16384)binary+=String.fromCharCode(...bytes.subarray(i,i+16384));
  return btoa(binary);
}
