import {mkdirSync,writeFileSync} from 'node:fs';
import {TyreModel} from './tyre-core.js';
const work=process.env.SOUND_WORK||`${process.env.HOME}/turku3d/loops/sounds-astra`;
mkdirSync(`${work}/work/tyres`,{recursive:true});
const scenarios={
 't02-chirp':{seconds:2,controls:t=>({slip:t>.45&&t<.78?.85:0,load:1,speed:35,surface:0})},
 't03-drift-light':{seconds:13,controls:t=>({slip:.22+.035*Math.sin(t*1.7),load:.7,speed:45,surface:0})},
 't04-drift-heavy':{seconds:13,controls:t=>({slip:.67+.12*Math.sin(t*.81)+.04*Math.sin(t*3.1),load:1.35+.12*Math.sin(t*.59),speed:70,surface:0})},
 't05-slip-load-sweep':{seconds:24,controls:t=>({slip:t<12?Math.sin(Math.PI*t/12)**2:.55,load:t<12?1:.2+1.6*Math.sin(Math.PI*(t-12)/12)**2,speed:55,surface:0})},
 't06-surface-sweep':{seconds:24,controls:t=>({slip:.55,load:1,speed:55,surface:t<8?0:t<16?1:2})},
};
for(const [id,s] of Object.entries(scenarios)){
 const synth=new TyreModel(48000),out=new Float32Array(48000*s.seconds),block=new Float32Array(128);
 for(let i=0;i<out.length;i+=128){synth.params=s.controls(i/48000);synth.render(block);out.set(block.subarray(0,Math.min(128,out.length-i)),i);}
 writeFileSync(`${work}/work/tyres/${id}.f32`,Buffer.from(out.buffer));console.log(id);
}
