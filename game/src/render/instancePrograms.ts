import type * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';

/** r186 normally emits array<mat4, instanceCount> uniforms: each car/tree batch size compiles a
 * different vertex program, including velocity and shadow passes. Its built-in attribute path
 * has count-independent shaders and retains per-object matrices/previous matrices correctly.
 * Keep the builder object-specific: sharing its nodes would alias other instances' buffers. */
export function instancePrograms(builder: NodeBuilder, object: THREE.Object3D) {
  if ((object as THREE.InstancedMesh).isInstancedMesh && !(object as THREE.SkinnedMesh).isSkinnedMesh)
    (builder as NodeBuilder & { getUniformBufferLimit(): number }).getUniformBufferLimit = () => 0;
}
