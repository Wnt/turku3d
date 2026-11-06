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
const instancePaints = new WeakMap<THREE.Object3D, THREE.InstancedBufferAttribute | THREE.InterleavedBufferAttribute>();
const sharedNodes = new WeakMap<THREE.InterleavedBufferAttribute, Node>();
/** The node that reads one attribute of a shared instanced buffer: one node per attribute, so every stage and every
 *  read of it binds the same vertex attribute (a node with a stride is not deduplicated by its buffer). */
export function sharedNode(attribute: THREE.InterleavedBufferAttribute, type: 'float' | 'vec3' | 'vec4'): Node {
  let node = sharedNodes.get(attribute);
  if (!node) { node = instancedBufferAttribute(attribute.data, type, attribute.data.stride, attribute.offset) as unknown as Node; sharedNodes.set(attribute, node); }
  return node;
}
/** A batch whose paints share an instanced buffer with other per-instance data (actors/fleet/damageRig.ts damageBatch:
 *  WebGPU binds at most eight vertex buffers per draw): `attribute` is its paint (rgb), written by paintInstances. */
export function sharePaintBuffer(mesh: THREE.InstancedMesh, attribute: THREE.InterleavedBufferAttribute) { instancePaints.set(mesh, attribute); }
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

/** Object3D.clone() does not carry the paint (it lives in a WeakMap by mesh): copy it onto every cloned mesh,
 *  pairing source and clone by traversal order. An unpainted fleet mesh fails its program build and is not drawn. */
export function copyActorPaint(source: THREE.Object3D, clone: THREE.Object3D) {
  const from: THREE.Object3D[] = [], to: THREE.Object3D[] = [];
  source.traverse(o => { from.push(o); }); clone.traverse(o => { to.push(o); });
  from.forEach((o, i) => { const paint = actorPaints.get(o); if (paint && to[i]) actorPaints.set(to[i], paint.clone()); });
}

/** Gives `target` the paint of the actor mesh `source` (a car part's debris copy); false when `source` has none. */
export function shareActorPaint(source: THREE.Object3D, target: THREE.Object3D): boolean {
  const paint = actorPaints.get(source);
  if (paint) actorPaints.set(target, paint.clone());
  return !!paint;
}

export function instancePaintOf(mesh: THREE.Object3D) { return instancePaints.get(mesh); }
export function actorPaintOf(mesh: THREE.Object3D) { return actorPaints.get(mesh); }

// Node graphs build per object: an instanced batch binds its own paint buffer, a standalone draw
// its object uniform. Neither the count nor the paints enter the shader text.
export const paintNode = Fn((builder: NodeBuilder) => {
  const instances = instancePaints.get(builder.object);
  if (instances) return (instances as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute
    ? sharedNode(instances as THREE.InterleavedBufferAttribute, 'vec3')
    : instancedBufferAttribute(instances as THREE.InstancedBufferAttribute, 'vec3');
  if (!actorPaints.has(builder.object)) throw new Error(`parked paint missing for ${builder.object.name}`);
  return uniform(new THREE.Vector3()).onObjectUpdate(({ object }) => actorPaints.get(object!));
}) as unknown as () => Node<'vec3'>;

