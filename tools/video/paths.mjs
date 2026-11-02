// Cubic camera splines with a gentle speed ease; endpoint tangents keep edits moving.
const keys=['E','N','H','yaw','pitch','fov'];
export function poseAt(shot,t) {
  const eased=t*.7+(t*t*(3-2*t))*.3;
  const points=[shot.from,...(shot.via??[]),shot.to];
  // Unwrap compass headings before interpolation across north.
  const headings=[points[0][3]];
  for(let i=1;i<points.length;i++)headings.push(headings.at(-1)+((points[i][3]-headings.at(-1)+540)%360)-180);
  const segment=Math.min(points.length-2,Math.floor(eased*(points.length-1))),u=eased*(points.length-1)-segment;
  return Object.fromEntries(keys.map((key,k)=>{
    const at=i=>k===3?headings[Math.max(0,Math.min(points.length-1,i))]:points[Math.max(0,Math.min(points.length-1,i))][k];
    if(points.length===2)return[key,at(0)+(at(1)-at(0))*eased];
    const a=at(segment-1),b=at(segment),c=at(segment+1),d=at(segment+2);
    return[key,.5*((2*b)+(-a+c)*u+(2*a-5*b+4*c-d)*u*u+(-a+3*b-3*c+d)*u*u*u)];
  }));
}
export function tilesForShot(shot) {
  const tiles=new Set(),p=[shot.from,...(shot.via??[]),shot.to];
  const length=p.slice(1).reduce((sum,q,i)=>sum+Math.hypot(q[0]-p[i][0],q[1]-p[i][1]),0);
  // Dense coverage plus a complete 250m ring, including every control point and endpoint.
  const n=Math.max(64,Math.ceil(length*4));
  for(let k=0;k<=n;k++) {
    const p=poseAt(shot,k/n),i=Math.floor((p.E-23459500)/250),j=Math.floor((6704500-p.N)/250);
    for(let a=-1;a<=1;a++)for(let b=-1;b<=1;b++)tiles.add(`t0_${i+a}_${j+b}`);
  }
  return [...tiles].sort();
}
export function allTiles(config){return [...new Set(config.shots.flatMap(tilesForShot))].sort();}
