// Keep architectural night light in distant geometry; source images remain authored game maps.
import fs from 'node:fs';
const rules = JSON.parse(fs.readFileSync(new URL('../../pipeline/turku3d/night_lighting.json', import.meta.url), 'utf8'));
const smooth = (a,b,x) => { const t=Math.max(0,Math.min(1,(x-a)/(b-a))); return t*t*(3-2*t); };

export function nightKey(primitive, extras) {
  const key=primitive.getMaterial()?.getExtras().material_key ?? extras.material_key;
  return rules.materials[key] ? key : null;
}

export function nightEmission(primitive, key, index, matrix) {
  const factor=rules.materials[key]?.intensity ?? 0;
  let rgb=(primitive.getMaterial()?.getEmissiveFactor() ?? [0,0,0]).map(v=>v*factor);
  if(key==='night_bridge_soffit') {
    // Inverse-transpose normal via cofactor columns; normalized, including nonuniform scale.
    const n=primitive.getAttribute('NORMAL')?.getElement(index,[]) ?? [0,-1,0];
    const a=matrix.slice(0,3),b=matrix.slice(4,7),c=matrix.slice(8,11);
    const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
    const columns=[cross(b,c),cross(c,a),cross(a,b)];
    const normal=[0,1,2].map(k=>columns.reduce((s,col,j)=>s+col[k]*n[j],0));
    const down=-normal[1]/Math.max(1e-8,Math.hypot(...normal));
    const color=primitive.getMaterial()?.getBaseColorFactor() ?? [1,1,1];
    rgb=rgb.map((v,k)=>v*color[k]*smooth(.25,.8,down));
  }
  return [...rgb,key==='night_boat_cabin'?1:0];
}
