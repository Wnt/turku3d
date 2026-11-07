import { materialEnvIntensity, materialEnvRotation, modelNormalMatrix, modelWorldMatrixInverse, velocity } from 'three/tsl';

/** r186 refreshes every node-material draw in full on every frame: NodeMaterialObserver.needsRefresh returns FULL for
 *  any material with a node property (`hasNode`) and for every draw of a pass with a velocity MRT (beauty under TRAA),
 *  before it compares anything. A full refresh runs the program's per-object node updates, re-checks every uniform of
 *  its object groups, every vertex attribute and every bound texture. Its `object.static` and bundle shortcuts only
 *  ever served classic materials. Windows (Radeon 560X laptop), city.lab play: the main thread was ~100 % busy and
 *  draws took 37 % of it (win-prof1); the M1 Max's headless play was the same (mac-fix4-1, 57 %).
 *
 *  A draw whose per-object state the observer can see in full takes the observer's own classic-material path: it is
 *  refreshed once per program and pass (SHARED, so the program's shared groups and render-wide nodes still update)
 *  and skipped after that (NONE) while `equals` finds its world matrix, material uniforms, geometry, instancing,
 *  lights and environment unchanged. The observer sees all of it when every per-object node of the program reads only
 *  those: the object's world matrix (Object3DNode/ModelNode scopes of matrixWorld, the normal and inverse matrices),
 *  the draw's own material (the observer's `refreshUniforms`, which `equals` compares, and any other property, whose
 *  primitive or texture value is compared per draw), motion vectors (VelocityNode), its textures'
 *  uv transform and flip (TextureNode; no texture transform is animated after load), references and uniform arrays in a
 *  shared group (the scene's fog, CSM's cascades), which the program's SHARED refresh updates, and r186's copy of
 *  instance matrices into the previous ones after a velocity draw (equal while instanceMatrix.version is). A program with
 *  any other per-object update (onObjectUpdate callbacks, texture transforms, camera-relative uniforms), or a draw with
 *  a per-object uniform no such node owns (a value code may set at any time), keeps r186's full refresh. Bound
 *  textures are checked per draw (identity and version, as Bindings._update does), so a swap or an upload still binds.
 *  After any change a draw refreshes in full for one more frame: the motion-vector update keeps last frame's matrix.
 *  `audit(frames)` runs the full update behind every skipped draw for a while and reports what it would have missed. */
export const Refresh = { NONE: 0, SHARED: 1, FULL: 2 } as const;

interface Node {
  updateType?: string; updateBeforeType?: string; updateAfterType?: string; isUniformNode?: boolean; name?: string;
  isMaterialReferenceNode?: boolean; material?: unknown; property?: string; scope?: string; node?: Node | null;
  uniformNode?: Node; previousModelWorldMatrix?: Node; isUniformGroup?: boolean; _matrixUniform?: Node | null;
  _flipYUniform?: Node | null; group?: { shared?: boolean } | null; groupNode?: { shared?: boolean; name?: string } | null;
  callback?: unknown; previousInstances?: boolean; value?: unknown; constructor: { type?: string };
}
export interface Binding {
  isSampledTexture?: boolean; isNodeUniformsGroup?: boolean; isUniformBuffer?: boolean; isStorageBuffer?: boolean;
  groupNode?: { shared?: boolean }; name?: string;
  texture?: { version: number } | null; textureNode?: { value: unknown } | null; version?: number;
  uniforms?: { nodeUniform?: { node?: Node } }[];
  update?(): boolean; clearUpdateRanges?(): void; updateRanges?: unknown[];
}
export interface State { updateNodes: Iterable<Node>; updateBeforeNodes: Iterable<Node>; updateAfterNodes: Iterable<Node> }
export interface Monitor {
  hasAnimation: boolean; renderId: number; refreshUniforms: readonly string[];
  hasDynamicInstancing(object: unknown): boolean; firstInitialization(renderObject: Draw): boolean;
  getLights(lightsNode: unknown, renderId: number): unknown; equals(renderObject: Draw, lights: unknown, renderId: number): boolean;
}
export interface Draw {
  object: { name?: string; isSkinnedMesh?: boolean; isBatchedMesh?: boolean; morphTargetInfluences?: unknown };
  material?: { name?: string }; lightsNode: unknown;
  getMonitor(): Monitor; getNodeBuilderState(): State; getBindings(): { bindings: Binding[] }[];
}
interface Nodes {
  needsRefresh(renderObject: Draw): number; getNodeFrameForRender(renderObject: Draw): { renderId: number };
  updateForRender(renderObject: Draw): void;
}

const OBJECT = 'object';
/** Object3DNode scopes that follow from matrixWorld alone (viewPosition reads the camera too). */
const WORLD_SCOPES = new Set(['worldMatrix', 'position', 'scale', 'direction']);
/** r186's per-object uniforms the observer's `equals` sees the inputs of: the normal and inverse world matrices
 *  (matrixWorld), and the environment's intensity and rotation (material.envMap*, scene.environmentIntensity and
 *  environmentRotation; every lit material samples the IBL: 1,592 of 2,044 draws stayed full for these). */
const MATRIX_UNIFORMS = new Set<unknown>([modelNormalMatrix, modelWorldMatrixInverse, materialEnvIntensity, materialEnvRotation]);

const valueKind = (value: unknown) => {
  const v = value as Record<string, unknown> | null | undefined;
  if (v === null || v === undefined || typeof v !== 'object') return typeof v;
  for (const kind of ['isMatrix3', 'isMatrix4', 'isVector2', 'isVector3', 'isVector4', 'isColor', 'isTexture']) if (v[kind]) return kind.slice(2);
  return 'object';
};
const describe = (node: Node) => `${node.constructor.type ?? '?'}:${node.scope ?? node.property ?? node.name ?? ''}`
  + `:${valueKind(node.value)}:${node.groupNode?.name ?? ''}:#${(node as { id?: number }).id ?? '?'}`;
/** r186's after-draw copy of an instanced mesh's matrices into its previous ones (Instance.js, or instancePrograms'
 *  wrapper of it): with unchanged matrices (the observer compares instanceMatrix.version) it copies equal values. */
const previousInstances = (node: Node) => node.previousInstances === true
  || (typeof node.callback === 'function' && String(node.callback).includes('previousInstanceMatrix'));

/** Whether the observer's `equals` covers every per-object node update of the program. */
export function observable(state: State): boolean {
  return uncovered(state) === null;
}

/** The first per-object node update the observer does not cover (its class and scope/property/name), else null. */
export function uncovered(state: State): string | null {
  for (const node of state.updateBeforeNodes) if (node.updateBeforeType === OBJECT) return `before ${describe(node)}`;
  for (const node of state.updateAfterNodes)
    if (node.updateAfterType === OBJECT && node !== velocity && !previousInstances(node)) return `after ${describe(node)}`;
  for (const node of state.updateNodes) {
    if (node.updateType !== OBJECT) continue;
    if (node === velocity || MATRIX_UNIFORMS.has(node) || node.isUniformGroup) continue;
    const type = node.constructor.type;
    // A texture's uv transform and flip: set from the texture when it is made (none is animated after load); a
    // texture swap is a binding change texturesChanged sees.
    if (type === 'TextureNode') continue;
    if ((type === 'Object3DNode' || type === 'ModelNode') && WORLD_SCOPES.has(node.scope ?? '')) continue;
    // The draw's own material: a refresh uniform equals() compares, or any other property (userData.weatherReceiver,
    // render/post.ts) whose value needsRefresh compares per draw (referencePaths).
    if (node.isMaterialReferenceNode && node.material === null) continue;
    // A reference into a shared group (r186's scene fog: colour, near, far in the render group) holds one value for
    // every draw; the program's SHARED refresh updates it once per pass.
    if (node.group?.shared === true || node.groupNode?.shared === true) continue;
    return `update ${describe(node)}`;
  }
  return null;
}

/** The properties of the draw's own material the program references beyond the observer's refreshUniforms, split
 *  into keys as r186's ReferenceNode reads them. */
export function referencePaths(state: State, refreshUniforms: readonly string[]): string[][] {
  const paths = new Map<string, string[]>();
  for (const node of state.updateNodes) {
    const property = node.property ?? '';
    if (node.updateType === OBJECT && node.isMaterialReferenceNode && node.material === null && !refreshUniforms.includes(property))
      paths.set(property, property.split('.'));
  }
  return [...paths.values()];
}

/** A referenced property's value on `material`: a primitive or a texture (by identity), else UNSEEN (an object whose
 *  contents may change in place, which only a full refresh reads). */
export function referenceValue(material: unknown, keys: readonly string[]): unknown {
  let value = material as Record<string, unknown> | undefined;
  for (const key of keys) { value = value?.[key] as Record<string, unknown> | undefined; if (value === undefined || value === null) return value; }
  return typeof value !== 'object' || (value as { isTexture?: boolean }).isTexture ? value : UNSEEN;
}
export const UNSEEN = Symbol('unseen');

/** The texture nodes the program's material references set per draw (the draw's own maps). */
export function materialTextures(state: State) {
  const nodes = new Set<unknown>();
  for (const node of state.updateNodes) if (node.isMaterialReferenceNode && node.node) nodes.add(node.node);
  return nodes;
}

/** The uniform nodes the program's covered update nodes write. */
export function ownedUniforms(state: State) {
  const owned = new Set<unknown>(MATRIX_UNIFORMS);
  // The IBL's PMREM size and mip count follow its texture, which a draw binds (texturesChanged sees another one).
  for (const node of state.updateBeforeNodes) if (node.constructor.type === 'PMREMNode')
    for (const key of ['_width', '_height', '_maxMip']) { const u = (node as unknown as Record<string, unknown>)[key]; if (u) owned.add(u); }
  for (const node of state.updateNodes) {
    if (node.uniformNode) owned.add(node.uniformNode);
    if (node.isMaterialReferenceNode && node.node) owned.add(node.node);
    if (node === velocity && node.previousModelWorldMatrix) owned.add(node.previousModelWorldMatrix);
    if (node.constructor.type === 'TextureNode') { if (node._matrixUniform) owned.add(node._matrixUniform); if (node._flipYUniform) owned.add(node._flipYUniform); }
  }
  return owned;
}

/** Whether every per-object buffer binding of the draw holds only uniforms the covered nodes own. Shared groups
 *  update in the program's SHARED refresh; storage buffers and plain uniform buffers are never covered. */
export function coveredBindings(draw: Draw, owned: Set<unknown>): boolean {
  return freeBinding(draw, owned) === null;
}

/** The first per-object binding value the covered nodes do not own, else null. */
export function freeBinding(draw: Draw, owned: Set<unknown>): string | null {
  for (const group of draw.getBindings()) for (const binding of group.bindings) {
    if (binding.isStorageBuffer) return `storage ${binding.name ?? ''}`;
    if (!binding.isUniformBuffer && !binding.isNodeUniformsGroup) continue;
    if (binding.groupNode?.shared === true) continue;
    if (!binding.isNodeUniformsGroup) return `buffer ${binding.name ?? ''}`;
    for (const uniform of binding.uniforms ?? []) {
      const node = uniform.nodeUniform?.node;
      if (!owned.has(node)) return `uniform ${node ? `${describe(node)}:${node.updateType ?? ''}` : '?'}`;
    }
  }
  return null;
}

/** Whether a bound texture changed since the draw's last full refresh: a new version of the texture it binds, or
 *  another texture behind a node with one value for every draw. A material's own maps (MaterialReferenceNode
 *  textures, `perDraw`) take each draw's value only while that draw updates, and `equals` compares them per material. */
export function texturesChanged(draw: Draw, perDraw: ReadonlySet<unknown> = EMPTY): boolean {
  let sampled = textureBindings.get(draw);
  if (!sampled) {
    sampled = [];
    for (const group of draw.getBindings()) for (const binding of group.bindings) if (binding.isSampledTexture) sampled.push(binding);
    textureBindings.set(draw, sampled);
  }
  for (const binding of sampled) {
    const texture = binding.texture;
    if (texture && binding.version !== texture.version) return true;
    if (binding.textureNode && !perDraw.has(binding.textureNode) && binding.textureNode.value !== texture) return true;
  }
  return false;
}
const EMPTY = new Set<unknown>();
/** Each draw's sampled-texture bindings: r186 creates a draw's bindings once (RenderObject.getBindings), so the
 *  per-frame check walks only these (1 % of the Windows drive profile walked every binding, win-after5). */
const textureBindings = new WeakMap<Draw, Binding[]>();

export type Audit = { frames: number; skipped: number; missed: number; rows: [string, number][] };

/** Install on an initialized r186 renderer (its node manager exists). `updateBinding` uploads a uniform group (the
 *  backend's), used only by `audit`. */
export function staticRefresh(renderer: object) {
  const nodes = (renderer as { _nodes: Nodes })._nodes;
  const backend = (renderer as { backend: { updateBinding(binding: Binding): void } }).backend;
  const info = (renderer as { info: { frame: number } }).info;
  const needsRefresh = nodes.needsRefresh;
  /** Per program: the uniforms its covered nodes own and its per-draw material textures, or null when the observer
   *  does not cover it. */
  const programs = new WeakMap<object, { owned: Set<unknown>; maps: Set<unknown>; paths: string[][] } | null>();
  /** Per draw: the values of its program's referencePaths at its last full refresh. */
  const references = new WeakMap<Draw, unknown[]>();
  /** Whether the draw's referenced material values are those of its last full refresh (and stores them when not). */
  const sameReferences = (draw: Draw, paths: string[][]) => {
    if (paths.length === 0) return true;
    let seen = references.get(draw), same = seen !== undefined;
    if (!seen) references.set(draw, seen = new Array(paths.length));
    for (let i = 0; i < paths.length; i++) {
      const value = referenceValue(draw.material, paths[i]);
      if (value === UNSEEN || value !== seen[i]) { same = false; seen[i] = value; }
    }
    return same;
  };
  /** Per draw: whether its bindings are covered (a draw's bindings are created with it and never replaced). */
  const draws = new WeakMap<Draw, boolean>();
  /** Draws that refresh in full once more after a change (motion vectors keep last frame's matrix for one frame). */
  const settling = new WeakSet<Draw>();
  const stats = { full: 0, shared: 0, none: 0 };
  let audit: { until: number; start: number; skipped: number; missed: number; rows: Map<string, number>; done: (a: Audit) => void } | null = null;

  /** Audit: the full node and uniform update behind a skipped draw; a uniform it changes is a miss (uploaded now). */
  const check = (self: Nodes, draw: Draw) => {
    self.updateForRender(draw);
    for (const group of draw.getBindings()) for (const binding of group.bindings) {
      if (!binding.isNodeUniformsGroup || binding.groupNode?.shared === true || !binding.update) continue;
      if (binding.update()) {
        audit!.missed++;
        const key = `${draw.object.name || '?'} | ${draw.material?.name || '?'} | ${binding.name ?? '?'}`;
        audit!.rows.set(key, (audit!.rows.get(key) ?? 0) + 1);
        backend.updateBinding(binding);
      }
      if (binding.updateRanges?.length) binding.clearUpdateRanges?.();
    }
  };

  nodes.needsRefresh = function (this: Nodes, draw: Draw) {
    if (audit && info.frame >= audit.until) {
      const { start, skipped, missed, rows, done } = audit;
      audit = null;
      done({ frames: info.frame - start, skipped, missed, rows: [...rows].sort((a, b) => b[1] - a[1]) });
    }
    const state = draw.getNodeBuilderState(), monitor = draw.getMonitor();
    let program = programs.get(state);
    if (program === undefined) {
      program = observable(state)
        ? { owned: ownedUniforms(state), maps: materialTextures(state), paths: referencePaths(state, monitor.refreshUniforms) } : null;
      programs.set(state, program);
    }
    const object = draw.object;
    if (program === null || monitor.hasAnimation || object.isSkinnedMesh || object.isBatchedMesh || object.morphTargetInfluences
      || monitor.hasDynamicInstancing(object)) { stats.full++; return needsRefresh.call(this, draw); }
    let covered = draws.get(draw);
    if (covered === undefined) { covered = coveredBindings(draw, program.owned); draws.set(draw, covered); }
    if (!covered) { stats.full++; return needsRefresh.call(this, draw); }
    if (monitor.firstInitialization(draw)) { sameReferences(draw, program.paths); settling.add(draw); stats.full++; return Refresh.FULL; }
    const { renderId } = this.getNodeFrameForRender(draw);
    let refresh: number = Refresh.NONE;
    if (monitor.renderId !== renderId) { monitor.renderId = renderId; refresh = Refresh.SHARED; }
    // equals() runs every time: it keeps the observer's snapshot of this draw current.
    const same = monitor.equals(draw, monitor.getLights(draw.lightsNode, renderId), renderId);
    // Both run every time: each keeps its snapshot of this draw current.
    const referenced = sameReferences(draw, program.paths);
    if (!same || !referenced || texturesChanged(draw, program.maps)) { settling.add(draw); stats.full++; return Refresh.FULL; }
    if (settling.has(draw)) { settling.delete(draw); stats.full++; return Refresh.FULL; }
    if (refresh === Refresh.SHARED) stats.shared++; else stats.none++;
    if (audit) { audit.skipped++; check(this, draw); }
    return refresh;
  };
  return {
    stats,
    /** Node ids of the matrix uniforms and velocity node it accepts, to compare with explain()'s '#id'. */
    known: () => ({ normalMatrix: (modelNormalMatrix as unknown as { id: number }).id,
      worldInverse: (modelWorldMatrixInverse as unknown as { id: number }).id, velocity: (velocity as unknown as { id: number }).id }),
    /** Why a draw keeps r186's full refresh ('covered' when it does not). Diagnostics. */
    explain(draw: Draw) {
      const state = draw.getNodeBuilderState(), monitor = draw.getMonitor(), object = draw.object;
      const program = uncovered(state);
      if (program) return `program ${program}`;
      if (monitor.hasAnimation || object.isSkinnedMesh || object.isBatchedMesh || object.morphTargetInfluences) return 'animated';
      if (monitor.hasDynamicInstancing(object)) return 'dynamic instancing';
      const unseen = referencePaths(state, monitor.refreshUniforms).find(keys => referenceValue(draw.material, keys) === UNSEEN);
      if (unseen) return `reference object ${unseen.join('.')}`;
      return freeBinding(draw, ownedUniforms(state)) ?? 'covered';
    },
    /** Run the full update behind every skipped draw for `frames` frames; resolves with what it would have missed. */
    audit(frames = 120) {
      return new Promise<Audit>(done => { audit = { until: info.frame + frames, start: info.frame, skipped: 0, missed: 0, rows: new Map(), done }; });
    },
    release() { nodes.needsRefresh = needsRefresh; },
  };
}
