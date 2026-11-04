import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { cameraProjectionMatrix, positionLocal, positionPrevious, renderGroup, velocity } from 'three/tsl';
import TRAANode from 'three/addons/tsl/display/TRAANode.js';
import { installRigidVelocity, velocityProjection } from '../../src/render/rigidVelocity';
import { freezeModel } from '../../src/world/frozenTransforms';
import { shareInstanceProgram } from '../../src/render/instancePrograms';

type Velocity = typeof velocity & {
  projectionMatrix: THREE.Matrix4 | null;
  previousProjectionMatrix: THREE.UniformNode<'mat4', THREE.Matrix4>;
  previousCameraViewMatrix: THREE.UniformNode<'mat4', THREE.Matrix4>;
  previousModelWorldMatrix: THREE.UniformNode<'mat4', THREE.Matrix4>;
  setup(builder: unknown): THREE.Node;
  update(frame: { frameId: number; camera: THREE.Camera; object: THREE.Object3D }): void;
  updateAfter(frame: { object: THREE.Object3D }): void;
};
/** r186 TRAANode's per-frame camera handling, which RenderPipeline.render brackets with its callbacks. */
type Traa = TRAANode & { _velocityNode: unknown; setViewOffset(w: number, h: number): void; clearViewOffset(): void };

installRigidVelocity();
const node = velocity as Velocity;

const traaFor = (camera: THREE.PerspectiveCamera) => {
  const traa = new TRAANode(null as never, null as never, null as never, camera) as Traa;
  traa._velocityNode = node;
  return traa;
};
const ids = (root: THREE.Node) => { const seen = new Set<number>(); root.traverse(child => seen.add(child.id)); return seen; };
const builderOf = (object: THREE.Mesh) => ({ object, geometry: object.geometry, material: object.material });

test('static frozen terrain and trees have zero TRAA velocity while the camera is still, wherever their programs were built', () => {
  const width = 1134, height = 737; // a 1512x982 window at pixel ratio 0.75
  const camera = new THREE.PerspectiveCamera(55, width / height, 0.5, 6000);
  camera.position.set(3, 41.5, 7); camera.lookAt(30, 38, -60); camera.updateMatrixWorld();
  const traa = traaFor(camera);
  // A streamed tile as VisualTiles publishes it: frozen ground and an instanced tree layer.
  const scene = new THREE.Scene(); scene.updateMatrix(); scene.matrixAutoUpdate = false;
  const tile = new THREE.Group(); tile.position.set(512, 0, -512);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(512, 512), new THREE.MeshStandardNodeMaterial()); ground.rotation.x = -Math.PI / 2;
  const trees = new THREE.InstancedMesh(new THREE.ConeGeometry(2, 8), new THREE.MeshStandardNodeMaterial(), 4);
  tile.add(ground, trees); scene.add(tile); freezeModel(tile); scene.updateMatrixWorld();

  // Streaming preparation (compileModel) builds programs between frames, where TRAA has cleared the velocity
  // projection; a first draw builds its program inside the pipeline, where it is set.
  node.projectionMatrix = null;
  const programs = [[ground, node.setup(builderOf(ground))], [trees, node.setup(builderOf(trees))]] as [THREE.Mesh, THREE.Node][];
  traa.setViewOffset(width, height);
  programs.push([ground, node.setup(builderOf(ground))]);
  traa.clearViewOffset();
  for (const [, program] of programs) {
    expect(ids(program).has(velocityProjection.id)).toBe(true);
    expect(ids(program).has(cameraProjectionMatrix.id)).toBe(false);
  }

  const points = [new THREE.Vector3(30, 0, -40), new THREE.Vector3(-200, 3, 100)];
  const unjittered = new THREE.Matrix4(), previousView = new THREE.Matrix4(), previousProjection = new THREE.Matrix4();
  let still = 0, motionError = 0;
  for (let frameId = 1; frameId <= 64; frameId++) {
    // From frame 40 the camera turns slowly: motion vectors must still equal the true, unjittered motion.
    if (frameId >= 40) { camera.rotation.y += 0.0004; camera.updateMatrixWorld(); }
    scene.updateMatrixWorld();
    camera.updateProjectionMatrix(); unjittered.copy(camera.projectionMatrix);
    traa.setViewOffset(width, height);
    for (const [object] of programs) {
      node.update({ frameId, camera, object });
      for (const local of points) {
        const world = local.clone().applyMatrix4(object.matrixWorld);
        const now = world.clone().applyMatrix4(camera.matrixWorldInverse).applyMatrix4(velocityProjection.value);
        const then = local.clone().applyMatrix4(node.previousModelWorldMatrix.value)
          .applyMatrix4(node.previousCameraViewMatrix.value).applyMatrix4(node.previousProjectionMatrix.value);
        const px = (a: THREE.Vector3, b: THREE.Vector3) => Math.max(Math.abs(a.x - b.x) * width / 2, Math.abs(a.y - b.y) * height / 2);
        if (frameId < 40) still = Math.max(still, px(now, then));
        else if (frameId > 40) {
          const truth = world.clone().applyMatrix4(camera.matrixWorldInverse).applyMatrix4(unjittered);
          const before = world.clone().applyMatrix4(previousView).applyMatrix4(previousProjection);
          motionError = Math.max(motionError, Math.abs(px(now, then) - px(truth, before)));
        }
      }
      node.updateAfter({ object });
    }
    previousView.copy(camera.matrixWorldInverse); previousProjection.copy(unjittered);
    traa.clearViewOffset();
  }
  // Before the shared projection, prepared programs read the jittered camera projection: 0.49 px of
  // "motion" every frame on static pixels, which TRAA's history followed.
  expect(still).toBeLessThan(1e-4);
  expect(motionError).toBeLessThan(1e-4);
});

test('both velocity paths share one render-group projection uniform', () => {
  const rigid = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardNodeMaterial());
  const deformed = new THREE.InstancedMesh(new THREE.BoxGeometry(), new THREE.MeshStandardNodeMaterial(), 2);
  for (const projection of [null, new THREE.Matrix4()]) {
    node.projectionMatrix = projection;
    for (const mesh of [rigid, deformed]) expect(ids(node.setup(builderOf(mesh))).has(velocityProjection.id)).toBe(true);
  }
  node.projectionMatrix = null;
  expect(velocityProjection.groupNode).toBe(renderGroup);
});

test('still shared instances never read the bare geometry as last frame\'s position', () => {
  // Shared still-instance programs carry no previous instance matrices (render/instancePrograms.ts), so r186's
  // positionPrevious there is the untransformed geometry: each tree reported its placement as motion and TRAA
  // dropped the crowns' history. Rigid still instances take the rigid path; an animated one (vegetation wind)
  // reads the positionPrevious its own positionNode writes.
  const tree = new THREE.InstancedMesh(new THREE.BoxGeometry(), new THREE.MeshStandardNodeMaterial(), 2);
  shareInstanceProgram(tree, true);
  expect(ids(node.setup(builderOf(tree))).has(positionPrevious.id)).toBe(false);
  const swaying = new THREE.MeshStandardNodeMaterial(); swaying.positionNode = positionLocal;
  const crown = new THREE.InstancedMesh(new THREE.BoxGeometry(), swaying, 2);
  shareInstanceProgram(crown, true);
  expect(ids(node.setup(builderOf(crown))).has(positionPrevious.id)).toBe(true);
  // Moving instances (cars) keep r186's previous instance matrices.
  const cars = new THREE.InstancedMesh(new THREE.BoxGeometry(), new THREE.MeshStandardNodeMaterial(), 2);
  expect(ids(node.setup(builderOf(cars))).has(positionPrevious.id)).toBe(true);
});

test('TRAA jitter stays sub-pixel and centred in drawing-buffer pixels at pixel ratios 0.75, 1 and 1.5', () => {
  for (const ratio of [0.75, 1, 1.5]) {
    const width = Math.floor(1512 * ratio), height = Math.floor(982 * ratio);
    const camera = new THREE.PerspectiveCamera(55, 1512 / 982, 0.5, 6000);
    camera.updateMatrixWorld();
    const traa = traaFor(camera);
    const point = new THREE.Vector3(4, -2, -30);
    const offsets: [number, number][] = [];
    for (let frame = 0; frame < 32; frame++) {
      camera.updateProjectionMatrix();
      const plain = point.clone().applyMatrix4(camera.projectionMatrix);
      traa.setViewOffset(width, height);
      const jittered = point.clone().applyMatrix4(camera.projectionMatrix);
      traa.clearViewOffset();
      offsets.push([(jittered.x - plain.x) * width / 2, (jittered.y - plain.y) * height / 2]);
    }
    for (const [x, y] of offsets) { expect(Math.abs(x)).toBeLessThanOrEqual(0.5); expect(Math.abs(y)).toBeLessThanOrEqual(0.5); }
    const mean = offsets.reduce((sum, [x, y]) => [sum[0] + x / 32, sum[1] + y / 32], [0, 0]);
    expect(Math.hypot(mean[0], mean[1])).toBeLessThan(0.05);
  }
  node.projectionMatrix = null;
});
