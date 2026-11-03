import type * as THREE from 'three/webgpu';
import { cameraProjectionMatrix, cameraWorldMatrix, modelWorldMatrixInverse, positionView, uniform, velocity, vec4 } from 'three/tsl';

type Builder = {
  object?: THREE.Object3D & { isInstancedMesh?: boolean; isBatchedMesh?: boolean; isSkinnedMesh?: boolean } | null;
  geometry?: THREE.BufferGeometry & { isInstancedBufferGeometry?: boolean } | null;
  material: THREE.Material & { positionNode?: unknown; vertexNode?: unknown };
};
type Velocity = typeof velocity & {
  projectionMatrix: THREE.Matrix4 | null;
  previousProjectionMatrix: THREE.Node<'mat4'>; previousCameraViewMatrix: THREE.Node<'mat4'>; previousModelWorldMatrix: THREE.Node<'mat4'>;
  setup(builder: unknown): THREE.Node<'vec2'>;
};

/** Rigid surfaces move only with their object matrix, so r186's velocity needs neither its interpolated local position
 *  nor positionPrevious: both clip positions follow from the fragment's view position (a varying every lit program
 *  already has) and the same current/previous object and camera matrices. That keeps beauty's MRT motion output
 *  within the 16 WebGPU inter-stage varyings of facade programs. Vertex programs (VAT walkers), skinning, morphs and
 *  instancing keep r186's per-vertex previous position. TRAA's unjittered projection reaches both paths through the
 *  same shared node. */
export function installRigidVelocity() {
  const node = velocity as Velocity;
  if ((node as { rigid?: boolean }).rigid) return;
  const original = node.setup.bind(node);
  node.setup = (builder: unknown) => {
    const { object, geometry, material } = builder as Builder;
    const deformed = !object || object.isInstancedMesh || object.isBatchedMesh || object.isSkinnedMesh || geometry?.isInstancedBufferGeometry
      || Object.keys(geometry?.morphAttributes ?? {}).length > 0 || material.positionNode || material.vertexNode || material.userData.vertexProgram;
    if (deformed) return original(builder);
    const projection = node.projectionMatrix === null ? cameraProjectionMatrix : uniform(node.projectionMatrix);
    const view = vec4(positionView, 1);
    const local = modelWorldMatrixInverse.mul(cameraWorldMatrix.mul(view));
    const current = projection.mul(view);
    const previous = node.previousProjectionMatrix.mul(node.previousCameraViewMatrix.mul(node.previousModelWorldMatrix)).mul(local);
    return current.xy.div(current.w).sub(previous.xy.div(previous.w));
  };
  (node as { rigid?: boolean }).rigid = true;
}
