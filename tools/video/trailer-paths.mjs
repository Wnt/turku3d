// Trailer camera paths: Catmull-Rom splines through keys [E,N,H,yaw,pitch,fov,roll], optional look-at target keys,
// eased travel and an optional hour sweep. Coordinates: EPSG:3877 E/N, N2000 eye height, compass yaw, pitch up.
const EASE={
 linear:t=>t,
 cruise:t=>t*.8+t*t*(3-2*t)*.2,
 smooth:t=>t*t*(3-2*t),
 in:t=>t*t,
 out:t=>1-(1-t)*(1-t),
};
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
export function poseAt(shot,u){
 const t=(EASE[shot.ease??'cruise']??EASE.cruise)(Math.max(0,Math.min(1,u))),keys=keysOf(shot);
 const pose={E:spline(keys,0,t),N:spline(keys,1,t),H:spline(keys,2,t),yaw:spline(keys,3,t,true),pitch:spline(keys,4,t),fov:spline(keys,5,t)||60,roll:spline(keys,6,t)};
 if(shot.target){
  const g=shot.target,a={E:spline(g,0,t),N:spline(g,1,t),H:spline(g,2,t)},dE=a.E-pose.E,dN=a.N-pose.N;
  pose.yaw=(Math.atan2(dE,dN)*180/Math.PI+360)%360;pose.pitch=Math.atan2(a.H-pose.H,Math.hypot(dE,dN))*180/Math.PI;
 }
 return pose;
}
export function hourAt(shot,u){const a=shot.hour??12;return shot.hourTo==null?a:a+(shot.hourTo-a)*Math.max(0,Math.min(1,u));}
export function tilesAlong(shot,n=64){
 const tiles=new Set();
 for(let k=0;k<=n;k++){const p=poseAt(shot,k/n),i=Math.floor((p.E-23459500)/250),j=Math.floor((6704500-p.N)/250);
  for(let a=-1;a<=1;a++)for(let b=-1;b<=1;b++)tiles.add(`t0_${i+a}_${j+b}`);}
 return [...tiles].sort();
}
