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

const SHARED = 'sharedInstanceProgram', STATIC = 'staticInstances', MOVING = 'movingInstances';
/** Per-instance attributes a shared mesh's material binds by role while its graph is built (fleet paint and lamp
 *  states): the shared program reads the build owner's, and each draw binds its own mesh's attribute of that role. */
type Role = THREE.InstancedBufferAttribute | THREE.InterleavedBuffer;
const roles = new WeakMap<THREE.Object3D, Readonly<Record<string, Role>>>();
export interface ShareOptions {
  /** Instances move (traffic and parked cars): velocity passes share too, each mesh with its own previous-frame
   *  instance matrices (`previousColumns`), updated after its own draw as r186 does for the build owner. */
  moving?: boolean;
  /** Per-instance attributes the material reads from builder.object, by role: an attribute, or an instanced
   *  interleaved buffer whose fields the material reads (damaged batches pack paint, slot and lamps into one); every
   *  mesh sharing the program has the same roles (they are part of the program key). */
  attributes?: Readonly<Record<string, Role>>;
}
/** Opt in for an InstancedMesh whose materials read no object-specific data while their graph is built
 * (no builder.object lookups other than `options.attributes`). Its built program is then shared by every such mesh
 * with the same material, geometry layout, pass and instance layout, so streamed tiles add instances, not builds.
 * `still`: instances never move (vegetation), so velocity passes may share too: their velocity is the camera's
 * alone, and a repacked pool never pairs a slot with another tree's previous matrix. */
export function shareInstanceProgram(mesh: THREE.InstancedMesh, still = false, options: ShareOptions = {}) {
  mesh.userData[SHARED] = true;
  if (still) mesh.userData[STATIC] = true;
  if (options.moving && !still) mesh.userData[MOVING] = true;
  if (options.attributes) roles.set(mesh, options.attributes);
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
    && (draw.context.mrt?.has('velocity') !== true || mesh.userData[STATIC] === true || mesh.userData[MOVING] === true);
}
/** A shared draw that keeps r186's previous-frame instance data: moving instances in a velocity pass. */
const movingVelocity = (draw: InstanceDraw) => draw.context.mrt?.has('velocity') === true && draw.object.userData[MOVING] === true;

/** The r186 render-object key without the per-object instancing term, plus the instance layout. */
export function sharedInstanceKey(draw: InstanceDraw) {
  const mesh = draw.object as Mesh, count = mesh.count;
  const instance = mesh as unknown as { isInstancedMesh: boolean };
  instance.isInstancedMesh = false; mesh.count = 1;
  let key: string;
  try { key = `${draw.getMaterialCacheKey()},${draw.getDynamicCacheKey()}`; }
  finally { instance.isInstancedMesh = true; mesh.count = count; }
  const own = roles.get(mesh), layout = own ? Object.keys(own).sort().map(name => {
    const role = own[name];
    return `${name}/${(role as THREE.InterleavedBuffer).isInterleavedBuffer ? `i${(role as THREE.InterleavedBuffer).stride}` : (role as THREE.BufferAttribute).itemSize}/${role.usage}`;
  }).join(';') : '';
  return THREE.NodeUtils.hashString(`instance-program:${key}:${mesh.instanceMatrix.usage}:${mesh.instanceColor?.usage ?? 'none'}`
    + `:${movingVelocity(draw) ? 'moving' : ''}:${layout}`);
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

/** Each moving shared mesh's previous-frame instance matrices: r186 keeps them for the build owner only (a clone of
 *  its matrices behind module-private state), so the other meshes of a shared velocity program bind these. Starts as
 *  the current matrices (no motion on the first frame, as r186's clone). */
interface Previous { array: Float32Array; buffer: THREE.InstancedInterleavedBuffer; columns: THREE.InterleavedBufferAttribute[] }
const ownPrevious = new WeakMap<THREE.Object3D, Previous>();
export function previousColumns(mesh: Mesh, usage: THREE.Usage = mesh.instanceMatrix.usage) {
  let own = ownPrevious.get(mesh);
  if (!own || own.array.length !== mesh.instanceMatrix.array.length) {
    const array = new Float32Array(mesh.instanceMatrix.array as Float32Array), buffer = new THREE.InstancedInterleavedBuffer(array, 16, 1);
    buffer.setUsage(usage); buffer.version = mesh.instanceMatrix.version;
    own = { array, buffer, columns: [0, 4, 8, 12].map(offset => new THREE.InterleavedBufferAttribute(buffer, 4, offset)) };
    ownPrevious.set(mesh, own);
  }
  return own;
}
/** After a moving shared mesh's velocity draw: this frame's matrices become its previous ones (r186's
 *  OnAfterObjectUpdate in Instance.js, for every mesh of the program instead of its build owner alone). */
export function keepPreviousInstances(mesh: Mesh) {
  const own = ownPrevious.get(mesh);
  if (!own) return;
  own.array.set(mesh.instanceMatrix.array as Float32Array);
  own.buffer.version = mesh.instanceMatrix.version;
}

/** This mesh's view of a packed role buffer at the owner's field (offset and size): one per buffer and field. */
const fields = new WeakMap<THREE.InterleavedBuffer, Map<string, THREE.InterleavedBufferAttribute>>();
function field(buffer: THREE.InterleavedBuffer, like: THREE.InterleavedBufferAttribute) {
  let views = fields.get(buffer);
  if (!views) { views = new Map(); fields.set(buffer, views); }
  const key = `${like.offset}/${like.itemSize}/${like.normalized}`;
  let view = views.get(key);
  if (!view) {
    view = new THREE.InterleavedBufferAttribute(buffer, like.itemSize, like.offset, like.normalized);
    // r186's BufferAttributeNode marks the views it creates as instanced (the instance step of the vertex layout).
    (view as unknown as { isInstancedBufferAttribute?: boolean }).isInstancedBufferAttribute = (like as unknown as { isInstancedBufferAttribute?: boolean }).isInstancedBufferAttribute;
    views.set(key, view);
  }
  return view;
}

/** Replace the build owner's instance vertex buffers by this draw's own buffers over the same arrays. */
export function remapInstanceAttributes(attributes: Attribute[], owner: Mesh, mesh: Mesh) {
  const geometry = new Set(Object.values(mesh.geometry.attributes));
  const ownerRoles = roles.get(owner), meshRoles = roles.get(mesh);
  return attributes.map(attribute => {
    if (geometry.has(attribute)) return attribute;
    const interleaved = attribute as THREE.InterleavedBufferAttribute;
    if (interleaved.isInterleavedBufferAttribute && interleaved.data.array === owner.instanceMatrix.array && interleaved.itemSize === 4)
      return matrixColumns(mesh.instanceMatrix).columns[interleaved.offset / 4];
    // The owner's previous-frame matrices (velocity programs of moving instances): one 16-float instanced buffer
    // that is neither its matrices nor a role attribute.
    if (mesh.userData[MOVING] === true && interleaved.isInterleavedBufferAttribute && interleaved.itemSize === 4
      && interleaved.data.stride === 16 && (interleaved.data as unknown as { isInstancedInterleavedBuffer?: boolean }).isInstancedInterleavedBuffer === true)
      return previousColumns(mesh, interleaved.data.usage).columns[interleaved.offset / 4];
    if (owner.instanceColor && mesh.instanceColor && (attribute as THREE.BufferAttribute).array === owner.instanceColor.array)
      return colorAttribute(mesh.instanceColor);
    if (ownerRoles && meshRoles) for (const name in ownerRoles) {
      const role = ownerRoles[name], own = meshRoles[name];
      if (!own) continue;
      if (role === attribute) return own as THREE.InstancedBufferAttribute;
      if (interleaved.isInterleavedBufferAttribute && interleaved.data === role) return field(own as THREE.InterleavedBuffer, interleaved);
    }
    return attribute;
  });
}

/** r186's after-draw hook of a velocity program's instancing (Instance.js) looks up the previous matrices of the
 *  drawn object, which exist for the build owner only: for every other mesh of a shared program it would throw. Wrap
 *  it so the owner keeps r186's update and every mesh (owner included, whose draws bind `previousColumns` too) keeps
 *  its own. Recognised by the property it reads; the rest of the program's hooks are untouched. */
export function shareInstanceHooks(state: { updateAfterNodes?: { callback?: (frame: { object: THREE.Object3D | null }) => unknown }[] }, owner: Mesh) {
  for (const node of state.updateAfterNodes ?? []) {
    const original = node.callback;
    if (typeof original !== 'function' || !String(original).includes('previousInstanceMatrix')) continue;
    node.callback = frame => {
      const object = frame.object as Mesh | null;
      if (object === owner) original(frame);
      if (object?.isInstancedMesh) keepPreviousInstances(object);
    };
    // render/staticRefresh.ts: still the previous-matrix copy (equal values while instanceMatrix.version is unchanged).
    (node as { previousInstances?: boolean }).previousInstances = true;
  }
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
    if (object?.isInstancedMesh && object.userData[MOVING] === true) shareInstanceHooks(state as Parameters<typeof shareInstanceHooks>[0], object);
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
    /** Shared states hold no previous-frame instance data, except moving instances in velocity passes: there every
     *  mesh binds its own previous matrices (remapInstanceAttributes) and keeps them after its draw (shareInstanceHooks). */
    builder(builder: NodeBuilder, draw: InstanceDraw) {
      if (shared.has(draw) && !movingVelocity(draw)) (builder as NodeBuilder & { needsPreviousData(): boolean }).needsPreviousData = () => false;
    },
    dispose() {
      nodes.getForRenderCacheKey = getKey; nodes._createNodeBuilderState = createState; geometries.updateForRender = update;
      if (prototype && getAttributes) prototype.getAttributes = getAttributes;
    },
  };
}
