#!/usr/bin/env node
// Motion proof for trailer clips: the hero car's track on screen must be smooth (Jonni 2026-11-11: "the car jerks to a
// different position frame by frame"). Two independent measurements of the same thing:
//  - the rendered track: the hero's screen position per frame, read from the rendered scene by the capture rig;
//  - the pixel track: the yellow hero's centroid per frame, measured on the encoded clip.
// Both are judged by their residual from a smooth path. Positions are pixels at 1920x1080.
//   node tools/video/trailer-motion.mjs clip.mp4[@start@duration] ...     pixel check of existing clips
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';

// Limits, from the clean baseline on 2026-11-11 (H1/v1 and the parked W1/v1 measure 1.7-2.0 px on the pixel track;
// the jerking drive shots 11-44 px). The rendered track has no measurement noise, so its limit is tighter.
export const LIMITS={rendered:{rms:1,max:4},pixels:{medianRms:4},minFound:.5};

const std=v=>{const m=v.reduce((s,x)=>s+x,0)/v.length;return Math.sqrt(v.reduce((s,x)=>s+(x-m)*(x-m),0)/v.length);};
// Residual from a moving median of k frames (edge padded): the measure the pixel baseline was taken with.
function medianResidual(v,k=9){
 const h=k>>1,pad=[...Array(h).fill(v[0]),...v,...Array(h).fill(v.at(-1))];
 return v.map((x,i)=>x-pad.slice(i,i+k).sort((a,b)=>a-b)[h]);
}
// Value at frame i of a least-squares parabola through the valid frames of a window around i.
function fitAt(v,i,half){
 const n=v.length,lo=Math.max(0,Math.min(i-half,n-1-2*half)),hi=Math.min(n-1,lo+2*half);
 let s0=0,s1=0,s2=0,s3=0,s4=0,t0=0,t1=0,t2=0;
 for(let j=lo;j<=hi;j++){if(v[j]==null)continue;const t=j-i,q=t*t;s0++;s1+=t;s2+=q;s3+=q*t;s4+=q*q;t0+=v[j];t1+=v[j]*t;t2+=v[j]*q;}
 if(s0<6)return null;
 const det=s0*(s2*s4-s3*s3)-s1*(s1*s4-s3*s2)+s2*(s1*s3-s2*s2);
 return det?(t0*(s2*s4-s3*s3)-s1*(t1*s4-s3*t2)+s2*(t1*s3-s2*t2))/det:null;
}
/** Residual of a per-frame screen track [[x,y]|null,...] from a smooth path (parabola over 15 frames, and the 9-frame median). */
export function trackResidual(points,half=7){
 const xs=points.map(p=>p?p[0]:null),ys=points.map(p=>p?p[1]:null),found=points.filter(Boolean);
 let sx=0,sy=0,max=0,worst=-1,count=0;
 for(let i=0;i<points.length;i++){
  if(!points[i])continue;const fx=fitAt(xs,i,half),fy=fitAt(ys,i,half);if(fx==null||fy==null)continue;
  const dx=xs[i]-fx,dy=ys[i]-fy,d=Math.hypot(dx,dy);sx+=dx*dx;sy+=dy*dy;count++;if(d>max){max=d;worst=i;}
 }
 if(found.length<12||!count)return{frames:points.length,found:found.length};
 const fx=found.map(p=>p[0]),fy=found.map(p=>p[1]),r=x=>+x.toFixed(2);
 return{frames:points.length,found:found.length,rms:r(Math.sqrt((sx+sy)/count)),rmsX:r(Math.sqrt(sx/count)),rmsY:r(Math.sqrt(sy/count)),max:r(max),worstFrame:worst,
  medianRmsX:r(std(medianResidual(fx))),medianRmsY:r(std(medianResidual(fy))),maxJumpX:r(Math.max(...fx.slice(1).map((x,i)=>Math.abs(x-fx[i]))))};
}
/** The yellow hero car's centroid per frame of an encoded clip, in pixels at 1920x1080 (null where it is not found). */
export function pixelTrack(file,{start,duration,width=640,height=360}={}){
 return new Promise((resolve,reject)=>{
  const args=['-n','19','ffmpeg','-v','error','-threads','2',...(start!=null?['-ss',String(start)]:[]),...(duration!=null?['-t',String(duration)]:[]),'-i',file,
   '-vf',`scale=${width}:${height}`,'-pix_fmt','rgb24','-f','rawvideo','-'];
  const p=spawn('nice',args,{stdio:['ignore','pipe','inherit']}),size=width*height*3,track=[],k=1920/width;let rest=Buffer.alloc(0);
  const one=b=>{
   let n=0,sx=0,sy=0;
   for(let y=0,o=0;y<height;y++)for(let x=0;x<width;x++,o+=3){const r=b[o],g=b[o+1],c=b[o+2];if(r>150&&g>110&&c<95&&r-c>80&&Math.abs(r-g)<70){n++;sx+=x;sy+=y;}}
   track.push(n<40?null:[sx/n*k,sy/n*k,n]);
  };
  p.stdout.on('data',d=>{rest=rest.length?Buffer.concat([rest,d]):d;let o=0;for(;rest.length-o>=size;o+=size)one(rest.subarray(o,o+size));rest=rest.subarray(o);});
  p.on('error',reject);p.on('close',code=>code?reject(Error(`ffmpeg exit ${code} on ${file}`)):resolve(track));
 });
}
/** Verdict for one clip: `rendered` = the rig's per-frame hero positions (or null), `pixels` = pixelTrack of the encoded file. */
export function motionVerdict(rendered,pixels,{subject=true}={}){
 const a=rendered?trackResidual(rendered):null,b=trackResidual(pixels),reasons=[];
 if(a?.rms!=null&&(a.rms>LIMITS.rendered.rms||a.max>LIMITS.rendered.max))reasons.push(`rendered hero track residual rms ${a.rms} max ${a.max} px (limits ${LIMITS.rendered.rms}/${LIMITS.rendered.max})`);
 if(b.medianRmsX!=null&&Math.max(b.medianRmsX,b.medianRmsY)>LIMITS.pixels.medianRms)reasons.push(`pixel hero track residual x ${b.medianRmsX} y ${b.medianRmsY} px (limit ${LIMITS.pixels.medianRms})`);
 // The hero is the subject of a follow shot: when the rig says it is in frame, the pixels must show it.
 const inFrame=rendered?rendered.filter(p=>p&&p[0]>0&&p[0]<1920&&p[1]>0&&p[1]<1080).length:0;
 if(subject&&inFrame>=12&&b.found<inFrame*LIMITS.minFound)reasons.push(`hero in frame for ${inFrame} frames but found in the pixels of only ${b.found}`);
 return{pass:reasons.length===0,reasons,limits:LIMITS,rendered:a,pixels:b};
}
if(import.meta.url===pathToFileURL(process.argv[1]??'').href){
 for(const spec of process.argv.slice(2)){
  const [file,start,duration]=spec.split('@');
  if(!fs.existsSync(file)){console.log(`${file}: missing`);continue;}
  const r=trackResidual(await pixelTrack(file,{start,duration}));
  const verdict=r.medianRmsX==null?'hero not found':Math.max(r.medianRmsX,r.medianRmsY)>LIMITS.pixels.medianRms?'JERKS':'smooth';
  console.log(`${verdict.padEnd(14)} ${file.split('/').slice(-2).join('/')}${start?` @${start}+${duration}`:''}: hero in ${r.found}/${r.frames} frames`+(r.medianRmsX==null?'':` | residual x ${r.medianRmsX} y ${r.medianRmsY} px (limit ${LIMITS.pixels.medianRms}) | fit rms ${r.rms} max ${r.max} | max jump x ${r.maxJumpX}`));
 }
}
