import {TyreModel} from './tyre-core.js';
import assert from 'node:assert/strict';
const sampleRate=48000,block=new Float32Array(128);
function measure(params){
 const synth=new TyreModel(sampleRate);synth.params=params;let energy=0,count=0,peak=0;
 for(let b=0;b<750;b++){
  synth.render(block);
  if(b>375)for(const value of block){energy+=value*value;count++;peak=Math.max(peak,Math.abs(value));assert(Number.isFinite(value));}
 }
 return {rms:Math.sqrt(energy/count),peak};
}
const base={slip:.6,load:1,speed:55,surface:0};
const silent=measure({...base,speed:0}),zeroLoad=measure({...base,load:0});
const light=measure({...base,load:.4}),heavy=measure({...base,load:1.6}),gravel=measure({...base,surface:2});
assert(silent.peak<1e-8,'stationary tyres must be silent after settling');
assert(zeroLoad.peak<1e-6,'unloaded tyres must be silent after settling');
assert(heavy.rms>light.rms*1.7,'load must increase sustained energy');
assert(gravel.peak<1,'surface transitions must retain headroom');
const start=performance.now(),synth=new TyreModel(sampleRate);
for(let i=0;i<sampleRate*30/128;i++)synth.render(block);
const elapsed=performance.now()-start;
console.log(JSON.stringify({status:'PASS',silent,zeroLoad,light,heavy,gravel,render30SecondsMs:elapsed,realTimeRatio:30000/elapsed,benchmarkNote:'Single voice in Node on this Mac; not a game-frame measurement.'},null,2));
