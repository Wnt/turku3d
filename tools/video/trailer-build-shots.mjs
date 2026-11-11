#!/usr/bin/env node
// Builds tools/video/trailer-shots.json (storyboard v7 after picture review 1: base shots and variants) and
// cloud/finals-queue.json. The Tori donut layout and the T1a lens position are computed from the recorded takes.
//   node tools/video/trailer-build-shots.mjs [--seed N]
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {carAt,loadTake} from './trailer-cars.mjs';
const here=path.dirname(new URL(import.meta.url).pathname),root=path.join(os.homedir(),'turku3d/video/2026-11-11-trailer');
const B=23459000,N0=6704000,k=(e,n,...r)=>[+(B+e).toFixed(2),+(N0+n).toFixed(2),...r];
const rad=d=>d*Math.PI/180,dir=b=>[Math.sin(rad(b)),Math.cos(rad(b))];
let seed=Number(process.argv.includes('--seed')?process.argv[process.argv.indexOf('--seed')+1]:7);
const rnd=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296;},gauss=()=>Math.sqrt(-2*Math.log(rnd()+1e-12))*Math.cos(2*Math.PI*rnd());
const HERO={model:'e30',color:'yellow'};

// ---- Tori donuts: six recorded donuts packed tightly, phases chosen so no two cars ever come close ----
const TAKES=['tori-0','tori-1','tori-2','tori-3','tori-6','tori-7'],T0=4,T1=16,DT=.1,STEPS=Math.round((T1-T0)/DT)+1;
const base=TAKES.map(take=>Array.from({length:STEPS},(_,i)=>{const p=carAt({take,t0:0},T0+i*DT).pos;return[p.x+500,500-p.z,p.y];}));
const centre0=base.map(s=>{const a=s.slice(Math.round(2/DT));return[a.reduce((x,p)=>x+p[0],0)/a.length,a.reduce((x,p)=>x+p[1],0)/a.length];});
// Ground plane of the square from the cars' own heights: y = a*E + b*N + c.
function plane(points){let s=Array.from({length:3},()=>[0,0,0,0]);for(const [e,n,y] of points){const r=[e,n,1];for(let i=0;i<3;i++){for(let j=0;j<3;j++)s[i][j]+=r[i]*r[j];s[i][3]+=r[i]*y;}}
 for(let i=0;i<3;i++){let p=i;for(let r=i+1;r<3;r++)if(Math.abs(s[r][i])>Math.abs(s[p][i]))p=r;[s[i],s[p]]=[s[p],s[i]];for(let r=0;r<3;r++)if(r!==i){const f=s[r][i]/s[i][i];for(let c=i;c<4;c++)s[r][c]-=f*s[i][c];}}
 return s.map((r,i)=>r[3]/r[i]);}
const tori=plane(base.flat()),toriGround=(e,n)=>tori[0]*e+tori[1]*n+tori[2]-.62;
const place=(i,[cx,cy,phi])=>base[i].map(([e,n])=>{const de=e-centre0[i][0],dn=n-centre0[i][1],c=Math.cos(phi),s=Math.sin(phi);return[cx+de*c+dn*s,cy-de*s+dn*c];});
const GAP=6.2;
function cost(state){
 const paths=state.map((s,i)=>place(i,s)),g=[state.reduce((a,s)=>a+s[0],0)/6,state.reduce((a,s)=>a+s[1],0)/6];let pen=0,r=0,min=1e9;
 for(let i=0;i<6;i++)for(const p of paths[i])r=Math.max(r,Math.hypot(p[0]-g[0],p[1]-g[1]));
 for(let i=0;i<6;i++)for(let j=i+1;j<6;j++)for(let t=0;t<STEPS;t++){const d=Math.hypot(paths[i][t][0]-paths[j][t][0],paths[i][t][1]-paths[j][t][1]);min=Math.min(min,d);if(d<GAP)pen+=(GAP-d)**2;}
 return{value:r+pen*40,r,min,g};
}
let best=null;
for(let restart=0;restart<6;restart++){
 let state=TAKES.map((_,i)=>[12*Math.sin(i*Math.PI/3)+gauss(),12*Math.cos(i*Math.PI/3)+gauss(),rnd()*2*Math.PI]),cur=cost(state);
 for(let it=0,n=14000;it<n;it++){
  const temp=1.5*(1-it/n)+.02,i=Math.floor(rnd()*6),next=state.map(s=>[...s]);
  if(rnd()<.6){next[i][0]+=gauss()*1.2;next[i][1]+=gauss()*1.2;}else next[i][2]+=gauss()*.35;
  const c=cost(next);if(c.value<cur.value||rnd()<Math.exp((cur.value-c.value)/temp)){state=next;cur=c;}
  if(cur.min>=GAP-.4&&(!best||cur.r<best.cost.r))best={state:state.map(s=>[...s]),cost:cur};
 }
}
if(!best)throw Error('no donut layout found');
// Cluster centre on the open, sunlit east half of the square, inside the area the takes drove over.
const G=[657.9,619.3],shift=[G[0]-best.cost.g[0],G[1]-best.cost.g[1]],layout=best.state.map(s=>[s[0]+shift[0],s[1]+shift[1],s[2]]);
const paths=layout.map((s,i)=>place(i,s)),R=best.cost.r,at=(i,t)=>paths[i][Math.max(0,Math.min(STEPS-1,Math.round((t-T0)/DT)))];
const slope=[+tori[0].toFixed(5),+tori[1].toFixed(5)];
// ---- T1a: lens on the cobbles, looking into the sun (azimuth 250 at 17.8), nearest pass 3-4 m ----
const SUN=250,handles=1.5;
let lens=null;
for(let tc=5.6;tc<=12.2;tc+=.1)for(let e=G[0]-R-4;e<=G[0]+R+8;e+=1)for(let n=G[1]-R-4;n<=G[1]+R+8;n+=1){
 let nearest=1e9,who=-1,when=0,clipMin=1e9,seen=0;
 for(let t=tc-handles;t<=tc+2.1+handles;t+=.1)for(let i=0;i<6;i++){
  const p=at(i,t),d=Math.hypot(p[0]-e,p[1]-n),bearing=Math.atan2(p[0]-e,p[1]-n)*180/Math.PI,off=Math.abs(((bearing-SUN+540)%360)-180);
  clipMin=Math.min(clipMin,d);
  if(t>=tc&&t<=tc+2.1){if(d<nearest&&off<50){nearest=d;who=i;when=t;}if(off<42&&d<45)seen++;}
 }
 if(clipMin<3.0||nearest<3.1||nearest>3.9)continue;
 const score=seen-8*Math.abs(when-(tc+.8));
 if(!lens||score>lens.score)lens={score,e,n,tc,who,when,nearest,clipMin};
}
if(!lens)throw Error('no T1a lens position found');
// The hero (yellow E30) is the car that passes the T1a lens.
const fleet=[['golf7','red'],['octavia-rs-2020','blue'],['carla-ford-mustang','white'],['v60','black'],['carla-mini-cooper-s-2021','silver']];
const six=t0=>TAKES.map((take,i)=>{const [model,color]=i===lens.who?[HERO.model,HERO.color]:fleet[i<lens.who?i:i-1];
 return{take,model,color,t0:+t0.toFixed(2),rotate:+(layout[i][2]*180/Math.PI).toFixed(2),pivot:[+(B+centre0[i][0]).toFixed(2),+(N0+centre0[i][1]).toFixed(2)],
  move:[+(layout[i][0]-centre0[i][0]).toFixed(2),+(layout[i][1]-centre0[i][1]).toFixed(2)],slope};});
const gG=toriGround(G[0],G[1]);
// T1b drone: from the south-east towards the Orthodox church, the nearest car about 25 m away.
const orbit=(height,from,to,nearest=22,pitch=11.1,fov=40)=>{const dist=R+nearest-2.5,aim=(height-.6)/Math.tan(rad(pitch)),keys=[],target=[];
 for(const b of [from,(from+to)/2,to]){const [s,c]=dir(b),e=G[0]+dist*s,n=G[1]+dist*c;keys.push(k(e,n,+(toriGround(e,n)+height).toFixed(2),0,0,fov));
  target.push(k(e-aim*s,n-aim*c,+(gG+.6).toFixed(2)));}
 return{keys,target};};
const b0=5.6,c0=b0+3.5;// T1b clip start in car time; C1 continues the same replay at the cut
const common={weather:'clear',traffic:0,sceneryRadius:2000,ease:'linear'};
const shots=[];
const add=(id,base,fields)=>shots.push({id,...(base?{variant:fields.variant}:{}),...fields});

// ---- P1 postcard (morning on purpose) ----
const p1=(h0,h1,pitch)=>[k(698,202,h0,52,pitch,30),k(724.5,216.1,h1,52,pitch,30)];
const P1={duration:2.6,hour:7.5,weather:'clear',traffic:40,sceneryRadius:4000,ease:'linear',keys:p1(15.5,14.5,-4),
 note:'Postcard: slow glide up the Aura at 15 m with a slight descent, 30 degree lens, cathedral tower on the skyline left of centre, sun 35 degrees out of frame to the right, starts past the black-hulled ship'};
add('P1',0,P1);
add('P1-11m',1,{...P1,keys:p1(11.5,10.8,-2.5),variant:'eye height 11 m'});
add('P1-dawn',1,{...P1,hour:6.8,variant:'hour 6.8 (sunrise)'});
add('P1-11m-dawn',1,{...P1,keys:p1(11.5,10.8,-2.5),hour:6.8,variant:'eye height 11 m, hour 6.8'});

// ---- H1 cathedral slide: the lens retreats in front of the sliding car and stops 5 m from where it comes to rest ----
const h1Samples=[];for(let t=2.5;t<=6;t+=.05){const p=carAt({take:'h1-d',t0:0},t).pos;h1Samples.push([p.x+500,500-p.z,p.y]);}
// One track gives the slope along the track only: least squares along its principal direction.
const hm=[0,1,2].map(i=>h1Samples.reduce((a,p)=>a+p[i],0)/h1Samples.length),hd=(()=>{const a=h1Samples[0],b=h1Samples.at(-1),l=Math.hypot(b[0]-a[0],b[1]-a[1]);return[(b[0]-a[0])/l,(b[1]-a[1])/l];})();
let sxx=0,sxy=0;for(const p of h1Samples){const s=(p[0]-hm[0])*hd[0]+(p[1]-hm[1])*hd[1];sxx+=s*s;sxy+=s*(p[2]-hm[2]);}
const h1Slope=[+(sxy/sxx*hd[0]).toFixed(5),+(sxy/sxx*hd[1]).toFixed(5)];
// Lens position relative to the car by car time: [distance ahead, bearing from the car, height offset, yaw, pitch, fov].
const H1T=[[2.8,10,298,-.57,100,16,70],[3.4,9,298,-.53,100,17,70],[3.8,6.5,296,-.43,99,19,68],[4.2,4.8,292,-.36,96,22,64],[4.6,4.5,285,-.35,93,24,64],[5,4.6,278,-.35,90,25,64],[5.4,4.8,274,-.36,88.5,26,64],[5.8,5,272,-.37,87.5,26,64],[9,5,272,-.37,87.5,26,64]];
const h1At=t=>{const c=Math.max(H1T[0][0],Math.min(H1T.at(-1)[0],t));let i=1;while(H1T[i][0]<c)i++;const a=H1T[i-1],b=H1T[i],f=(c-a[0])/(b[0]-a[0]);return a.map((v,j)=>v+(b[j]-v)*f).slice(1);};
const h1Keys=(t0,rate,edit=v=>v)=>Array.from({length:13},(_,i)=>{const [d,b,dh,yaw,pitch,fov]=edit(h1At(t0+rate*5.4*i/12)),[sn,cs]=dir(b);return[+(d*sn).toFixed(2),+(d*cs).toFixed(2),+dh.toFixed(2),+yaw.toFixed(1),+pitch.toFixed(1),+fov.toFixed(1)];});
const h1Car=(t0,rate)=>({take:'h1-d',...HERO,t0,rate,move:[7.4,-5.1],slope:h1Slope});
const off=(d,b,dh,yaw,pitch,fov)=>{const [s,c]=dir(b);return[+(d*s).toFixed(2),+(d*c).toFixed(2),dh,yaw,pitch,fov];};
const h1Note='Cathedral slide: lens 0.45 m above the cobbles, 4.5-5 m in front of the car through the slide, west tower above; real handbrake slide (68 km/h entry, 180 degrees); the first frame after the cut is car time 4.3 (car sideways, smoke); the take is shifted 9 m back along its own track so the car rests in sunlight. ';
const H1={duration:2.4,hour:18.2,...common,minClear:-.1,cars:[h1Car(2.8,1)],follow:{car:0,frame:'world',smooth:.3},keys:h1Keys(2.8,1),note:h1Note+'Real time: 1.9 s of slide, then the car rests in its smoke.'};
add('H1',0,H1);
add('H1-slow',1,{...H1,cars:[h1Car(3.4,.6)],keys:h1Keys(3.4,.6),note:h1Note,variant:'replay at 0.6x: 3.3 s of slide'});
add('H1-low',1,{...H1,minClear:-.3,keys:h1Keys(2.8,1,([d,b,dh,yaw,pitch])=>[d*.8,b,dh-.2,yaw,pitch-2,80]),variant:'lower (0.25 m) and closer (3.6-4 m), 80 degree lens'});
const h1High=[off(10,236,.5,65,9,58),off(9,238,.5,66,10,58),off(7.5,242,.55,68,12,58),off(7,246,.6,70,14,58),off(6.5,250,.6,72,15,58),off(6.2,253,.6,74,16,58),off(6,255,.6,75,16,58),off(6,255,.6,75,16,58),off(6,255,.6,75,16,58),off(6,255,.6,75,16,58)];
add('H1-high',1,{...H1,minClear:.2,keys:h1High,variant:'three-quarter view from 1.3 m, 6-7 m to the side of the track'});

// ---- D1 launch ----
const launch={take:'rantakatu-launch',...HERO,t0:4.2};
const D1={duration:1.6,hour:18.2,...common,cars:[launch],follow:{car:0,frame:'heading',smooth:.1},keys:[[1.5,-3.8,-.27,-8,9,80],[1.5,-3.4,-.27,-9,9,80]],
 note:'Launch at full throttle on Lantinen Rantakatu (76-95 km/h): lens 0.35 m above the paving, 1.5 m right of the car axis, 80 degree lens'};
add('D1',0,D1);
add('D1-front',1,{...D1,keys:[[-1.4,5.5,-.2,168,3,70],[-1.4,4.6,-.2,168,3,70]],variant:'low front three-quarter, lens ahead of the car looking back'});

// ---- D2 bridge ----
const bridge={take:'d2-e',...HERO,t0:5.4};
const D2={duration:1.6,hour:18.3,...common,cars:[bridge],follow:{car:0,frame:'world',smooth:.15},keys:[[-4.1,-22.5,5.4,0,0,36],[-18.7,-6,5.4,0,0,36]],target:[[0,0,.2],[0,0,.2]],
 note:'Bridge: drone over the river 18 m downstream of Auransilta and 6 m above the deck, tracking alongside; car on the bridge from clip 0.4 s to 3.2 s (70-82 km/h), it leaves the bridge at 3.2 s and hits a kerb at 3.8 s (end of the tail handle is unusable)'};
add('D2',0,D2);
add('D5',0,{...D2,duration:1.8,cars:[{take:'d2-e',...HERO,t0:4.9}],follow:{car:0,frame:'heading',smooth:.15},keys:[[3.6,.8,-.22,-98,3,62],[3.6,-.2,-.22,-88,3,62]],target:undefined,
 note:'Bridge from the side at wheel height: lens 0.4 m above the deck, 3.6 m to the right of the car, tracking with it at 70-82 km/h, railing and the river upstream streaking behind (replaces D3). Core = car time 6.4-8.2 on the bridge; the tail handle ends in the kerb hit'});
add('D2-front',1,{...D2,follow:{car:0,frame:'heading',smooth:.15},keys:[[.5,26,6.5,180,-13,40],[.5,18,6,180,-15,42]],target:undefined,variant:'high front view along the bridge, the car drives towards the lens'});

// ---- D3 castle (go/no-go by the coordinator from a still) ----
const D3={duration:1.6,hour:18.3,...common,cars:[{take:'d3-f',...HERO,t0:21.4}],follow:{car:0,frame:'world',smooth:.2},keys:[[-3,7.5,.4,0,0,50],[2,7,.4,0,0,50]],target:[[-1.5,-3,1.6],[-1.5,-3,1.6]],
 note:'Castle: low tracking from the north side of Linnankatu while the car passes at 64-75 km/h (autopilot take d3-f). UNSURE: the castle should be to the south of this stretch; never rendered'};
add('D3',0,D3);
add('D3-wide',1,{...D3,duration:2,cars:[{take:'d3-f',...HERO,t0:22.2}],follow:undefined,target:undefined,keys:[k(-1440,-1057,5.6,182,3,50),k(-1441,-1057,5.6,182,3,50)],variant:'locked-off wide view from 72 m north of the road, low lens, castle mass above the passing car'});
add('D3-chase',1,{...D3,cars:[{take:'d3-f',...HERO,t0:14.2}],follow:{car:0,frame:'heading',smooth:.15},keys:[[-1.2,-9,3,2,-6,40],[-.6,-7.5,2.6,1,-5,42]],target:undefined,variant:'crane chase on the approach, castle ahead (sun near the frame)'});

// ---- D4 riverside quay ----
const quay={take:'d4-quay-2',...HERO,t0:1.3,lights:0};
const D4={duration:2,hour:18.4,...common,cars:[quay],follow:{car:0,frame:'heading',smooth:.15},keys:[[1.2,-5.5,.38,-14,2,55],[1.2,-5.2,.38,-14,2,55]],
 note:'Riverside quay: chase from 1 m above the quay surface, behind and right of the car, moored boats and the river on the left (26-73 km/h; the quay ends at clip 5.0 s)'};
add('D4',0,D4);
add('D4-front',1,{...D4,keys:[[1.8,7,.5,198,2,50],[1.8,6,.5,198,2,50]],variant:'front three-quarter from the land side, boats behind the car'});

// ---- W1 on foot: same quay, locked-off ----
const parked={take:'d4-quay-2',...HERO,t0:3,rate:0};
const W1={duration:1.6,hour:18.5,...common,cars:[parked],walker:{from:[B+735.48,N0+199.81],heading:57,speed:1.4,t0:0,H:2.68},
 keys:[k(721.43,184.36,4.1,30,0,36),k(721.38,184.34,4.1,30,0,36)],
 note:'On foot: locked-off at 1.5 m on the quay, hero car parked in the left foreground, the driver walks away on the right (about 70 % of frame width). Walker renders (server draft d2); lens pulled back 3 m after that draft so the whole car is in frame'};
add('W1',0,W1);
add('W1-back',1,{...W1,walker:{from:[B+714.93,N0+186.46],heading:237,speed:1.4,t0:0,H:2.58},keys:[k(727.01,199.08,4.15,211.3,0,36),k(727.05,199.1,4.15,211.3,0,36)],variant:'looking south-west into the low sun: backlit walker, lens over the quay edge'});

// ---- T1a / T1b / C1: one donut replay ----
const lg=toriGround(lens.e,lens.n),t1aT0=lens.tc-handles;
const T1a={duration:2.1,hour:17.8,...common,minClear:.05,cars:six(t1aT0),keys:[k(lens.e,lens.n,+(lg+.4).toFixed(2),SUN-4,6,75),k(lens.e,lens.n,+(lg+.4).toFixed(2),SUN+4,6,75)],
 note:`Tori donuts from the cobbles: lens 0.4 m above the paving looking into the sun, the yellow E30 passes ${lens.nearest.toFixed(1)} m from the lens at clip ${(lens.when-t1aT0).toFixed(1)} s, no car closer than ${lens.clipMin.toFixed(1)} m`};
add('T1a',0,T1a);
add('T1a-high',1,{...T1a,keys:T1a.keys.map(key=>[key[0],key[1],+(lg+1).toFixed(2),key[3],2,75]),variant:'lens 1.0 m above the paving'});
add('T1a-track',1,{...T1a,follow:{car:lens.who,frame:'world',smooth:.3},keys:[off(4.6,SUN-180,-.2,SUN,5,75),off(4.6,SUN-180,-.2,SUN,5,75)],minClear:.05,variant:'lens rides 4.6 m beside the hero car, looking into the sun (other cars may pass close)'});
// Orbit about the group centre with an explicit tilt: height above the paving at the group centre, bearings of the lens
// from the centre, distance to the nearest car, tilt down and lens. All six cars stay in frame because the lens pans with G.
const orbit2=(height,from,to,nearest,pitch,fov)=>{const dist=R+nearest-2.5,keys=[];
 for(const b of [from,(from+to)/2,to]){const [s,c]=dir(b);keys.push(k(G[0]+dist*s,G[1]+dist*c,+(gG+height).toFixed(2),(b+180)%360,-pitch,fov));}
 return{keys,target:undefined};};
const T1b={duration:3.5,hour:17.8,...common,cars:six(b0),...orbit2(4.5,143,135,19,6,34),
 note:`Tori donuts: lens 4.5 m above the paving, 19 m from the nearest car, 40 degree lens tilted 7 degrees down: Orthodox church dome whole with sky above it, cars at about 40-64 % of frame height, the bottom third clean paving for the title, the group fills about 80 % of the width; slow orbit panning with the group. Six takes packed into a circle of ${(2*R).toFixed(0)} m (never closer than ${best.cost.min.toFixed(1)} m centre to centre); tighter than that the cars would touch. The layout is centred so the lamp post of the square stands inside one donut`};
add('T1b',0,T1b);
add('T1b-sun',1,{...T1b,...orbit2(3,84,76,14,7,40),variant:'second angle: from the east side looking west-north-west, sun just out of frame on the left, smoke backlit'});
add('T1b-low',1,{...T1b,...orbit(3,143,135,14,10.5,36),variant:'3 m above the paving, 14 m from the nearest car, 36 degree lens (dome cut at the top)'});
add('T1b-14m',1,{...T1b,...orbit2(14,143,135,35,17,34),variant:'drone 14 m above the paving, 35 m from the nearest car'});
add('T1b-rev',1,{...T1b,...orbit2(4.5,135,143,19,7,40),variant:'orbit reversed'});
const [s153,c153]=dir(153);
const c1=(end,clock,label)=>({duration:10.4,hour:clock[0],hourKeys:[[0,clock[0]],[.112,clock[0]],[.515,clock[1]],[1,clock[1]+.1]],weather:'clear',traffic:40,scenery:true,allowMissing:true,cars:six(c0),drift:[.515,.9,.4],
 keys:[k(G[0]+16*s153,G[1]+16*c153,+(gG+22).toFixed(1),333,-48,55),...end],
 note:`Rise and pull back from low over the Tori donuts (same replay and hour as the end of T1b), turning through the sunset to ${label}; clock ${clock[0]} to ${clock[1]} so the lights come on at dusk. Clip 13.4 s: 1.5 s handle, 5.4 s move, then 6.5 s of slow continued drift for the end card. View distance 8 km.`});
const endHigh=[k(700,545,60,325,-30,60),k(750,440,95,280,-20,64),k(800,350,120,205,-15,66)],endLow=[k(695,550,40,325,-28,58),k(735,470,50,285,-16,60),k(770,400,60,208,-10,62)];
add('C1',0,c1(endHigh,[17.8,20.4],'the aerial-centre pose (120 m, yaw 205, pitch -15, river to the horizon)'));
add('C1-low',1,{...c1(endLow,[17.8,20.4],'a lower end pose (60 m over the river bank)'),variant:'end pose 60 m lower and closer'});
add('C1-late',1,{...c1(endHigh,[18.5,21.5],'the aerial-centre pose'),variant:'clock 18.5 to 21.5'});
add('C1-low-late',1,{...c1(endLow,[18.5,21.5],'a lower end pose (60 m)'),variant:'end pose 60 m, clock 18.5 to 21.5'});

const config={fps:60,handles,date:'2026-09-08',traffic:40,walkers:0,shots};
fs.writeFileSync(path.join(here,'trailer-shots.json'),JSON.stringify(config,null,1));
// ---- finals queue: corrected base shots in priority order, then variants for coverage ----
const REV=JSON.parse(fs.readFileSync(path.join(here,'trailer-queue-rev.json')));
const order=(REV.order??['C1','P1','T1b','T1a','H1','W1','D1','D2','D4']);// base shots to capture now; D3 and W1 wait for a verdict on their drafts
const variants=REV.variants??['H1-slow','H1-low','C1-low','T1b-14m','T1a-high','H1-high','P1-11m','C1-late','T1b-rev','T1a-track','W1-back','D1-front','D2-front','D4-front','P1-dawn','C1-low-late','P1-11m-dawn'];
// Bump a shot's revision to capture it again (the runner keys entries by their content).
const byId=Object.fromEntries(shots.map(s=>[s.id,s]));
const queue=[...order,...variants,...(REV.extra??[])].filter(id=>byId[id]).map(id=>({shot:id,rev:REV[id]??1,note:byId[id].variant?`variant: ${byId[id].variant}`:'base'}));
fs.mkdirSync(path.join(root,'cloud'),{recursive:true});
const qfile=path.join(root,'cloud/finals-queue.json');fs.writeFileSync(qfile+'.tmp',JSON.stringify({updated:new Date().toISOString(),queue},null,1));fs.renameSync(qfile+'.tmp',qfile);
console.log(JSON.stringify({layoutDiameter:+(2*R).toFixed(1),minGap:+best.cost.min.toFixed(2),toriPlane:tori.map(v=>+v.toFixed(4)),G,lens:{...lens,hero:TAKES[lens.who]},h1Slope,centres:layout.map(s=>[+s[0].toFixed(1),+s[1].toFixed(1)]),shots:shots.length,queue:queue.length}));
