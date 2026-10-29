import { describe, test, expect, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { percentiles } from '../../src/render/performance';
import { renderPixelRatio } from '../../src/render/quality';
import { materialKey, surfaceStyle, MaterialStyler } from '../../src/render/materials';
import { Environment } from '../../src/render/environment';
import { parseParams } from '../../src/core/params';
import { GpuFrameTimer } from '../../src/render/gpuTimer';

test('nearest-rank percentiles handle small and empty windows without invented zero timings', () => {
  expect(percentiles([5, 1, 3, 4, 2])).toEqual({ samples: 5, p50: 3, p95: 5 });
  expect(percentiles([])).toEqual({ samples: 0, p50: null, p95: null });
});

test('whole-frame timer keeps in-flight WebGL queries separate and rejects disjoint results', async () => {
  vi.useFakeTimers();
  let count = 0, disjoint = false;
  const begin = vi.fn(), end = vi.fn();
  const gl = {
    QUERY_RESULT_AVAILABLE: 1, QUERY_RESULT: 2,
    getExtension: () => ({ TIME_ELAPSED_EXT: 3, GPU_DISJOINT_EXT: 4 }),
    createQuery: () => ({ id: ++count }), deleteQuery: vi.fn(),
    beginQuery: begin, endQuery: end, isContextLost: () => false,
    getParameter: () => { const value = disjoint; disjoint = false; return value; },
    getQueryParameter: (query: { id: number }, parameter: number) => parameter === 1 ? true : query.id * 1e6,
  };
  const backend = { gl, trackTimestamp: true };
  const received: number[][] = [];
  const timer = new GpuFrameTimer({ backend } as unknown as THREE.WebGPURenderer,
    (frame, ms) => received.push([frame, ms]));
  try {
    timer.begin(42); timer.end(); timer.begin(43); timer.end();
    expect(count).toBe(2); // no reuse until the readbacks complete
    expect(begin).toHaveBeenCalledTimes(2); expect(end).toHaveBeenCalledTimes(2);
    expect(backend.trackTimestamp).toBe(false); // no nested engine queries
    await vi.runAllTimersAsync(); await timer.flush();
    expect(received).toEqual([[42, 1], [43, 2]]);
    disjoint = true; timer.begin(44); timer.end(); timer.begin(45); timer.end();
    await vi.runAllTimersAsync(); await timer.flush();
    expect(received).toHaveLength(2); expect(timer.skipped).toBe(2);
    await timer.dispose(); expect(gl.deleteQuery).toHaveBeenCalledTimes(2);
  } finally { vi.useRealTimers(); }
});

test('HiDPI render budget and QA output sizes are explicit', () => {
  expect(renderPixelRatio(1280, 720, 2, false)).toBe(2);
  expect(renderPixelRatio(2560, 1440, 2, false)).toBe(1);
  expect(renderPixelRatio(3840, 2160, 2, false)).toBeCloseTo(2 / 3);
  expect(renderPixelRatio(1280, 720, 2, true)).toBe(1);
  expect(parseParams('?quality=low').quality).toBe('low');
  expect(parseParams('').quality).toBe('medium');
  expect(() => parseParams('?quality=ultra')).toThrow(/quality/);
});

test('material keys inherit glTF node extras; per-primitive material key takes precedence', () => {
  const parent = new THREE.Group(), mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  parent.userData.material_key = 'granite_curb'; parent.add(mesh);
  expect(materialKey(mesh, mesh.material)).toBe('granite_curb');
  mesh.material.userData.material_key = 'grass_park';
  expect(materialKey(mesh, mesh.material)).toBe('grass_park');
  expect(surfaceStyle('library/asphalt_fine/v1')).toBe('asphalt');
  expect(surfaceStyle('water_river')).toBe('water');
  expect(surfaceStyle('plaster_facade')).toBe('standard');
});

describe('glTF material preservation and streamed ownership', () => {
  test('styling keeps albedo, normal, ORM and UV transforms, releases only its own material', () => {
    const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
    const env = new Environment(new THREE.Scene(), renderer, 'medium');
    const styler = new MaterialStyler(renderer, env, 'medium', true);
    const source = new THREE.MeshStandardMaterial({ color: 0x807060, roughness: 0.85 });
    const albedo = new THREE.Texture(), normal = new THREE.Texture(), orm = new THREE.Texture();
    albedo.repeat.set(0.5, 0.25); albedo.colorSpace = THREE.SRGBColorSpace;
    source.map = albedo; source.normalMap = normal;
    source.aoMap = source.roughnessMap = source.metalnessMap = orm;
    const model = new THREE.Group(), mesh: THREE.Mesh = new THREE.Mesh(new THREE.BoxGeometry(), source);
    mesh.userData.material_key = 'granite_curb'; model.add(mesh);
    styler.apply(model);
    const result = mesh.material as THREE.MeshStandardNodeMaterial;
    expect(result.isNodeMaterial).toBe(true);
    expect(result.map).toBe(albedo); expect(result.normalMap).toBe(normal);
    expect(result.aoMap).toBe(orm); expect(result.roughnessMap).toBe(orm); expect(result.metalnessMap).toBe(orm);
    expect(result.color.equals(source.color)).toBe(true);
    expect(result.map!.repeat.toArray()).toEqual([0.5, 0.25]);
    let ownedDisposed = 0, originalDisposed = 0, mapDisposed = 0;
    result.addEventListener('dispose', () => ownedDisposed++);
    source.addEventListener('dispose', () => originalDisposed++);
    albedo.addEventListener('dispose', () => mapDisposed++);
    styler.release(model); styler.release(model);
    expect(ownedDisposed).toBe(1); expect(originalDisposed).toBe(0); expect(mapDisposed).toBe(0);
    styler.dispose(); env.dispose(); mesh.geometry.dispose(); source.dispose();
  });
});

test('coplanar river tiles share a reflection and transfer ownership on unload', () => {
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const env = new Environment(new THREE.Scene(), renderer, 'medium');
  const styler = new MaterialStyler(renderer, env, 'medium', true);
  const models = [0, 250].map(x => {
    const model = new THREE.Group();
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(250, 250).rotateX(-Math.PI / 2), new THREE.MeshStandardMaterial());
    mesh.position.x = x; mesh.userData.material_key = 'water'; model.add(mesh); styler.apply(model);
    return { model, mesh };
  });
  expect(models[0].mesh.material).toBe(models[1].mesh.material);
  let disposed = 0;
  models[0].mesh.material.addEventListener('dispose', () => disposed++);
  styler.release(models[0].model);
  expect(disposed).toBe(0);
  expect(models[1].mesh.children).toHaveLength(1);
  styler.release(models[1].model);
  expect(disposed).toBe(1);
  styler.dispose(); env.dispose();
});

test('streamed styled graphs share exact maps and survive either tile being released first', () => {
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const env = new Environment(new THREE.Scene(), renderer, 'low'), styler = new MaterialStyler(renderer, env, 'low', true);
  const map = new THREE.Texture();
  const models = Array.from({ length: 3 }, (_, i) => {
    const source = new THREE.MeshStandardMaterial({ map: i === 2 ? new THREE.Texture() : map });
    const model = new THREE.Group(), mesh = new THREE.Mesh(new THREE.BoxGeometry(), source);
    mesh.userData.material_key = 'granite_curb'; model.add(mesh); styler.apply(model);
    return { model, mesh, source };
  });
  const material = models[0].mesh.material;
  expect(models[1].mesh.material).toBe(material); expect(models[2].mesh.material).not.toBe(material);
  const disposed = vi.fn(); material.addEventListener('dispose', disposed);
  styler.release(models[0].model); expect(disposed).not.toHaveBeenCalled();
  expect(models[0].mesh.material).toBe(models[0].source);
  styler.release(models[1].model); expect(disposed).toHaveBeenCalledOnce();
  styler.dispose(); env.dispose();
});
