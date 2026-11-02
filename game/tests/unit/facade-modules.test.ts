import { expect, it, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { addFacadeModules, prepareFacadeModules, rectangularOpenings } from '../../src/render/facadeModules';
import { interleaveProgram } from '../../src/render/facadeProgramAttributes';

function patch(kind = 1, clipped = false) {
  const geometry = new THREE.BufferGeometry();
  const fields: Record<string, [number[], number]> = {
    position:[[0,0,0, 2,0,0, 2,3,0, 0,3,0],3], normal:[[0,0,-1, 0,0,-1, 0,0,-1, 0,0,-1],3],
    uv:[[clipped ? .2 : 0,0, 2,0, 2,3, 0,3],2],
    _kit_extent:[Array(4).fill([0,0,2,3]).flat(),4], _kit_detail:[Array(4).fill([0,0,0,kind]).flat(),4],
    _kit_layout:[Array(4).fill([0,2,3]).flat(),3], _kit_frame:[Array(4).fill([.2,.3,.4,0]).flat(),4],
    _kit_trim:[Array(4).fill([.7,.7,.7]).flat(),3], _kit_opening:[Array(4).fill([.98,.95,.02,0]).flat(),4],
  };
  for (const [name,[array,size]] of Object.entries(fields)) geometry.setAttribute(name,new THREE.Float32BufferAttribute(array,size));
  geometry.setIndex([0,2,1,0,3,2]);
  return geometry;
}

it('reads exact reviewed rectangles after program interleaving and excludes arches, doors and clipped patches', () => {
  const geometry=patch(); interleaveProgram(geometry);
  expect(rectangularOpenings(geometry)).toHaveLength(1);
  expect(rectangularOpenings(patch(5))).toHaveLength(0);
  expect(rectangularOpenings(patch(2))).toHaveLength(0);
  expect(rectangularOpenings(patch(1,true))).toHaveLength(0);
});

it('pools five solid pieces with positive transforms and distance LOD without duplicating a model', () => {
  const model=new THREE.Group(); const source=new THREE.Mesh(patch()); source.position.set(100,5,80); model.add(source);
  addFacadeModules(model); addFacadeModules(model);
  const roots=model.children.filter(o=>o.name==='facade-modules'); expect(roots).toHaveLength(1);
  const lod=roots[0].children[0] as THREE.LOD;
  const mesh=lod.children[0] as THREE.InstancedMesh; expect(mesh.count).toBe(5);
  const matrix=new THREE.Matrix4();
  for(let i=0;i<mesh.count;i++){
    mesh.getMatrixAt(i,matrix); expect(matrix.determinant()).toBeGreaterThan(0);
    expect(mesh.boundingBox!.containsBox(new THREE.Box3(new THREE.Vector3(-.5,-.5,-.5),new THREE.Vector3(.5,.5,.5)).applyMatrix4(matrix))).toBe(true);
  }
  expect(lod.levels[1].distance).toBe(110);
  expect(lod.position.x).toBeGreaterThan(100);
});

it('cooperatively prepares the same instance transforms as synchronous construction', async () => {
  const models=[new THREE.Group(),new THREE.Group()];
  for(const model of models)for(let i=0;i<40;i++){
    const source=new THREE.Mesh(patch());source.position.x=i*10;model.add(source);
  }
  addFacadeModules(models[0]);
  let clock=0,frames=0;
  const timer=vi.spyOn(performance,'now').mockImplementation(()=>++clock);
  vi.stubGlobal('requestAnimationFrame',(callback:FrameRequestCallback)=>{frames++;queueMicrotask(()=>callback(clock));return frames;});
  try { await prepareFacadeModules(models[1]); } finally { timer.mockRestore();vi.unstubAllGlobals(); }
  const arrays=models.map(model=>{
    const result:number[][]=[];
    model.getObjectByName('facade-modules')!.traverse(o=>{if((o as THREE.InstancedMesh).isInstancedMesh)result.push(Array.from((o as THREE.InstancedMesh).instanceMatrix.array));});
    return result;
  });
  expect(frames).toBeGreaterThan(0);expect(arrays[1]).toEqual(arrays[0]);
});

it('uses metric wall programs for ground and upper windows, retaining shop and entrance exclusions', () => {
  const geometry=patch(0);
  geometry.setAttribute('_kit_floors',new THREE.Float32BufferAttribute(Array(4).fill([1,1,0]).flat(),3));
  geometry.setAttribute('_kit_opening',new THREE.Float32BufferAttribute(Array(4).fill([.5,.6,.2,0]).flat(),4));
  const windows=rectangularOpenings(geometry);
  expect(windows).toHaveLength(3);
  expect(windows[0].origin.y).toBeCloseTo(.2);
  expect(windows[2].origin.y).toBeCloseTo(2.2);
  expect(windows.every(w=>w.recess===0 && w.width===1)).toBe(true);
  for(const module of [1,2]){
    const layout=geometry.getAttribute('_kit_layout');
    for(let i=0;i<4;i++)layout.setX(i,module);
    expect(rectangularOpenings(geometry)).toHaveLength(2);
  }
});

it('admits a complete window below the eaves when its whole storey would not fit', () => {
  const geometry=patch(0);
  geometry.setAttribute('_kit_floors',new THREE.Float32BufferAttribute(Array(4).fill([2,2,0]).flat(),3));
  geometry.setAttribute('_kit_opening',new THREE.Float32BufferAttribute(Array(4).fill([.5,.25,.1,0]).flat(),4));
  const windows=rectangularOpenings(geometry);
  expect(windows).toHaveLength(2);
  expect(windows[1].origin.y).toBeCloseTo(2.2);
});

it('places only covered windows on a sloping roof wall without requiring a rectangular wall outline', () => {
  const geometry=patch(0);
  geometry.getAttribute('position').setY(3,2);
  geometry.getAttribute('uv').setY(3,2);
  geometry.setAttribute('_kit_floors',new THREE.Float32BufferAttribute(Array(4).fill([1,1,0]).flat(),3));
  geometry.setAttribute('_kit_opening',new THREE.Float32BufferAttribute(Array(4).fill([.5,.6,.2,0]).flat(),4));
  expect(rectangularOpenings(geometry).map(w=>Math.round(w.origin.y*10))).toEqual([2,12]);
});

it('does not bridge a missing wall rectangle even if the outer four wall corners survive', () => {
  const source=patch(0),geometry=new THREE.BufferGeometry();
  const uv:number[]=[];
  for(const [x,y,w,h] of [[0,0,2,.8],[0,1.8,2,1.2],[0,.8,.75,1],[1.25,.8,.75,1]])
    uv.push(x,y,x+w,y,x+w,y+h,x,y+h);
  for(const [name,attribute] of Object.entries(source.attributes)) {
    const values:number[]=[];
    for(let i=0;i<16;i++)for(let c=0;c<attribute.itemSize;c++)
      values.push(name==='uv'?uv[i*2+c]:name==='position'?(c===0?uv[i*2]:c===1?uv[i*2+1]:0):attribute.array[c]);
    geometry.setAttribute(name,new THREE.Float32BufferAttribute(values,attribute.itemSize));
  }
  geometry.setIndex(Array.from({length:4},(_,i)=>[i*4,i*4+2,i*4+1,i*4,i*4+3,i*4+2]).flat());
  geometry.setAttribute('_kit_floors',new THREE.Float32BufferAttribute(Array(16).fill([1,1,0]).flat(),3));
  geometry.setAttribute('_kit_opening',new THREE.Float32BufferAttribute(Array(16).fill([.5,.6,.2,0]).flat(),4));
  const windows=rectangularOpenings(geometry);
  expect(windows).toHaveLength(2);
  expect(windows.map(w=>Math.round(w.origin.y*10))).toEqual([2,22]);
});
