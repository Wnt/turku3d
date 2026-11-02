// Compile only existing facade evidence. No guessed windows on shells, roofs or blank walls.
import { decodeFacadeRgb } from '../../pipeline/turku3d/package/facade_decode.mjs';

export const programFields = ['_KIT_LAYOUT','_KIT_WALL','_KIT_TRIM','_KIT_BASE','_KIT_EXTENT','_KIT_FLOORS',
  '_KIT_OPENING','_KIT_BANDS','_KIT_DETAIL','_KIT_GLAZING','_KIT_FRAME'];

export function openingKind(primitive, extras) {
  if (programFields.slice(0,-2).every(key => primitive.getAttribute(key)) && primitive.getAttribute('TEXCOORD_0')) return 'program';
  const material = primitive.getMaterial();
  // Legacy atlas kits need their atlas addressing, never ordinary metre UV sampling.
  if (material?.getExtras().facade_kit) return 'none';
  const walls = extras.objects ?? [extras];
  if (!walls.some(row => row.surface === 'wall') || !primitive.getAttribute('TEXCOORD_0')) return 'none';
  return material?.getBaseColorTexture() ? 'mapped' : 'none';
}

export function transformedUv(info, uv) {
  const transform = info?.getExtension('KHR_texture_transform');
  if (!transform) return uv;
  const [sx,sy] = transform.getScale(), [x,y] = transform.getOffset(), angle = transform.getRotation();
  return [x + Math.cos(angle)*uv[0]*sx - Math.sin(angle)*uv[1]*sy,
    y + Math.sin(angle)*uv[0]*sx + Math.cos(angle)*uv[1]*sy];
}

const smooth = (a,b,v) => { const t=Math.max(0,Math.min(1,(v-a)/(b-a))); return t*t*(3-2*t); };
export function glazingMask(roughness, rgb) {
  return roughness == null ? 1-smooth(.14,.22,.2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2])
    : 1-smooth(.2,.5,roughness);
}

/** Remove coplanar tessellation inside one instruction/UV chart, without changing its mapping. */
export function reduceFacade(primitive, matrix, simplifier) {
  const position=primitive.getAttribute('POSITION'),uv=primitive.getAttribute('TEXCOORD_0');
  const fields=programFields.map(key=>primitive.getAttribute(key)).filter(Boolean);
  const positions=[],coords=[],source=[],remap=[],welded=new Map(), instructions=new Map(), ids=[];
  for(let i=0;i<position.getCount();i++){
    const p=[0,1,2].map(k=>matrix[k]*position.getElement(i,[])[0]+matrix[k+4]*position.getElement(i,[])[1]+
      matrix[k+8]*position.getElement(i,[])[2]+matrix[k+12]);
    const t=uv.getElement(i,[]);
    const instruction=JSON.stringify(fields.flatMap(a=>a.getElement(i,[])));
    if(!instructions.has(instruction))instructions.set(instruction,instructions.size);
    const id=instructions.get(instruction);
    const key=JSON.stringify([...p.map(v=>Math.round(v*1e5)),...t,id]);
    let index=welded.get(key);
    if(index===undefined){index=source.length;welded.set(key,index);source.push(i);positions.push(...p);coords.push(...t,id);ids.push(id);}
    remap.push(index);
  }
  const indices=Uint32Array.from(primitive.getIndices()?.getArray()??remap.map((_,i)=>i),i=>remap[i]);
  const charts=new Map(),reduced=[];
  for(let i=0;i<indices.length;i+=3){
    const tri=Array.from(indices.subarray(i,i+3));
    if(ids[tri[0]]!==ids[tri[1]]||ids[tri[0]]!==ids[tri[2]]){reduced.push(...tri);continue;}
    const id=ids[tri[0]];if(!charts.has(id))charts.set(id,[]);charts.get(id).push(...tri);
  }
  for(const chart of charts.values()){
    const local=new Map(),original=[],p=[],t=[];
    const ix=Uint32Array.from(chart,index=>{
      if(!local.has(index)){local.set(index,original.length);original.push(index);p.push(...positions.slice(index*3,index*3+3));t.push(...coords.slice(index*3,index*3+2));}
      return local.get(index);
    });
    const [result]=simplifier.simplifyWithAttributes(ix,new Float32Array(p),3,new Float32Array(t),2,[1,1],null,
      Math.max(3,Math.floor(ix.length*.08/3)*3),.03,['ErrorAbsolute','Permissive']);
    for(const i of result)reduced.push(original[i]);
  }
  const used=[],compact=new Map();
  const output=Array.from(reduced,i=>{if(!compact.has(i)){compact.set(i,used.length);used.push(source[i]);}return compact.get(i);});
  return {vertices:used,indices:output};
}

export async function decodeImage(texture, sharp) {
  const bytes=texture.getImage();
  if (texture.getMimeType()==='image/ktx2') return decodeFacadeRgb(bytes);
  const {data,info}=await sharp(bytes).removeAlpha().raw().toBuffer({resolveWithObject:true});
  return {data,width:info.width,height:info.height};
}

/** A tile-local RGBA atlas: authored albedo + glazing, a single sampler/draw family. */
export async function openingAtlas(doc, candidates, sharp) {
  const entries=[], byMaterial=new Map();
  for (const primitive of candidates) {
    const material=primitive.getMaterial();
    if (byMaterial.has(material)) continue;
    const entry={material,index:entries.length}; entries.push(entry);byMaterial.set(material,entry);
  }
  if (!entries.length) return {byMaterial};
  // At most 16 MiB uncompressed per source tile, including gutters.
  const columns=Math.ceil(Math.sqrt(entries.length)), cell=Math.min(256,Math.floor(2048/columns));
  if (cell<8) throw Error('Too many independent distant facade materials');
  const rows=Math.ceil(entries.length/columns), width=columns*cell,height=rows*cell;
  const pixels=Buffer.alloc(width*height*4), size=cell-4;
  for (const entry of entries) {
    const albedo=await decodeImage(entry.material.getBaseColorTexture(),sharp);
    const roughness=entry.material.getMetallicRoughnessTexture();
    const orm=roughness?await decodeImage(roughness,sharp):null;
    const colour=await sharp(albedo.data,{raw:{width:albedo.width,height:albedo.height,channels:3}})
      .resize(size,size,{fit:'fill'}).raw().toBuffer();
    // Classify before reduction: averaged masonry roughness must not become glass.
    const mask=Buffer.alloc(albedo.width*albedo.height);
    for(let y=0;y<albedo.height;y++)for(let x=0;x<albedo.width;x++){
      const i=(y*albedo.width+x)*3;
      const rough=orm?orm.data[(Math.min(orm.height-1,Math.floor(y/albedo.height*orm.height))*orm.width+
        Math.min(orm.width-1,Math.floor(x/albedo.width*orm.width)))*3+1]/255:null;
      const rgb=[albedo.data[i],albedo.data[i+1],albedo.data[i+2]].map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;});
      mask[y*albedo.width+x]=Math.round(255*glazingMask(rough,rgb));
    }
    const alpha=await sharp(mask,{raw:{width:albedo.width,height:albedo.height,channels:1}})
      .resize(size,size,{fit:'fill'}).greyscale().raw().toBuffer();
    const left=entry.index%columns*cell,top=Math.floor(entry.index/columns)*cell;
    for(let y=0;y<cell;y++)for(let x=0;x<cell;x++){
      const i=(Math.max(0,Math.min(size-1,y-2))*size+Math.max(0,Math.min(size-1,x-2)));
      const dst=((top+y)*width+left+x)*4;
      pixels.set(colour.subarray(i*3,i*3+3),dst);pixels[dst+3]=alpha[i];
    }
    // Sample texel centres; filtering never reaches a neighbour's facade.
    entry.rect=[(left+2.5)/width,(top+2.5)/height,(size-1)/width,(size-1)/height];
    const info=entry.material.getBaseColorTextureInfo();
    entry.repeat=[info.getWrapS()===10497?1:0,info.getWrapT()===10497?1:0];
  }
  const texture=doc.createTexture('far-authored-openings').setMimeType('image/png')
    .setImage(await sharp(pixels,{raw:{width,height,channels:4}}).png().toBuffer());
  const material=doc.createMaterial('far-authored-openings').setBaseColorTexture(texture)
    .setRoughnessFactor(1).setMetallicFactor(0);
  return {byMaterial,material,bytes:width*height*4,entries:entries.length};
}
