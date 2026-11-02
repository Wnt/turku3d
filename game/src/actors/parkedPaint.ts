import * as THREE from 'three/webgpu';
import { Fn, instancedBufferAttribute, uniform } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';

export const PAINT_COLORS: Readonly<Record<string, readonly [number, number, number]>> = {
  silver: [165, 174, 181], white: [224, 228, 225], charcoal: [54, 61, 69],
  black: [23, 26, 30], grey: [100, 106, 111], blue: [38, 78, 126], red: [138, 38, 39], beige: [154, 143, 118],
};
/** Paint as the shader reads it: sRGB in 0..1. */
export function paintOf(color: string): readonly [number, number, number] {
  const [r, g, b] = PAINT_COLORS[color] ?? PAINT_COLORS.silver;
  return [r / 255, g / 255, b / 255];
}

// A batch's per-instance paints are a node attribute of that draw, like its instance matrices:
// r186 binds the InstancedBufferAttribute itself (instance step, re-uploaded on needsUpdate), and
// the renderer releases it with the InstancedMesh. A driven or remote car
// carries its paint as an object uniform, so it needs no buffer or geometry of its own.
const instancePaints = new WeakMap<THREE.Object3D, THREE.InstancedBufferAttribute>();
const actorPaints = new WeakMap<THREE.Object3D, THREE.Vector3>();

/** Writes `colors` as the paints of instances offset..offset+n-1 of `mesh` (0..n-1 by default). */
export function paintInstances(mesh: THREE.InstancedMesh, colors: readonly string[], offset = 0) {
  let paints = instancePaints.get(mesh);
  if (!paints) {
    paints = new THREE.InstancedBufferAttribute(new Float32Array(mesh.instanceMatrix.count * 3), 3);
    instancePaints.set(mesh, paints);
  }
  let dirty=false;
  colors.forEach((color,i)=>{const [r,g,b]=paintOf(color),at=offset+i;
    if(paints.getX(at)!==Math.fround(r)||paints.getY(at)!==Math.fround(g)||paints.getZ(at)!==Math.fround(b)){paints.setXYZ(at,r,g,b);dirty=true;}
  });
  if(dirty)paints.needsUpdate = true;
}

export function paintActor(mesh: THREE.Mesh, color: string) {
  actorPaints.set(mesh, new THREE.Vector3(...paintOf(color)));
}

export function instancePaintOf(mesh: THREE.Object3D) { return instancePaints.get(mesh); }
export function actorPaintOf(mesh: THREE.Object3D) { return actorPaints.get(mesh); }

// Node graphs build per object: an instanced batch binds its own paint buffer, a standalone draw
// its object uniform. Neither the count nor the paints enter the shader text.
export const paintNode = Fn((builder: NodeBuilder) => {
  const instances = instancePaints.get(builder.object);
  if (instances) return instancedBufferAttribute(instances, 'vec3');
  if (!actorPaints.has(builder.object)) throw new Error(`parked paint missing for ${builder.object.name}`);
  return uniform(new THREE.Vector3()).onObjectUpdate(({ object }) => actorPaints.get(object!));
}) as unknown as () => Node<'vec3'>;

