#!/usr/bin/env node
// Motion proof for trailer clips: the hero car's track on screen must be smooth (Jonni 2026-11-11: "the car jerks to a
// different position frame by frame"). Two independent measurements of the same thing:
//  - the rendered track: the hero's screen position per frame, read from the rendered scene by the capture rig;
//  - the pixel track: the picture at that position, matched between consecutive frames of the encoded clip.
// Both are judged by their residual from a smooth path. Positions are pixels at 1920x1080.
//   node tools/video/trailer-motion.mjs clips/D1/s2.mp4 ...     re-check captured clips from their sidecars
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';

// Limits in px at 1920 (2026-11-11): a smooth replay gives a rendered track residual of 0.05-0.3 px rms and the jerking
// drive shots 3.5-26 px. The pixel track adds matching noise with outliers where shadows sweep over the car, so it is
// judged by the median residual: a jerking replay moves every frame, a shadow only a few.
export const LIMITS={rendered:{rms:1,max:4},pixels:{median:1.5},minFound:.5};

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
 let sx=0,sy=0,max=0,worst=-1,count=0;const all=[];
 for(let i=0;i<points.length;i++){
  if(!points[i])continue;const fx=fitAt(xs,i,half),fy=fitAt(ys,i,half);if(fx==null||fy==null)continue;
  const dx=xs[i]-fx,dy=ys[i]-fy,d=Math.hypot(dx,dy);sx+=dx*dx;sy+=dy*dy;count++;all.push(d);if(d>max){max=d;worst=i;}
 }
 if(found.length<12||!count)return{frames:points.length,found:found.length};
 const fx=found.map(p=>p[0]),fy=found.map(p=>p[1]),r=x=>+x.toFixed(2);
 return{frames:points.length,found:found.length,rms:r(Math.sqrt((sx+sy)/count)),rmsX:r(Math.sqrt(sx/count)),rmsY:r(Math.sqrt(sy/count)),median:r(all.sort((a,b)=>a-b)[all.length>>1]),max:r(max),worstFrame:worst,
  medianRmsX:r(std(medianResidual(fx))),medianRmsY:r(std(medianResidual(fy))),maxJumpX:r(Math.max(...fx.slice(1).map((x,i)=>Math.abs(x-fx[i]))))};
}
/**
 * The subject's track measured on the encoded pixels, in pixels at 1920x1080. `around` holds the rig's rendered
 * positions per frame; a crop of the picture at that position (10 % x 8 % of the frame: inside the car body) is
 * matched between consecutive frames, and the measured picture displacement is accumulated into a track. A flat crop
 * (a black frame, a missing subject) or a match at the edge of the search range gives no position for that frame.
 */
export function pixelTrack(file,around,{start,duration,width=960,height=540,range=12}={}){
 return new Promise((resolve,reject)=>{
  const args=['-n','19','ffmpeg','-v','error','-threads','2',...(start!=null?['-ss',String(start)]:[]),...(duration!=null?['-t',String(duration)]:[]),'-i',file,
   '-vf',`scale=${width}:${height}`,'-pix_fmt','gray','-f','rawvideo','-'];
  const p=spawn('nice',args,{stdio:['ignore','pipe','inherit']}),size=width*height,track=[],k=1920/width,cw=Math.round(width*.1),ch=Math.round(height*.08);
  let rest=Buffer.alloc(0),prev=null;
  const corner=c=>[Math.round(c[0]/k-cw/2),Math.round(c[1]/k-ch/2)];
  const one=cur=>{
   const i=track.length,c=around[i],a=around[i-1];let at=null;
   if(c&&c[0]>=0&&c[0]<1920&&c[1]>=0&&c[1]<1080){
    at=[c[0],c[1]];// a new segment starts on the rendered position
    if(prev&&a&&track[i-1]){
     const [ax,ay]=corner(a),[bx,by]=corner(c);at=null;
     if(ax>=0&&ay>=0&&ax+cw<=width&&ay+ch<=height&&bx-range>=0&&by-range>=0&&bx+cw+range<=width&&by+ch+range<=height){
      let m=0,q=0;for(let y=0;y<ch;y++)for(let x=0;x<cw;x++){const v=prev[(ay+y)*width+ax+x];m+=v;q+=v*v;}
      if(q/(cw*ch)-(m/(cw*ch))**2>=16){
       const side=2*range+1,sad=new Float64Array(side*side);let best=0;
       for(let dy=-range;dy<=range;dy++)for(let dx=-range;dx<=range;dx++){
        let e=0;for(let y=0;y<ch;y++){let o=(ay+y)*width+ax,n=(by+dy+y)*width+bx+dx;for(let x=0;x<cw;x++)e+=Math.abs(prev[o+x]-cur[n+x]);}
        const j=(dy+range)*side+dx+range;sad[j]=e;if(e<sad[best])best=j;
       }
       const dx=best%side-range,dy=Math.floor(best/side)-range;
       if(Math.abs(dx)<range&&Math.abs(dy)<range){
        // Parabola through the neighbours of the best match: sub-pixel displacement.
        const sub=(l,c0,r)=>{const d=l-2*c0+r;return d>0?(l-r)/(2*d):0;},fx=sub(sad[best-1],sad[best],sad[best+1]),fy=sub(sad[best-side],sad[best],sad[best+side]);
        at=[track[i-1][0]+(bx-ax+dx+fx)*k,track[i-1][1]+(by-ay+dy+fy)*k];
       }
      }
     }
    }
   }
   track.push(at);prev=Buffer.from(cur);
  };
  p.stdout.on('data',d=>{rest=rest.length?Buffer.concat([rest,d]):d;let o=0;for(;rest.length-o>=size;o+=size)one(rest.subarray(o,o+size));rest=rest.subarray(o);});
  p.on('error',reject);p.on('close',code=>code?reject(Error(`ffmpeg exit ${code} on ${file}`)):resolve(track));
 });
}
/** Verdict for one clip: `rendered` = the rig's per-frame subject positions, `pixels` = pixelTrack of the encoded file around them. */
export function motionVerdict(rendered,pixels){
 const a=trackResidual(rendered),b=trackResidual(pixels),reasons=[];
 if(a.rms==null)reasons.push(`subject rendered in only ${a.found} of ${a.frames} frames`);
 else if(a.rms>LIMITS.rendered.rms||a.max>LIMITS.rendered.max)reasons.push(`rendered track residual rms ${a.rms} max ${a.max} px (limits ${LIMITS.rendered.rms}/${LIMITS.rendered.max})`);
 if(b.median!=null&&b.median>LIMITS.pixels.median)reasons.push(`pixel track residual median ${b.median} px, rms ${b.rms} (limit ${LIMITS.pixels.median})`);
 // When the rig says the subject is in frame, the pixels must show it there.
 const inFrame=rendered.filter(p=>p&&p[0]>=0&&p[0]<1920&&p[1]>=0&&p[1]<1080).length;
 if(inFrame>=12&&b.found<inFrame*LIMITS.minFound)reasons.push(`subject in frame for ${inFrame} frames but matched in the pixels of only ${b.found}`);
 // Agreement of the two tracks: how far the picture drifts from the rendered positions over the clip.
 let drift=0;for(let i=0;i<pixels.length;i++)if(pixels[i]&&rendered[i])drift=Math.max(drift,Math.hypot(pixels[i][0]-rendered[i][0],pixels[i][1]-rendered[i][1]));
 return{pass:reasons.length===0,reasons,limits:LIMITS,rendered:a,pixels:{...b,maxDriftFromRendered:+drift.toFixed(2)}};
}
if(import.meta.url===pathToFileURL(process.argv[1]??'').href){
 // Re-check captured clips from their sidecars: node trailer-motion.mjs [--write] clips/D1/s2.mp4 ...
 // (--write stores the verdict in the sidecar and updates its `pass`).
 const write=process.argv.includes('--write');
 for(const file of process.argv.slice(2).filter(a=>a!=='--write')){
  const path=file.replace(/\.mp4$/,'.json'),side=fs.existsSync(path)?JSON.parse(fs.readFileSync(path)):null,track=side?.motion?.track;
  if(!track){console.log(`${file}: no rendered track in the sidecar (captured before 2026-11-11 14:25)`);continue;}
  const v=motionVerdict(track,await pixelTrack(file,track));
  console.log(`${v.pass?'pass':'FAIL'} ${file.split('/').slice(-2).join('/')}: rendered rms ${v.rendered.rms} max ${v.rendered.max} px | pixels median ${v.pixels.median} rms ${v.pixels.rms} max ${v.pixels.max} px in ${v.pixels.found}/${v.pixels.frames} frames, drift from rendered ${v.pixels.maxDriftFromRendered} px${v.reasons.length?' | '+v.reasons.join('; '):''}`);
  if(write){
   const c=side.cadence;side.motion={...v,subject:side.motion.subject,track};
   side.pass=v.pass&&c.exactDuplicateCaptures===0&&c.freezes===0&&Number(c.stream?.nb_read_frames)===side.frames&&(side.minCameraClearance==null||side.minCameraClearance>=(side.shot.minClear??.2))
    &&(side.tier==='draft'||((side.readiness?.framesResettled??0)===0&&(side.unsettledStations??[]).length===0));
   fs.writeFileSync(path,JSON.stringify(side,null,1));
  }
 }
}
