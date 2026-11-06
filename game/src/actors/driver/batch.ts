import * as THREE from 'three/webgpu';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { attribute, float, instancedBufferAttribute, int, ivec2, mat3, mat4, normalize, normalLocal, positionLocal, positionPrevious, step, textureLoad, vec4 } from 'three/tsl';
import type { DriverModel } from './model';

/** Every animated driver in one instanced draw (README "Cost": on the M1 Air each draw costs about 90 µs of frame time,
 *  instanced or not). Each driver owns a layer (its pool slot) of a bone texture: its skeleton's matrices in its seat
 *  frame, written when the rig poses it; the instance matrix carries the seat to the world every frame, so a driver
 *  posed on alternate frames still rides with its car. The vertex stage blends the four bones of each vertex from the
 *  instance's layer, and collapses the detail tiers beyond the instance's (model.ts TIERS: the wheel, the legs, the
 *  face fall away with distance). A second texture keeps each layer's previous pose for the motion vectors. */

/** Drivers drawn at once at most (pool slots; one bone-texture layer each). */
export const BATCH_SLOTS = 64;
type Builder = NodeBuilder & { needsPreviousData(): boolean };

function boneTexture(width: number, name: string) {
  const texture = new THREE.DataArrayTexture(new Float32Array(width * 4 * BATCH_SLOTS), width, 1, BATCH_SLOTS);
  texture.format = THREE.RGBAFormat; texture.type = THREE.FloatType;
  texture.minFilter = texture.magFilter = THREE.NearestFilter;
  texture.generateMipmaps = false; texture.colorSpace = THREE.NoColorSpace;
  texture.name = name;
  texture.needsUpdate = true;
  return texture;
}

export class DriverBatch {
  readonly mesh: THREE.InstancedMesh;
  /** Floats per layer: four texels (one matrix) per bone. */
  readonly stride: number;
  private readonly bones: THREE.DataArrayTexture;
  private readonly previous: THREE.DataArrayTexture;
  /** Per instance: its driver's layer (x) and detail tier (y). */
  private readonly instances: THREE.InstancedBufferAttribute;
  /** Layers uploaded once (r186 sends every layer on a texture's first upload, then only the marked ones). */
  private readonly uploaded = new WeakSet<THREE.Texture>();
  private count = 0;

  constructor(model: DriverModel, boneCount: number) {
    this.stride = boneCount * 16;
    this.bones = boneTexture(boneCount * 4, 'driver-bones');
    this.previous = boneTexture(boneCount * 4, 'driver-bones-previous');
    for (const texture of [this.bones, this.previous]) texture.onUpdate = () => { this.uploaded.add(texture); };
    this.instances = new THREE.InstancedBufferAttribute(new Float32Array(BATCH_SLOTS * 2), 2);
    this.instances.setUsage(THREE.DynamicDrawUsage);
    const material = model.material.clone() as THREE.MeshStandardNodeMaterial;
    material.name = 'driver-batch';
    material.roughnessNode = attribute('driverRoughness', 'float');
    // Vertices move before the instance matrix: velocity and the normal prepass keep this program (rigidVelocity.ts).
    material.userData.vertexProgram = true;
    const setup = material.setupPosition.bind(material);
    const { bones, previous, instances } = this;
    material.setupPosition = (builder: NodeBuilder) => {
      const index = attribute('skinIndex', 'uvec4'), weight = attribute('skinWeight', 'vec4');
      const instance = instancedBufferAttribute(instances, 'vec2') as unknown as Node<'vec2'>;
      const layer = int(instance.x.add(.5));
      const load = (texture: THREE.DataArrayTexture, column: Node<'int'>) =>
        (textureLoad(texture, ivec2(column, int(0))) as unknown as { depth(node: Node<'int'>): Node<'vec4'> }).depth(layer) as Node<'vec4'>;
      const bone = (texture: THREE.DataArrayTexture, k: 'x' | 'y' | 'z' | 'w') => {
        const column = int(index[k]).mul(4);
        return mat4(load(texture, column), load(texture, column.add(1)), load(texture, column.add(2)), load(texture, column.add(3)));
      };
      const skin = (texture: THREE.DataArrayTexture) => weight.x.mul(bone(texture, 'x')).add(weight.y.mul(bone(texture, 'y')))
        .add(weight.z.mul(bone(texture, 'z'))).add(weight.w.mul(bone(texture, 'w')));
      // 0 for a vertex of a tier beyond the instance's detail: it collapses onto the seat origin (zero-area triangles).
      const keep = float(1).sub(step(instance.y.add(.5), attribute('driverTier', 'float')));
      const position = vec4(attribute('position', 'vec3'), 1), matrix = skin(bones);
      positionLocal.assign(matrix.mul(position).xyz.mul(keep));
      normalLocal.assign(normalize(mat3(matrix).mul(attribute('normal', 'vec3'))));
      if ((builder as Builder).needsPreviousData()) positionPrevious.assign(skin(previous).mul(position).xyz.mul(keep));
      return setup(builder);
    };
    this.mesh = new THREE.InstancedMesh(model.geometry, material, BATCH_SLOTS);
    this.mesh.name = 'drivers';
    this.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.mesh.castShadow = false; this.mesh.receiveShadow = true;
    // Drivers are culled one by one on the CPU (drivers.ts); the batch spans the whole drawn set.
    this.mesh.frustumCulled = false;
    this.mesh.count = 0;
  }

  /** Writes slot `slot`'s seat-frame bone matrices (`matrices`, `stride` floats); the previous layer keeps the pose it
   *  replaces (`fresh`: a newly seated driver, no motion from the slot's last occupant). */
  pose(slot: number, matrices: Float32Array, fresh = false) {
    const current = this.bones.image.data as Float32Array, last = this.previous.image.data as Float32Array, at = slot * this.stride;
    last.set(fresh ? matrices : current.subarray(at, at + this.stride), at);
    current.set(matrices, at);
    for (const texture of [this.bones, this.previous]) {
      if (this.uploaded.has(texture)) texture.addLayerUpdate(slot);
      texture.needsUpdate = true;
    }
  }

  /** Starts a frame's drawn set. */
  begin() { this.count = 0; }

  /** Draws slot `slot`'s driver this frame at `seat` (its seat frame's world matrix), with detail tier `tier`. */
  add(slot: number, seat: THREE.Matrix4, tier: number) {
    if (this.count >= BATCH_SLOTS) return;
    const i = this.count++;
    this.mesh.setMatrixAt(i, seat);
    this.instances.setXY(i, slot, tier);
  }

  /** Ends the frame: the instances drawn and their uploads. */
  end() {
    this.mesh.count = this.count;
    this.mesh.visible = this.count > 0;
    if (!this.count) return;
    this.mesh.instanceMatrix.needsUpdate = true;
    this.instances.needsUpdate = true;
  }
}
