import type * as THREE from 'three/webgpu';
import { streamTrace } from '../telemetry/streamTrace';

/** Diagnostics for perf work without a profiler UI: which node programs get built (and whether inside a live frame,
 * where they stall the frame, or during streaming preparation), and one frame's draws/triangles per pass and group.
 * Read with `__render.census()` in the console or from a capture; nothing here runs per draw unless a census frame
 * is requested. */
export interface BuildEntry { t: number; inFrame: boolean; pass: string; material: string; object: string }

const LIMIT = 400, FAMILIES = 600;
export interface BuildFamily { key: string; count: number; first: number; last: number }
export class BuildLog {
  readonly entries: BuildEntry[] = [];
  /** Every build since load by in-frame/prep, pass and material (bounded), so families survive the ring. */
  readonly families = new Map<string, BuildFamily>();
  inFrameTotal = 0;
  preparedTotal = 0;
  record(renderer: THREE.WebGPURenderer, draw: { object?: THREE.Object3D; material?: THREE.Material; camera?: THREE.Camera }, inFrame: boolean) {
    if (inFrame) this.inFrameTotal++; else this.preparedTotal++;
    const entry = { t: Math.round(performance.now()), inFrame, pass: passLabel(renderer, draw.camera),
      material: label(draw.material), object: objectLabel(draw.object) };
    this.entries.push(entry);
    if (this.entries.length > LIMIT) this.entries.shift();
    // Live captures (telemetry/capture.ts) get every in-frame build as a timed event: tools/perf/tile_timeline.py
    // names the shader-build hitches by these families.
    if (inFrame) streamTrace.event('gfx.build', undefined, { pass: entry.pass, material: entry.material, object: entry.object });
    const key = `${inFrame ? 'frame' : 'prep'}\t${entry.pass}\t${entry.material.replace(/\d+/g, '#')}`;
    const family = this.families.get(key);
    if (family) { family.count++; family.last = entry.t; }
    else if (this.families.size < FAMILIES) this.families.set(key, { key, count: 1, first: entry.t, last: entry.t });
  }
  top(n = 60) { return [...this.families.values()].sort((a, b) => b.count - a.count).slice(0, n); }
}

function label(value?: { name?: string; type?: string } | null) { return (value?.name || value?.type || '?').slice(0, 80); }
/** The nearest named ancestors, with tile ids and hashes folded so families aggregate. */
function objectLabel(object?: THREE.Object3D) {
  const names: string[] = [];
  for (let node = object ?? null; node && names.length < 3; node = node.parent) {
    if (node.name && !(node as THREE.Scene).isScene) names.push(node.name.replace(/[0-9a-f]{12,}/g, '#').replace(/t0_-?\d+_-?\d+/g, 'T').slice(0, 48));
  }
  return names.reverse().join(' > ') || object?.type || '?';
}
function passLabel(renderer: THREE.WebGPURenderer, camera?: THREE.Camera) {
  const target = renderer.getRenderTarget();
  const name = target ? (target.texture?.name || `rt${target.width}x${target.height}`) : 'screen';
  return `${name}|${(camera as THREE.OrthographicCamera | undefined)?.isOrthographicCamera ? 'ortho' : 'persp'}`;
}

type Direct = (object: THREE.Object3D, material: THREE.Material, scene: THREE.Scene, camera: THREE.Camera, ...rest: unknown[]) => void;
export interface FrameEnds { install(each: () => void): void; uninstall(): void }
/** One whole frame's draws and triangles by pass and object family, sorted by draws. */
export function drawCensus(renderer: THREE.WebGPURenderer, frameEnds: FrameEnds, builds?: BuildLog) {
  return new Promise<{ draws: number; rows: [string, number, number][]; builds?: { inFrame: number; prepared: number; families: BuildFamily[]; recent: BuildEntry[] } }>(resolve => {
    const host = renderer as unknown as { _renderObjectDirect: Direct; bundleDraw?: ((object: THREE.Object3D, camera: THREE.Camera) => void) | null };
    const original = host._renderObjectDirect;
    const rows = new Map<string, { draws: number; tris: number }>();
    const count = (object: THREE.Object3D, camera: THREE.Camera, suffix: string) => {
      const mesh = object as THREE.Mesh, geometry = mesh.geometry;
      let count = geometry?.index ? geometry.index.count : geometry?.attributes.position?.count ?? 0;
      if (geometry?.drawRange && geometry.drawRange.count !== Infinity) count = Math.min(count, geometry.drawRange.count);
      const instances = (mesh as THREE.InstancedMesh).count > 1 ? (mesh as THREE.InstancedMesh).count : 1;
      const key = `${passLabel(renderer, camera)}\t${objectLabel(object)}${suffix}`;
      const row = rows.get(key) ?? { draws: 0, tris: 0 }; row.draws++; row.tris += count / 3 * instances; rows.set(key, row);
    };
    host._renderObjectDirect = function (this: unknown, object, material, scene, camera, ...rest) {
      count(object, camera, '');
      return original.call(this, object, material, scene, camera, ...rest);
    };
    // Replayed static render bundles (render/staticBundles.ts) encode nothing per draw; they count as `[b]` rows.
    const bundled = 'bundleDraw' in host;
    if (bundled) host.bundleDraw = (object, camera) => count(object, camera, ' [b]');
    let ends = 0;
    frameEnds.install(() => {
      if (++ends === 1) { rows.clear(); return; } // the first end closes a partial frame
      frameEnds.uninstall();
      host._renderObjectDirect = original;
      if (bundled) host.bundleDraw = null;
      const sorted = [...rows].map(([key, row]) => [key, row.draws, Math.round(row.tris)] as [string, number, number]).sort((a, b) => b[1] - a[1]);
      resolve({ draws: sorted.reduce((sum, row) => sum + row[1], 0), rows: sorted,
        builds: builds && { inFrame: builds.inFrameTotal, prepared: builds.preparedTotal, families: builds.top(), recent: builds.entries.slice(-80) } });
    });
  });
}

interface UniformRef { nodeUniform?: { name?: string; node?: Labelled } }
interface Labelled { name?: string; updateType?: string; isUniformNode?: boolean; constructor: { name: string }; [key: string]: unknown }
interface CensusObject { object: THREE.Object3D; camera: THREE.Camera; material: THREE.Material; getNodeBuilderState?: () => { updateNodes?: Labelled[] } }
type Group = { name?: string; isUniformBuffer?: boolean; uniforms?: UniformRef[] };
type UpdateByType = (this: Group, uniform: UniformRef) => boolean;

/** Readable names of a draw's uniform nodes: the update node owning each (ModelNode.worldMatrix,
 *  VelocityNode.previousModelWorldMatrix), else the uniform's own name and update type. */
function uniformLabels(renderObject: CensusObject, labels: WeakMap<object, string>) {
  for (const node of renderObject.getNodeBuilderState?.().updateNodes ?? []) {
    const owner = `${node.constructor.name}${typeof node.scope === 'string' ? `.${node.scope}` : ''}`;
    if (node.isUniformNode && !labels.has(node)) labels.set(node, `${node.name || owner}(${node.updateType})`);
    for (const [key, value] of Object.entries(node)) {
      const child = value as Labelled | null;
      if (child && typeof child === 'object' && child.isUniformNode && !labels.has(child)) labels.set(child, `${owner}.${key}(${child.updateType})`);
    }
  }
}

/** Put back a method replaced on an instance: delete the own property when the original came from the prototype. */
function restore(target: object, key: string, method: unknown) {
  if (Object.getPrototypeOf(target)[key] === method) delete (target as Record<string, unknown>)[key];
  else (target as Record<string, unknown>)[key] = method;
}

/** One frame's uniform-buffer writes by pass, object family, uniform group and uniform: which uniforms change every
 *  frame, and on which draws (static draws should change none). Rows `[key, count]`; writes = buffers written. */
export function uniformCensus(renderer: THREE.WebGPURenderer, frameEnds: FrameEnds) {
  return new Promise<{ writes: number; changes: number; rows: [string, number][] }>(resolve => {
    const host = renderer as unknown as { _bindings: { updateForRender(ro: CensusObject): void; updateSharedForRender(ro: CensusObject): void;
      _update(group: { bindings: Group[] }, bindings: unknown): void }; backend: { updateBinding(binding: unknown): void } };
    const bindings = host._bindings, labels = new WeakMap<object, string>(), rows = new Map<string, number>();
    let current: CensusObject | null = null, writes = 0, changes = 0, changedGroup: unknown = null;
    const own = { full: bindings.updateForRender, shared: bindings.updateSharedForRender, update: bindings._update, write: host.backend.updateBinding };
    const patched = new Map<object, UpdateByType>();
    const track = (method: 'updateForRender' | 'updateSharedForRender') => function (this: unknown, renderObject: CensusObject) {
      current = renderObject; uniformLabels(renderObject, labels);
      try { return own[method === 'updateForRender' ? 'full' : 'shared'].call(this, renderObject); } finally { current = null; }
    };
    bindings.updateForRender = track('updateForRender');
    bindings.updateSharedForRender = track('updateSharedForRender');
    // The bundled build's uniform-group class is reached through a live binding, then patched for this census only.
    bindings._update = function (this: unknown, bindGroup, list) {
      for (const binding of bindGroup.bindings) {
        if (!binding.isUniformBuffer) continue;
        let proto: object | null = Object.getPrototypeOf(binding);
        while (proto && !Object.prototype.hasOwnProperty.call(proto, 'updateByType')) proto = Object.getPrototypeOf(proto);
        if (proto && !patched.has(proto)) {
          const original = (proto as { updateByType: UpdateByType }).updateByType;
          patched.set(proto, original);
          (proto as { updateByType: UpdateByType }).updateByType = function (this: Group, uniform: UniformRef) {
            const changed = original.call(this, uniform);
            if (changed) changedGroup = this;
            if (changed && current) {
              changes++;
              const node = uniform.nodeUniform?.node;
              const label = (node && labels.get(node)) || node?.name || `${uniform.nodeUniform?.name ?? '?'}`;
              const key = `${passLabel(renderer, current.camera)}\t${objectLabel(current.object)}\t${this.name ?? '?'}\t${label}`;
              rows.set(key, (rows.get(key) ?? 0) + 1);
            }
            return changed;
          };
        }
      }
      return own.update.call(this, bindGroup, list);
    };
    // Buffers written with no per-uniform change (uniform arrays, buffer nodes) count as their own rows.
    host.backend.updateBinding = function (this: unknown, binding: unknown) {
      writes++;
      if (binding !== changedGroup) {
        const key = `${passLabel(renderer, current?.camera)}\t${current ? objectLabel(current.object) : '(no draw)'}\t${(binding as Group | undefined)?.name ?? '?'}\t(buffer written whole)`;
        rows.set(key, (rows.get(key) ?? 0) + 1);
      }
      changedGroup = null;
      return own.write.call(this, binding);
    };
    let ends = 0;
    frameEnds.install(() => {
      if (++ends === 1) { rows.clear(); writes = 0; changes = 0; return; } // the first end closes a partial frame
      frameEnds.uninstall();
      for (const [key, method] of [['updateForRender', own.full], ['updateSharedForRender', own.shared], ['_update', own.update]] as const)
        restore(bindings, key, method);
      restore(host.backend, 'updateBinding', own.write);
      for (const [proto, original] of patched) (proto as { updateByType: UpdateByType }).updateByType = original;
      resolve({ writes, changes, rows: [...rows].sort((a, b) => b[1] - a[1]) });
    });
  });
}
