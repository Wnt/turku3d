// Smooth Hermite paths; endpoint tangents avoid stopping at every edit.
export function poseAt(shot, t) {
  const keys = ['E','N','H','yaw','pitch','fov'];
  const eased = t * .7 + (t*t*(3-2*t)) * .3;
  return Object.fromEntries(keys.map((key,i) => {
    let d=shot.to[i]-shot.from[i];
    if(key==='yaw') d=((d+540)%360)-180;
    return [key,shot.from[i]+d*eased];
  }));
}
export function tilesForShot(shot) {
  const tiles=new Set();
  // Sample at sub-metre spacing, including both ends. One full 250m ring.
  const n=Math.max(2,Math.ceil(Math.hypot(shot.to[0]-shot.from[0],shot.to[1]-shot.from[1])*2));
  for(let k=0;k<=n;k++) {
    const p=poseAt(shot,k/n),i=Math.floor((p.E-23459500)/250),j=Math.floor((6704500-p.N)/250);
    for(let a=-1;a<=1;a++)for(let b=-1;b<=1;b++)tiles.add(`t0_${i+a}_${j+b}`);
  }
  return [...tiles].sort();
}
export function allTiles(config) {return [...new Set(config.shots.flatMap(tilesForShot))].sort();}
