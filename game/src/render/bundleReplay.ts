// r186 render-bundle record/replay (Renderer._renderBundle), made safe for the game's passes. Differences from r186:
// - A replay that finds a draw's GPU bind group replaced (a texture swapped, e.g. surface-array growth disposing the
//   old array) or a pipeline that was still compiling when it recorded records again before it executes: a bundle
//   holding a destroyed texture would fail the whole submit, and a skipped draw would stay missing.
// - Recording sorts the bundle's list like any pass and saves/restores the current bundle, so nested renders (shadow
//   maps drawn from a receiver's updateBefore) neither join it nor end it.
// - Bundles execute where three renders them, before the pass's other opaque and transparent draws (three defers
//   executeBundles to the end of the pass, so bundled ground would overdraw blended surfaces in front of it).
// - Replayed draws are added to renderer.info, so draw and triangle statistics stay comparable.
import type * as THREE from 'three/webgpu';

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;
interface BundleData {
  renderObjects?: Any[]; version?: number; bundleGPU?: unknown;
  /** Every distinct bind group the recorded draws bind, with the GPU group recorded. */
  groups?: Any[]; gpuGroups?: unknown[];
  /** Recorded draws whose pipeline was not ready (skipped by r186), so the bundle records again once they are. */
  waiting?: Any[];
  calls?: number; triangles?: number;
}

/** Census hook: called with each replayed draw (render/census.ts). */
export type BundleDrawHook = (object: THREE.Object3D, camera: THREE.Camera) => void;

const FULL = 2, SHARED = 1;

function refresh(r: Any, renderObject: Any) {
  const type = r._nodes.needsRefresh(renderObject);
  if (type === FULL) {
    r._nodes.updateBefore(renderObject);
    r._geometries.updateForRender(renderObject);
    r._nodes.updateForRender(renderObject);
    r._bindings.updateForRender(renderObject);
    r._nodes.updateAfter(renderObject);
  } else if (type === SHARED) {
    r._nodes.updateBefore(renderObject);
    r._nodes.updateForRender(renderObject);
    r._bindings.updateSharedForRender(renderObject);
    r._nodes.updateAfter(renderObject);
  }
}

/** Whether a recorded bundle no longer matches the GPU state its draws need. */
function stale(r: Any, data: BundleData) {
  const backend = r.backend, groups = data.groups!, gpu = data.gpuGroups!;
  for (let i = 0; i < groups.length; i++) if (backend.get(groups[i]).group !== gpu[i]) return true;
  if (data.waiting?.length) {
    for (const renderObject of data.waiting) if (!r._pipelines.isReady(renderObject)) return false;
    return true;
  }
  return false;
}

/** Triangles of one recorded mesh draw, as r186's Info counts them. */
function triangles(renderObject: Any) {
  const geometry = renderObject.geometry, index = geometry.index, range = renderObject.drawRange, group = renderObject.group;
  let first = range.start, last = Math.min(index ? index.count : geometry.attributes.position?.count ?? 0, range.start + range.count);
  if (group) { first = Math.max(first, group.start); last = Math.min(last, group.start + group.count); }
  const instances = renderObject.object.isInstancedMesh ? renderObject.object.count : 1;
  return Math.max(0, last - first) / 3 * instances;
}

function remember(r: Any, data: BundleData) {
  const seen = new Set<Any>(), groups: Any[] = [], waiting: Any[] = [];
  let calls = 0, count = 0;
  for (const renderObject of data.renderObjects!) {
    for (const group of renderObject.getBindings()) if (!seen.has(group)) { seen.add(group); groups.push(group); }
    if (!r._pipelines.isReady(renderObject)) { waiting.push(renderObject); continue; }
    calls++; count += triangles(renderObject);
  }
  data.groups = groups;
  data.gpuGroups = groups.map(group => r.backend.get(group).group);
  data.waiting = waiting;
  // Counted from the draws themselves: a nested render while recording (shadow maps) must not count on every replay.
  data.calls = calls; data.triangles = count;
}

export function renderBundle(r: Any, bundle: { bundleGroup: THREE.Object3D & { version: number }; camera: THREE.Camera; renderList: Any },
  sceneRef: THREE.Scene, lightsNode: unknown, hook: BundleDrawHook | null) {
  const { bundleGroup, camera, renderList } = bundle;
  const renderContext = r._currentRenderContext;
  const renderBundle = r._bundles.get(bundleGroup, camera, renderContext);
  const data: BundleData = r.backend.get(renderBundle);
  let record = r._bundleNeedsUpdate(bundleGroup, data);
  if (!record) {
    const objects = data.renderObjects!;
    for (let i = 0, l = objects.length; i < l; i++) refresh(r, objects[i]);
    if (stale(r, data)) {
      record = true;
      // r186 empties render lists unused for ten frames; project the members again rather than trust the list.
      renderList.begin();
      const clipping = renderContext.clippingContext;
      for (const child of bundleGroup.children) r._projectObject(child, camera, bundleGroup.renderOrder, renderList, clipping);
      renderList.finish();
    }
  }
  const info = r.info.render;
  if (record) {
    if (data.renderObjects) data.renderObjects.length = 0; else data.renderObjects = [];
    if (r.sortObjects === true) renderList.sort(r._opaqueSort, r._transparentSort);
    r.backend.beginBundle(renderContext);
    const outer = r._currentRenderBundle;
    r._currentRenderBundle = renderBundle;
    try {
      const { transparentDoublePass, transparent, opaque } = renderList;
      if (r.opaque === true && opaque.length > 0) r._renderObjects(opaque, camera, sceneRef, lightsNode);
      if (r.transparent === true && transparent.length > 0) r._renderTransparents(transparent, transparentDoublePass, camera, sceneRef, lightsNode);
    } finally { r._currentRenderBundle = outer; }
    r.backend.finishBundle(renderContext, renderBundle);
    data.version = bundleGroup.version;
    remember(r, data);
  } else {
    info.drawCalls += data.calls!; info.triangles += data.triangles!;
    if (hook) for (const renderObject of data.renderObjects!) hook(renderObject.object, camera);
  }
  r.backend.addBundle(renderContext, renderBundle);
}

/** After three rendered a pass's bundles (before its opaque list): execute them now, in place. */
export function executeBundles(r: Any) {
  const data = r.backend.get(r._currentRenderContext);
  const pass = data.currentPass as GPURenderPassEncoder | undefined;
  if (!data.renderBundles?.length || !pass || typeof pass.executeBundles !== 'function' || data.bundleEncoders) return;
  pass.executeBundles(data.renderBundles);
  data.renderBundles = [];
  // executeBundles clears the pass's pipeline, bind groups and buffers: the encoder's state cache must follow.
  data.currentSets = { attributes: {}, bindingGroups: [], pipeline: null, index: null };
}
