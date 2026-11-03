import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';

type Attribute = THREE.BufferAttribute | THREE.InterleavedBufferAttribute;
/** The r186 RenderObject fields used here; the class itself is not exported. */
export interface InstanceDraw {
  object: THREE.Object3D;
  geometry: THREE.BufferGeometry;
  context: { mrt: { has(name: string): boolean } | null };
  attributes: Attribute[] | null;
  vertexBuffers: (THREE.BufferAttribute | THREE.InterleavedBuffer)[] | null;
  initialCacheKey: number;
  getAttributes(): Attribute[];
  getMaterialCacheKey(): number;
  getDynamicCacheKey(): number;
  getNodeBuilderState(): object;
}
interface Nodes {
  getForRenderCacheKey(draw: InstanceDraw): number;
  _createNodeBuilderState(builder: NodeBuilder): object;
}
type Mesh = THREE.InstancedMesh & { isSkinnedMesh?: boolean };
type InstanceAttribute = THREE.InstancedBufferAttribute & { isStorageInstancedBufferAttribute?: boolean };

/** r186 normally emits array<mat4, instanceCount> uniforms: each car/tree batch size compiles a
 * different vertex program, including velocity and shadow passes. Its built-in attribute path
 * has count-independent shaders and retains per-object matrices/previous matrices correctly. */
export function instancePrograms(builder: NodeBuilder, object: THREE.Object3D) {
  if ((object as THREE.InstancedMesh).isInstancedMesh && !(object as THREE.SkinnedMesh).isSkinnedMesh)
    (builder as NodeBuilder & { getUniformBufferLimit(): number }).getUniformBufferLimit = () => 0;
}

const SHARED = 'sharedInstanceProgram', STATIC = 'staticInstances';
/** Opt in for an InstancedMesh whose materials read no object-specific data while their graph is built
 * (no builder.object lookups). Its built program is then shared by every such mesh with the same
 * material, geometry layout, pass and instance layout, so streamed tiles add instances, not builds.
 * `still`: instances never move (vegetation), so velocity passes may share too: their velocity is the camera's
 * alone, and a repacked pool never pairs a slot with another tree's previous matrix. */
export function shareInstanceProgram(mesh: THREE.InstancedMesh, still = false) {
  mesh.userData[SHARED] = true;
  if (still) mesh.userData[STATIC] = true;
}

/** r186 keys every InstancedMesh build by object.uuid because its instance nodes bind that mesh's own
 * matrix/colour buffers. Shared draws instead remap those vertex buffers per draw. Velocity passes keep
 * the per-object key (their previous-matrix copies are object hooks inside the built state) unless the instances
 * are still: each new city-wide tree pool mesh built its TRAA beauty programs inside a frame (census c9n7my:
 * nature-leaves 46, bark 37, impostor 34). */
export function sharesInstanceProgram(draw: InstanceDraw) {
  const mesh = draw.object as Mesh;
  return mesh.isInstancedMesh === true && mesh.userData[SHARED] === true && !mesh.isSkinnedMesh && !mesh.morphTexture
    && (mesh.instanceMatrix as InstanceAttribute).isStorageInstancedBufferAttribute !== true
    && (mesh.instanceColor as InstanceAttribute | null)?.isStorageInstancedBufferAttribute !== true
    && (draw.context.mrt?.has('velocity') !== true || mesh.userData[STATIC] === true);
}

/** The r186 render-object key without the per-object instancing term, plus the instance layout. */
export function sharedInstanceKey(draw: InstanceDraw) {
  const mesh = draw.object as Mesh, count = mesh.count;
  const instance = mesh as unknown as { isInstancedMesh: boolean };
  instance.isInstancedMesh = false; mesh.count = 1;
  let key: string;
  try { key = `${draw.getMaterialCacheKey()},${draw.getDynamicCacheKey()}`; }
  finally { instance.isInstancedMesh = true; mesh.count = count; }
  return THREE.NodeUtils.hashString(`instance-program:${key}:${mesh.instanceMatrix.usage}:${mesh.instanceColor?.usage ?? 'none'}`);
}

interface Own { buffer: THREE.InstancedInterleavedBuffer; columns: THREE.InterleavedBufferAttribute[] }
const ownMatrices = new WeakMap<THREE.InstancedBufferAttribute, Own>();
const ownColors = new WeakMap<THREE.InstancedBufferAttribute, THREE.InstancedBufferAttribute>();
function matrixColumns(source: THREE.InstancedBufferAttribute) {
  let own = ownMatrices.get(source);
  if (!own || own.buffer.array !== source.array) {
    const buffer = new THREE.InstancedInterleavedBuffer(source.array, 16, 1);
    buffer.setUsage(source.usage); buffer.version = -1;
    own = { buffer, columns: [0, 4, 8, 12].map(offset => new THREE.InterleavedBufferAttribute(buffer, 4, offset)) };
    ownMatrices.set(source, own);
  }
  return own;
}
function colorAttribute(source: THREE.InstancedBufferAttribute) {
  let own = ownColors.get(source);
  if (!own || own.array !== source.array) {
    own = new THREE.InstancedBufferAttribute(source.array, 3);
    own.setUsage(source.usage); own.version = -1;
    ownColors.set(source, own);
  }
  return own;
}
/** Mirror r186's per-frame instance update: copy changed ranges (or upload all) on the draw's own buffer. */
function follow(target: { version: number; clearUpdateRanges(): void; addUpdateRange(start: number, count: number): void },
  source: THREE.InstancedBufferAttribute) {
  if (target.version === source.version) return;
  target.clearUpdateRanges();
  for (const range of source.updateRanges) target.addUpdateRange(range.start, range.count);
  target.version = source.version;
}

/** Replace the build owner's instance vertex buffers by this draw's own buffers over the same arrays. */
export function remapInstanceAttributes(attributes: Attribute[], owner: Mesh, mesh: Mesh) {
  const geometry = new Set(Object.values(mesh.geometry.attributes));
  return attributes.map(attribute => {
    if (geometry.has(attribute)) return attribute;
    const interleaved = attribute as THREE.InterleavedBufferAttribute;
    if (interleaved.isInterleavedBufferAttribute && interleaved.data.array === owner.instanceMatrix.array && interleaved.itemSize === 4)
      return matrixColumns(mesh.instanceMatrix).columns[interleaved.offset / 4];
    if (owner.instanceColor && mesh.instanceColor && (attribute as THREE.BufferAttribute).array === owner.instanceColor.array)
      return colorAttribute(mesh.instanceColor);
    return attribute;
  });
}

/** Install after renderer initialization, when its node and geometry managers exist. */
export function shareInstancePrograms(renderer: THREE.WebGPURenderer) {
  const internal = renderer as unknown as { _nodes: Nodes; _geometries: { updateForRender(draw: InstanceDraw): void } };
  const nodes = internal._nodes, geometries = internal._geometries;
  const getKey = nodes.getForRenderCacheKey, createState = nodes._createNodeBuilderState, update = geometries.updateForRender;
  const keys = new WeakMap<InstanceDraw, number>(), shared = new WeakSet<InstanceDraw>();
  const owners = new WeakMap<object, Mesh>(), remapped = new WeakMap<InstanceDraw, Attribute[]>();
  type Prototype = { getAttributes(this: InstanceDraw): Attribute[] };
  let prototype: Prototype | null = null;
  let getAttributes: ((this: InstanceDraw) => Attribute[]) | null = null;
  // The render-object class is internal; patch its prototype from the first draw, before any shared draw.
  const patch = (draw: InstanceDraw) => {
    if (prototype) return;
    prototype = Object.getPrototypeOf(draw) as Prototype;
    const base = getAttributes = prototype.getAttributes;
    prototype.getAttributes = function () {
      const attributes = base.call(this);
      if (!shared.has(this) || remapped.get(this) === attributes) return attributes;
      const owner = owners.get(this.getNodeBuilderState());
      if (!owner) return attributes;
      const own = remapInstanceAttributes(attributes, owner, this.object as Mesh);
      this.attributes = own;
      this.vertexBuffers = [...new Set(own.map(value => (value as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute
        ? (value as THREE.InterleavedBufferAttribute).data : value as THREE.BufferAttribute))];
      remapped.set(this, own);
      return own;
    };
  };
  nodes.getForRenderCacheKey = function (draw) {
    let key = keys.get(draw);
    if (key !== undefined) return key;
    patch(draw);
    if (sharesInstanceProgram(draw)) { key = sharedInstanceKey(draw); shared.add(draw); }
    else key = getKey.call(this, draw);
    keys.set(draw, key);
    return key;
  };
  nodes._createNodeBuilderState = function (builder) {
    const state = createState.call(this, builder);
    const object = (builder as unknown as { object: Mesh | null }).object;
    if (object?.isInstancedMesh) owners.set(state, object);
    return state;
  };
  geometries.updateForRender = function (draw) {
    if (shared.has(draw)) {
      const mesh = draw.object as Mesh;
      const matrices = ownMatrices.get(mesh.instanceMatrix);
      if (matrices) follow(matrices.buffer, mesh.instanceMatrix);
      const colors = mesh.instanceColor && ownColors.get(mesh.instanceColor);
      if (colors) follow(colors, mesh.instanceColor!);
    }
    update.call(this, draw);
  };
  return {
    /** Shared states hold no object hook: previous instance matrices stay with each mesh's velocity pass. */
    builder(builder: NodeBuilder, draw: InstanceDraw) {
      if (shared.has(draw)) (builder as NodeBuilder & { needsPreviousData(): boolean }).needsPreviousData = () => false;
    },
    dispose() {
      nodes.getForRenderCacheKey = getKey; nodes._createNodeBuilderState = createState; geometries.updateForRender = update;
      if (prototype && getAttributes) prototype.getAttributes = getAttributes;
    },
  };
}
