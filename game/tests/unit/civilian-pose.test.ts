import { test, expect } from 'vitest';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { MeshoptDecoder } from 'meshoptimizer';
import { Matrix4, Quaternion, Vector3 } from 'three';
import { civilianPose } from '../../scripts/lib/civilian-pose.mjs';

test('civilian calibration narrows outward arms without changing legs, segment length or accumulating', async () => {
 const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.decoder':MeshoptDecoder});
 const doc=await io.read(new URL('../../public/assets/character/player.glb',import.meta.url).pathname);
 const joints=doc.getRoot().listSkins()[0].listJoints(), nodes=new Map(joints.map(n=>[n.getName(),n]));
 const clip=doc.getRoot().listAnimations().find(a=>a.getName()==='Walk_Loop')!;
 const pose=civilianPose(joints), world=(n:any)=>new Vector3().setFromMatrixPosition(new Matrix4().fromArray(n.getWorldMatrix()));
 let before=0,after=0;
 for(let frame=0;frame<40;frame++){
  pose.restore();
  const t=frame/30;
  for(const c of clip.listChannels()){
   const s=c.getSampler()!,times=s.getInput()!.getArray()!,out=s.getOutput()!,node=c.getTargetNode()!;
   let k=0;while(k<times.length-2&&times[k+1]<=t)k++;
   const u=(t-times[k])/(times[k+1]-times[k]),a=out.getElement(k,[]),b=out.getElement(k+1,[]);
   if(c.getTargetPath()==='rotation')node.setRotation(new Quaternion().fromArray(a).slerp(new Quaternion().fromArray(b),u).toArray());
   else if(c.getTargetPath()==='translation')node.setTranslation(a.map((v,i)=>v+(b[i]-v)*u) as [number,number,number]);
  }
  const legs=['DEF-hips','DEF-thigh.L','DEF-thigh.R','DEF-foot.L','DEF-foot.R'].map(name=>[...nodes.get(name)!.getWorldMatrix()]);
  const arm=nodes.get('DEF-upper_arm.L')!,elbow=nodes.get('DEF-forearm.L')!,hand=nodes.get('DEF-hand.L')!;
  const length=world(elbow).distanceTo(world(arm));before+=Math.abs(world(hand).x-world(arm).x);
  const original=[...arm.getRotation()];pose.apply();after+=Math.abs(world(hand).x-world(arm).x);
  expect(world(elbow).distanceTo(world(arm))).toBeCloseTo(length,6);
  legs.forEach((matrix,i)=>expect([...nodes.get(['DEF-hips','DEF-thigh.L','DEF-thigh.R','DEF-foot.L','DEF-foot.R'][i])!.getWorldMatrix()]).toEqual(matrix));
  pose.restore();expect([...arm.getRotation()]).toEqual(original);
 }
 console.log(JSON.stringify({meanLateralReachBefore:before/40,meanLateralReachAfter:after/40}));
 expect(after).toBeLessThan(before*.7);
});
