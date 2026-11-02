#!/usr/bin/env node
// Per-tile, area-weighted opening agreement on shipped wall triangles.
// Program masks omit centimetre frame bars; both LODs use the same reference evaluator.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { readSource, require, world } from './scenery-source.mjs';
import { openingKind, programFields, decodeImage, transformedUv, glazingMask } from './scenery-openings.mjs';
const sharp=require('sharp'),fract=v=>v-Math.floor(v),clamp=v=>Math.max(0,Math.min(1,v));
const sub=(a,b)=>a.map((v,k)=>v-b[k]),dot=(a,b)=>a.reduce((v,n,k)=>v+n*b[k],0);
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const mix=(points,weights)=>points[0].map((_,k)=>points.reduce((v,p,i)=>v+p[k]*weights[i],0));

/** CPU opening envelope, including measured patches, blank modules and arched heads. */
export function programMask(fields, uv) {
  const l=fields._KIT_LAYOUT,e=fields._KIT_EXTENT,f=fields._KIT_FLOORS,o=fields._KIT_OPENING,d=fields._KIT_DETAIL,b=fields._KIT_BANDS;
  const x=uv[0]-e[0],y=uv[1]-e[1],upper=y>=f[0];
  let bay=x/l[1],row=upper?(y-f[0])/f[1]+1:y/f[0],module=l[0];
  if(module>=42||y>=e[3]||(!upper&&(d[2]%2)>.5))return 0;
  if(!upper&&module%3===1&&Math.floor(bay)!==f[2])module--;
  const special=!upper&&module%3!==0,door=special&&module%3===1,shop=special&&module%3===2;
  const patch=d[3]>0,kind=d[3]%4;
  if(door||patch&&kind===2)return 0;
  if(!special)bay+=(Math.floor(bay)%2*2-1)*d[0];
  const xx=fract(bay),yy=fract(row),unit=patch?e[3]:(upper?f[1]:f[0]);
  const width=patch?1:shop?.78:o[0],height=patch?1:shop?.74:o[1],bottom=patch?0:shop?.05/3.84:o[2];
  const half=width*(patch?e[2]:l[1])/2,across=Math.abs(xx-.5)*(patch?e[2]:l[1]);
  const shape=patch?Math.floor(d[3]/4):upper?-d[3]:Math.floor(d[2]/2);
  const rise=Math.min(half*(shape===3?Math.sqrt(3):shape===2?1:.36),height*unit*.5);
  const radius=(half*half+rise*rise)/Math.max(rise*2,.001);
  const drop=shape===3?rise*(1-Math.sqrt(Math.max(0,4-(clamp(across/Math.max(half,.001))+1)**2))/Math.sqrt(3))
    :radius-Math.sqrt(Math.max(0,radius*radius-Math.min(across,half)**2));
  const top=bottom+height-(shape>0?drop/unit:0),py=patch?clamp(y/e[3]):yy;
  if(across>half||py<bottom||py>top)return 0;
  // Wall trim is not glazing, including bands that cross an opening.
  if(y>e[3]-b[1]||Math.min(x,e[2]-x)<b[3]||upper&&Math.min(yy,1-yy)*l[2]<b[2]/2)return 0;
  if(upper&&d[1]&&Math.floor(bay)%2===0&&Math.abs(xx-.5)*l[1]<=l[1]*.36&&yy*unit>=.15&&yy*unit<=1.05)return 0;
  return 1;
}

export function legacyGrid(point, normal) {
  const length=Math.hypot(normal[0],normal[2]);if(length<.001||Math.abs(normal[1])>.7)return 0;
  const along=point[0]*-normal[2]/length+point[2]*normal[0]/length;
  const x=fract(along/2.8),y=fract(point[1]/3.2);
  return x>=.22&&x<=.68&&y>=.25&&y<=.77?1:0;
}

function sample(image,uv,repeat=[1,1],channels=3) {
  const p=uv.map((v,k)=>repeat[k]?fract(v):clamp(v));
  const x=Math.min(image.width-1,Math.floor(p[0]*image.width)),y=Math.min(image.height-1,Math.floor(p[1]*image.height));
  return Array.from(image.data.subarray((y*image.width+x)*channels,(y*image.width+x)*channels+channels),v=>v/255);
}
async function surfaces(document, far=false) {
  const textures=new Map(),result=[];
  const image=async(texture,rgba=false)=>{
    if(!textures.has(texture))textures.set(texture,rgba?sharp(texture.getImage()).ensureAlpha().raw().toBuffer({resolveWithObject:true})
      .then(({data,info})=>({data,width:info.width,height:info.height})):decodeImage(texture,sharp));
    return textures.get(texture);
  };
  for(const node of document.getRoot().listNodes()){
    const extras=node.getExtras();if(!String(extras.layer).startsWith('buildings')&&extras.layer!=='landmarks')continue;
    if(node.getExtension('EXT_mesh_gpu_instancing'))continue;
    const matrix=node.getWorldMatrix();
    for(const primitive of node.getMesh()?.listPrimitives()??[]){
      const position=primitive.getAttribute('POSITION'),uv=primitive.getAttribute('TEXCOORD_0'),indices=primitive.getIndices();
      const material=primitive.getMaterial(),kind=far?String(extras.layer).replace('buildings-',''):openingKind(primitive,extras);
      const albedo=kind==='mapped'?await image(material.getBaseColorTexture(),far):null;
      const orm=kind==='mapped'&&!far&&material.getMetallicRoughnessTexture()?await image(material.getMetallicRoughnessTexture()):null;
      for(let t=0;t<(indices?.getCount()??position.getCount());t+=3){
        const ids=[0,1,2].map(k=>indices?.getScalar(t+k)??t+k),points=ids.map(i=>world(matrix,position.getElement(i,[])));
        const n=cross(sub(points[1],points[0]),sub(points[2],points[0])),length=Math.hypot(...n);
        if(length<.0001||Math.abs(n[1])/length>.7)continue;
        const owner=extras.objects?.find(o=>t>=o.start&&t<o.start+o.count)??extras;
        const fields=Object.fromEntries(programFields.filter(key=>primitive.getAttribute(key)).map(key=>[key,primitive.getAttribute(key).getElement(ids[0],[])]));
        const coords=uv?ids.map(i=>uv.getElement(i,[])):null;
        const mask=weights=>{
          const u=coords?mix(coords,weights):[0,0];
          if(kind==='program')return programMask(fields,u);
          if(kind!=='mapped')return 0;
          if(far){const rect=primitive.getAttribute('_FAR_RECT').getElement(ids[0],[]),repeat=primitive.getAttribute('_FAR_REPEAT').getElement(ids[0],[]);
            const p=u.map((v,k)=>rect[k]+(repeat[k]?fract(v):clamp(v))*rect[k+2]);return sample(albedo,p,[0,0],4)[3];}
          const cinfo=material.getBaseColorTextureInfo(),rinfo=material.getMetallicRoughnessTextureInfo();
          const rgb=sample(albedo,transformedUv(cinfo,u),[cinfo.getWrapS()===10497,cinfo.getWrapT()===10497])
            .map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
          return glazingMask(orm?sample(orm,transformedUv(rinfo,u),[rinfo.getWrapS()===10497,rinfo.getWrapT()===10497])[1]:null,rgb);
        };
        result.push({points,normal:n.map(v=>v/length),area:length/2,mask,coords,kind,
          signature:JSON.stringify(Object.fromEntries(programFields.slice(0,-2).filter(key=>fields[key]).map(key=>[key,fields[key].map(v=>Math.round(v*10000))]))),
          id:String(owner.building_id??owner.object_id??node.getName()),address:owner.address,landmark:!!owner.landmark_description});
      }
    }
  }
  return result;
}

function weights(triangle,point){
  const a=sub(triangle.points[1],triangle.points[0]),b=sub(triangle.points[2],triangle.points[0]),p=sub(point,triangle.points[0]);
  if(Math.abs(dot(p,triangle.normal))>.05)return null;
  const aa=dot(a,a),ab=dot(a,b),bb=dot(b,b),pa=dot(p,a),pb=dot(p,b),den=aa*bb-ab*ab;
  if(Math.abs(den)<1e-10)return null;
  const v=(bb*pa-ab*pb)/den,w=(aa*pb-ab*pa)/den;
  return Math.min(v,w,1-v-w)>=-.002?[1-v-w,v,w]:null;
}

export async function auditDocuments(source,derivative){
  const near=await surfaces(source),far=derivative?await surfaces(derivative,true):[],cells=new Map(),rows=new Map();
  const key=p=>p.map(v=>Math.floor(v/8)).join(',');
  for(const face of far){
    const lo=[0,1,2].map(k=>Math.floor(Math.min(...face.points.map(p=>p[k]))/8)),hi=[0,1,2].map(k=>Math.floor(Math.max(...face.points.map(p=>p[k]))/8));
    for(let x=lo[0];x<=hi[0];x++)for(let y=lo[1];y<=hi[1];y++)for(let z=lo[2];z<=hi[2];z++){
      const k=[x,y,z].join(',');if(!cells.has(k))cells.set(k,[]);cells.get(k).push(face);
    }
  }
  for(const face of near){
    if(!rows.has(face.id))rows.set(face.id,{building:face.id,address:face.address,landmark:face.landmark,area:0,near:0,far:0,invented:0,missing:0,offset:0,offsetArea:0});
    const row=rows.get(face.id),count=Math.max(1,Math.min(64,Math.ceil(face.area*4))),area=face.area/count;
    for(let i=0;i<count;i++){
      const a=Math.sqrt((i+.5)/count),b=fract(i*.61803398875+.37),w=[1-a,a*(1-b),a*b],p=mix(face.points,w);
      const reference=face.mask(w)>.5?1:0;
      let value=derivative?0:legacyGrid(p,face.normal),match;
      let best=Infinity;
      if(derivative)for(const f of cells.get(key(p))??[]){
        if(dot(f.normal,face.normal)<.95)continue;const v=weights(f,p);if(!v)continue;
        const score=Math.abs(dot(sub(p,f.points[0]),f.normal))+(face.kind==='program'&&f.signature!==face.signature?10:0);
        if(score>=best)continue;best=score;
        match={face:f,weights:v};value=f.mask(v)>.5?1:0;
      }
      row.area+=area;row.near+=reference*area;row.far+=value*area;
      row.invented+=Math.max(0,value-reference)*area;row.missing+=Math.max(0,reference-value)*area;
      if(face.kind==='program'&&match?.face.kind==='program'){
        const u=mix(face.coords,w),v=mix(match.face.coords,match.weights);
        row.offset+=Math.hypot(u[0]-v[0],u[1]-v[1])*area;row.offsetArea+=area;
      }
    }
  }
  const buildings=[...rows.values()].map(r=>({building:r.building,address:r.address,landmark:r.landmark,wall_m2:r.area,
    near_opening_m2:r.near,far_opening_m2:r.far,invented_m2:r.invented,missing_m2:r.missing,
    mismatch_score:(r.invented+r.missing)/Math.max(r.area,1e-9),bay_floor_offset_m:r.offsetArea?r.offset/r.offsetArea:null}));
  buildings.sort((a,b)=>b.mismatch_score-a.mismatch_score);
  return {method:'deterministic area samples; opening envelopes exclude frame bars; UV offset only for authored programs',
    buildings,wall_m2:buildings.reduce((n,b)=>n+b.wall_m2,0),invented_m2:buildings.reduce((n,b)=>n+b.invented_m2,0),missing_m2:buildings.reduce((n,b)=>n+b.missing_m2,0)};
}

if(process.argv[1]===fileURLToPath(import.meta.url)){
  const args=process.argv.slice(2),opt=(k,f)=>args.includes(k)?args[args.indexOf(k)+1]:f;
  const root=opt('--root',''),tiles=opt('--tiles','').split(',').filter(Boolean),out=opt('--out','window-audit.json'),products=opt('--derivatives',null),results=[];
  for(const tile of tiles){
    const dir=path.join(root,tile),manifest=JSON.parse(fs.readFileSync(path.join(dir,'last-full.json')));
    const source=path.join(dir,manifest.visual[tile].url),input=await readSource(source);
    let far;
    if(products){fs.mkdirSync(products,{recursive:true});const file=path.join(products,`${tile}.glb`);
      if(!fs.existsSync(file))execFileSync('node',[fileURLToPath(new URL('./scenery.mjs',import.meta.url)),source,file],{stdio:['ignore','ignore','inherit'],timeout:120000});
      far=await readSource(file);
    }
    results.push({tile,...await auditDocuments(input,far)});
    fs.writeFileSync(out,JSON.stringify({tiles:results},null,2));
    console.log(JSON.stringify({tile,...Object.fromEntries(Object.entries(results.at(-1)).filter(([k])=>k!=='buildings')),buildings:results.at(-1).buildings.length,worst:results.at(-1).buildings.slice(0,3)}));
  }
}
