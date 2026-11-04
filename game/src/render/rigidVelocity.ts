import * as THREE from 'three/webgpu';
import { cameraProjectionMatrix, cameraWorldMatrix, modelViewMatrix, modelWorldMatrixInverse, positionLocal, positionPrevious, positionView, renderGroup, uniform,
  velocity, vec4 } from 'three/tsl';

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

/** The current frame's projection for motion vectors: TRAA's unjittered matrix while its pipeline renders, else the
 *  camera's own. r186 instead picks it when a program is built: `projectionMatrix` is set only inside
 *  RenderPipeline.render, so every program built outside it (streaming preparation, compileModel) read the jittered
 *  camera projection. Its static pixels then moved by the sub-pixel jitter each frame against an unjittered previous
 *  frame, and TRAA's history walked with it: terrain and trees wandered while the camera stood still. */
export const velocityProjection = uniform(new THREE.Matrix4()).setName('velocityProjection').setGroup(renderGroup);

/** Rigid surfaces move only with their object matrix, so r186's velocity needs neither its interpolated local position
 *  nor positionPrevious: both clip positions follow from the fragment's view position (a varying every lit program
 *  already has) and the same current/previous object and camera matrices. That keeps beauty's MRT motion output
 *  within the 16 WebGPU inter-stage varyings of facade programs. Vertex programs (VAT walkers), skinning, morphs and
 *  instancing keep r186's per-vertex previous position. Both paths read `velocityProjection`, which is filled once per
 *  camera and frame from the same matrix r186 keeps as the next frame's previous projection.
 *  A/B `?taafix=0` (owner taa loop, until Jonni's standing-still retest; at most ten merges): r186's build-time choice. */
export function installRigidVelocity(buildTimeProjection = false) {
  const node = velocity as Velocity;
  if ((node as { rigid?: boolean }).rigid) return;
  const projection = () => !buildTimeProjection ? velocityProjection
    : node.projectionMatrix === null ? cameraProjectionMatrix : uniform(node.projectionMatrix);
  const update = node.update.bind(node);
  const frames = new WeakMap<THREE.Camera, number>();
  node.update = (frame: THREE.NodeFrame) => {
    const result = update(frame), camera = frame.camera;
    if (camera && frames.get(camera) !== frame.frameId) {
      frames.set(camera, frame.frameId);
      velocityProjection.value.copy(node.projectionMatrix ?? camera.projectionMatrix);
    }
    return result;
  };
  node.setup = (builder: unknown) => {
    const { object, geometry, material } = builder as Builder;
    // Still instances (render/instancePrograms.ts `staticInstances`: trees, facade modules) share one program per
    // material across meshes and repacked pools, built without previous instance matrices, so r186's
    // `positionPrevious` there is the bare geometry: every instance reported its whole placement as motion and TRAA
    // dropped its history (unresolved, shaking foliage). Their instances never move: without a vertex animation they
    // are rigid; with one (vegetation wind), the material's positionNode writes last frame's animated position into
    // `positionPrevious` (world/nature/materials.ts).
    const still = !!object?.isInstancedMesh && object.userData.staticInstances === true;
    const deformed = still ? !!(material.positionNode || material.vertexNode || material.userData.vertexProgram)
      : !object || object.isInstancedMesh || object.isBatchedMesh || object.isSkinnedMesh || geometry?.isInstancedBufferGeometry
      || Object.keys(geometry?.morphAttributes ?? {}).length > 0 || material.positionNode || material.vertexNode || material.userData.vertexProgram;
    const previousModelView = node.previousCameraViewMatrix.mul(node.previousModelWorldMatrix);
    let current: THREE.Node<'vec4'>, previous: THREE.Node<'vec4'>;
    if (deformed) {
      current = projection().mul(modelViewMatrix).mul(positionLocal);
      previous = node.previousProjectionMatrix.mul(previousModelView).mul(positionPrevious);
    } else {
      const view = vec4(positionView, 1);
      current = projection().mul(view);
      previous = node.previousProjectionMatrix.mul(previousModelView).mul(modelWorldMatrixInverse.mul(cameraWorldMatrix.mul(view)));
    }
    return current.xy.div(current.w).sub(previous.xy.div(previous.w));
  };
  (node as { rigid?: boolean }).rigid = true;
}
