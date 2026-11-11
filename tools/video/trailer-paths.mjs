// Trailer camera paths: Catmull-Rom splines through keys [E,N,H,yaw,pitch,fov,roll], optional look-at target keys,
// eased travel and an optional hour sweep. Coordinates: EPSG:3877 E/N, N2000 eye height, compass yaw, pitch up.
const EASE={
 linear:t=>t,
 cruise:t=>t*.8+t*t*(3-2*t)*.2,
 smooth:t=>t*t*(3-2*t),
 in:t=>t*t,
 out:t=>1-(1-t)*(1-t),
};
// A move that reaches path fraction b at clip fraction a (start slope s0) and then drifts on at constant speed.
function settleDrift(u,[a,b,s0]){
 const v=(1-b)/(1-a);if(u>=a)return b+(u-a)*v;
 const x=u/a,x2=x*x,x3=x2*x;return (x3-2*x2+x)*a*s0+(-2*x3+3*x2)*b+(x3-x2)*a*v;
}
function spline(points,k,t,wrap){
 const n=points.length;if(n===1)return points[0][k]??0;
 const values=points.map(p=>p[k]??0);
 if(wrap)for(let i=1;i<n;i++)values[i]=values[i-1]+((values[i]-values[i-1]+540)%360)-180;
 const s=Math.min(n-2,Math.floor(t*(n-1))),u=t*(n-1)-s,at=i=>values[Math.max(0,Math.min(n-1,i))];
 if(n===2)return at(0)+(at(1)-at(0))*t;
 const a=at(s-1),b=at(s),c=at(s+1),d=at(s+2);
 return .5*((2*b)+(-a+c)*u+(2*a-5*b+4*c-d)*u*u+(-a+3*b-3*c+d)*u*u*u);
}
export function keysOf(shot){return shot.keys??[shot.from,...(shot.via??[]),shot.to];}
// With an anchor (a followed car: {E,N,H,heading}) the keys and targets are offsets from it: [right, forward, up]
// in the anchor's heading frame (follow.frame 'heading', yaw relative to the heading) or [dE, dN, dH] ('world').
export function poseAt(shot,u,anchor){
 const c01=Math.max(0,Math.min(1,u)),t=shot.drift?settleDrift(c01,shot.drift):(EASE[shot.ease??'cruise']??EASE.cruise)(c01),keys=keysOf(shot);
 const pose={E:spline(keys,0,t),N:spline(keys,1,t),H:spline(keys,2,t),yaw:spline(keys,3,t,true),pitch:spline(keys,4,t),fov:spline(keys,5,t)||60,roll:spline(keys,6,t)};
 let aim=shot.target?{E:spline(shot.target,0,t),N:spline(shot.target,1,t),H:spline(shot.target,2,t)}:null;
 if(anchor){
  const world=shot.follow?.frame==='world',h=anchor.heading*Math.PI/180,c=Math.cos(h),s=Math.sin(h);
  const place=p=>world?{E:anchor.E+p.E,N:anchor.N+p.N,H:anchor.H+p.H}:{E:anchor.E+p.E*c+p.N*s,N:anchor.N-p.E*s+p.N*c,H:anchor.H+p.H};
  // follow.aimOnly: the camera keeps its absolute path and only the look-at target rides on the car.
  if(!shot.follow?.aimOnly){Object.assign(pose,place(pose));if(!world)pose.yaw+=anchor.heading;}
  if(aim)aim=place(aim);
 }
 if(aim){
  const dE=aim.E-pose.E,dN=aim.N-pose.N;
  pose.yaw=(Math.atan2(dE,dN)*180/Math.PI+360)%360;pose.pitch=Math.atan2(aim.H-pose.H,Math.hypot(dE,dN))*180/Math.PI;
 }
 return pose;
}
// Hour of day along the clip: constant, a linear sweep to `hourTo`, or piecewise linear `hourKeys` [[u,hour],...].
export function hourAt(shot,u){
 const a=shot.hour??12,c=Math.max(0,Math.min(1,u)),k=shot.hourKeys;
 if(k){if(c<=k[0][0])return k[0][1];for(let i=1;i<k.length;i++)if(c<=k[i][0])return k[i-1][1]+(k[i][1]-k[i-1][1])*(c-k[i-1][0])/(k[i][0]-k[i-1][0]);return k.at(-1)[1];}
 return shot.hourTo==null?a:a+(shot.hourTo-a)*c;
}
export function tilesAlong(shot,n=64){
 const tiles=new Set();
 for(let k=0;k<=n;k++){const p=poseAt(shot,k/n),i=Math.floor((p.E-23459500)/250),j=Math.floor((6704500-p.N)/250);
  for(let a=-1;a<=1;a++)for(let b=-1;b<=1;b++)tiles.add(`t0_${i+a}_${j+b}`);}
 return [...tiles].sort();
}
