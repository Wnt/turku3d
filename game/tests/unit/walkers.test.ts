import { describe, expect, test } from 'vitest';
import fs from 'node:fs';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { mrt, normalView, velocity } from 'three/tsl';
import { CAR, FOOT, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { CYCLE_EPOCH, dayCycle } from '../../src/core/dayCycle';
import { parseParams } from '../../src/core/params';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { NormalPrepass } from '../../src/render/normalPrepass';
import { REGIONS, parseWalkerAsset, type WalkerAsset } from '../../src/world/walkers/asset';
import { buildWalkNetwork, hash32 } from '../../src/world/walkers/network';
import { MAX_TRAVEL, Population, WALKER, type Walker } from '../../src/world/walkers/population';
import { RING, Ring, type RingView } from '../../src/world/walkers/ring';
import { WALKER_CAPS, liveWalkerCount, walkerCap, walkersNear } from '../../src/world/walkers';
import { WalkerMeshes } from '../../src/world/walkers/visuals';

const TILE = 250;
type Line = { pts: [number, number][]; flags: number };
/** A 500 x 500 m footway grid (two tiles each way) with a major street, clipped per tile like turku3d.roads:
 *  every crossing is a node, every polyline is cut at the 250 m seams and quantised to 0.1 m. */
function fixture(): Map<string, RoadGraphJson> {
  const lines: Line[] = [];
  for (const z of [20, 95, 170, 245.5, 330, 410, 480]) lines.push({ pts: [[5, z], [495, z + 3]], flags: FOOT });
  for (const x of [15, 120, 205, 300, 365, 470]) lines.push({ pts: [[x, 5], [x + 6, 495]], flags: FOOT });
  lines.push({ pts: [[60, 60], [190, 200], [320, 260], [440, 440]], flags: FOOT }); // a diagonal park path
  lines.push({ pts: [[0, 140], [500, 150]], flags: CAR | FOOT | (3 << 3) }); // a major street (lends its class)
  // Split every line at its crossings with other lines and at the seams.
  const cut = (a: [number, number], b: [number, number]) => {
    const ts = new Set([0, 1]);
    for (const other of lines) for (let i = 0; i + 1 < other.pts.length; i++) {
      const [c, d] = [other.pts[i], other.pts[i + 1]];
      const den = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0]);
      if (Math.abs(den) < 1e-9) continue;
      const t = ((c[0] - a[0]) * (d[1] - c[1]) - (c[1] - a[1]) * (d[0] - c[0])) / den;
      const u = ((c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])) / den;
      if (t > 1e-6 && t < 1 - 1e-6 && u >= -1e-9 && u <= 1 + 1e-9) ts.add(t);
    }
    for (const seam of [TILE]) for (const k of [0, 1]) {
      const t = (seam - a[k]) / (b[k] - a[k]);
      if (t > 1e-6 && t < 1 - 1e-6) ts.add(t);
    }
    return [...ts].sort((p, q) => p - q).map(t => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t] as [number, number]);
  };
  const q = (v: number) => Math.round(v * 10) / 10;
  const tiles = new Map<string, { nodes: number[]; index: Map<string, number>; edges: number[][] }>();
  for (const line of lines) {
    for (let i = 0; i + 1 < line.pts.length; i++) {
      const pts = cut(line.pts[i], line.pts[i + 1]);
      for (let k = 0; k + 1 < pts.length; k++) {
        const a = pts[k].map(q) as [number, number], b = pts[k + 1].map(q) as [number, number];
        const tx = Math.floor((a[0] + b[0]) / 2 / TILE), tz = Math.floor((a[1] + b[1]) / 2 / TILE), id = `${tx},${tz}`;
        let tile = tiles.get(id);
        if (!tile) tiles.set(id, tile = { nodes: [], index: new Map(), edges: [] });
        const node = (p: [number, number]) => {
          const key = p.join(',');
          let n = tile!.index.get(key);
          if (n === undefined) { n = tile!.nodes.length / 2; tile!.index.set(key, n); tile!.nodes.push(...p); }
          return n;
        };
        tile.edges.push([node(a), node(b), line.flags, q(Math.hypot(b[0] - a[0], b[1] - a[1]))]);
      }
    }
  }
  return new Map([...tiles].map(([id, t]) => {
    const [tx, tz] = id.split(',').map(Number);
    return [id, { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [tx * TILE, tz * TILE, (tx + 1) * TILE, (tz + 1) * TILE],
      nodes: t.nodes, edges: t.edges }];
  }));
}
const tiles = fixture();
const parts = (...ids: string[]) => ids.map(id => tiles.get(id)!);
const ALL = ['0,0', '1,0', '0,1', '1,1'];
// A clock-hour 12:00-13:00 sample of the day cycle, well before the epoch (the cycle is shorter than a real day).
const T0 = (() => {
  const base = CYCLE_EPOCH - 120 * 86_400_000;
  for (let k = 0; k < 4000; k++) if (dayCycle(base + k * 15_000).hour >= 12 && dayCycle(base + k * 15_000).hour < 12.5) return base + k * 15_000;
  throw new Error('no midday in the day cycle');
})();
const key = (w: Walker) => [w.id, w.x, w.z, w.yaw, w.distance, w.idleTime, w.moving, w.fade, w.variant, w.scale].join('|');
const sorted = (list: Walker[]) => list.map(key).sort();

describe('deterministic population', () => {
  test('the scorecard reads the same walkers from tile data alone, and the live count starts at zero', () => {
    const network = buildWalkNetwork(parts(...ALL));
    expect(sorted(walkersNear(network, 250, 250, 50, T0))).toEqual(sorted(new Population(network).near(250, 250, 50, T0)));
    expect(liveWalkerCount()).toBe(0);
  });

  test('two clients with the same clock get identical walkers', () => {
    const a = new Population(buildWalkNetwork(parts(...ALL))), b = new Population(buildWalkNetwork(parts(...ALL)));
    for (let k = 0; k < 6; k++) {
      const t = T0 + k * 7_919;
      const left = a.near(250, 250, 200, t), right = b.near(250, 250, 200, t);
      expect(left.length).toBeGreaterThan(20);
      expect(sorted(left)).toEqual(sorted(right));
    }
  });

  test('tile load order does not change the network or the walkers', () => {
    const orders = [ALL, ['1,1', '0,1', '1,0', '0,0'], ['0,1', '1,0', '1,1', '0,0']];
    const networks = orders.map(order => buildWalkNetwork(parts(...order)));
    const ids = networks.map(n => n.chains.map(c => c.id).sort());
    expect(ids[1]).toEqual(ids[0]); expect(ids[2]).toEqual(ids[0]);
    const walkers = networks.map(n => sorted(new Population(n).near(250, 250, 250, T0)));
    expect(walkers[1]).toEqual(walkers[0]); expect(walkers[2]).toEqual(walkers[0]);
  });

  test('chains join across complete seams; a missing neighbour leaves an incomplete seam node', () => {
    const full = buildWalkNetwork(parts(...ALL)), half = buildWalkNetwork(parts('0,0', '0,1'));
    const seamNodes = (n: typeof full) => n.nodes.filter(node => Math.abs(node.x - TILE) < 0.05 || Math.abs(node.z - TILE) < 0.05);
    expect(seamNodes(full).filter(node => node.exits.length === 2)).toHaveLength(0); // pass-through seams are joined away
    const open = seamNodes(half).filter(node => Math.abs(node.x - TILE) < 0.05);
    expect(open.length).toBeGreaterThan(0);
    expect(open.every(node => !node.complete)).toBe(true);
  });

  test('a client missing a neighbour tile agrees on every walker it knows in its own tiles', () => {
    const near = new Population(buildWalkNetwork(parts('0,0', '0,1'))), far = new Population(buildWalkNetwork(parts(...ALL)));
    for (let k = 0; k < 4; k++) {
      const t = T0 + k * 13_007;
      const known = near.near(120, 250, 130, t).filter(w => w.x < TILE - 1);
      const truth = new Map(far.near(120, 250, 130 + 5, t).map(w => [w.id, w]));
      expect(known.length).toBeGreaterThan(10);
      for (const w of known) {
        const other = truth.get(w.id)!;
        expect(other, `walker ${w.id}`).toBeDefined();
        expect(Math.hypot(other.x - w.x, other.z - w.z)).toBeLessThan(1e-6);
        expect(other.distance).toBeCloseTo(w.distance, 6);
      }
    }
  });

  test('walkers move continuously along FOOT ways at walking speed and live out their slot', () => {
    const population = new Population(buildWalkNetwork(parts(...ALL)));
    const before = new Map(population.near(250, 250, 250, T0).map(w => [w.id, w]));
    const after = population.near(250, 250, 250, T0 + 200);
    let moving = 0;
    for (const w of after) {
      const old = before.get(w.id);
      if (!old) continue;
      const step = Math.hypot(w.x - old.x, w.z - old.z);
      expect(step).toBeLessThan(WALKER.speed[1] * 0.2 + 0.05); // keep-right taper moves a little sideways too
      const walked = w.distance - old.distance;
      expect(walked).toBeGreaterThanOrEqual(0); expect(walked).toBeLessThanOrEqual(WALKER.speed[1] * 0.2 + 1e-9);
      if (w.moving && old.moving && walked >= WALKER.speed[0] * 0.2 - 1e-9) moving++;
    }
    expect(moving).toBeGreaterThan(10);
    for (const w of after) {
      expect(w.age).toBeGreaterThanOrEqual(0); expect(w.age).toBeLessThan(WALKER.life[1]);
      expect(Math.hypot(w.x - (w.cell.cx + 0.5) * 25, w.z - (w.cell.cz + 0.5) * 25)).toBeLessThan(MAX_TRAVEL + 40);
    }
  });

  test('density follows the busiest street: cells beside the major street hold more walkers per metre', () => {
    const network = buildWalkNetwork(parts(...ALL));
    const perMetre = (cz: number) => {
      let w = 0, m = 0;
      for (const cell of network.cells.values()) if (cell.cz === cz) for (const p of cell.pieces) { w += p.length * p.weight; m += p.length; }
      return w / m;
    };
    expect(perMetre(5)).toBeGreaterThan(perMetre(16)); // z 125..150 holds the major street; z 400..425 does not
  });
});

describe('ring', () => {
  const view: RingView = { x: 0, z: 0, fx: 0, fz: -1, cosHalf: Math.cos(Math.PI / 4) };
  const w = (id: number, x: number, z: number) => ({ id, x, z });

  test('draws in view to 100 m, behind only to 40 m, and keeps drawn walkers a little longer', () => {
    const ring = new Ring<ReturnType<typeof w>>();
    const walkers = [w(1, 0, -95), w(2, 0, -105), w(3, 0, 35), w(4, 0, 45), w(5, 70, 0)];
    const drawn = () => ring.select(walkers, view, 30, 1).map(d => d.walker.id).sort();
    expect(drawn()).toEqual([1, 3]);
    // Hysteresis is RING.keep in ring distance: 10 m in view, 4 m behind.
    walkers[0].z = -108; walkers[2].z = 43;
    expect(drawn()).toEqual([1, 3]);
    walkers[0].z = -112; walkers[2].z = 45;
    expect(drawn()).toEqual([]);
  });

  test('nearby walkers entering or leaving in view fade; far or unseen ones switch instantly', () => {
    const ring = new Ring<ReturnType<typeof w>>();
    const first = ring.select([w(1, 0, -20), w(2, 0, -80), w(3, 0, 20)], view, 30, 0);
    const fade = new Map(first.map(d => [d.walker.id, d.fade]));
    expect(fade.get(1)).toBe(0); expect(fade.get(2)).toBe(1); expect(fade.get(3)).toBe(1);
    const later = ring.select([w(1, 0, -20), w(2, 0, -80), w(3, 0, 20)], view, 30, RING.fadeSeconds / 2);
    expect(later.find(d => d.walker.id === 1)!.fade).toBeCloseTo(0.5, 5);
    // The cap drops walker 2 (far): it disappears at once; walker 1 dropped in view fades out.
    const capped = ring.select([w(4, 0, -5), w(1, 0, -20), w(2, 0, -80)], view, 1, 0.1).map(d => d.walker.id);
    expect(capped).toContain(1); expect(capped).not.toContain(2);
    let leaving = ring.select([w(4, 0, -5), w(1, 0, -20)], view, 1, RING.fadeSeconds);
    leaving = ring.select([w(4, 0, -5), w(1, 0, -20)], view, 1, RING.fadeSeconds);
    expect(leaving.map(d => d.walker.id)).toEqual([4]);
  });

  test('never draws more than the cap plus the fade margin', () => {
    const ring = new Ring<ReturnType<typeof w>>();
    const crowd = Array.from({ length: 200 }, (_, i) => w(i, Math.sin(i) * 60, -10 - (i % 80)));
    for (let k = 0; k < 5; k++) expect(ring.select(crowd, view, 30, 1).length).toBeLessThanOrEqual(30 + RING.margin);
    expect(ring.select(crowd, view, 30, 1).length).toBe(30);
  });
});

test('caps per quality tier and the ?walkers= override', () => {
  expect(WALKER_CAPS).toEqual({ mobile: 0, low: 10, medium: 30, high: 50 });
  expect(walkerCap('low', null)).toBe(10); expect(walkerCap('medium', null)).toBe(30); expect(walkerCap('high', null)).toBe(50);
  expect(walkerCap('medium', 0)).toBe(0); expect(walkerCap('low', 80)).toBe(80);
  expect(parseParams('?walkers=12').walkers).toBe(12); expect(parseParams('').walkers).toBeNull();
  expect(() => parseParams('?walkers=-1')).toThrow(); expect(() => parseParams('?walkers=2.5')).toThrow(); expect(() => parseParams('?walkers=201')).toThrow();
});

const file = fs.readFileSync('public/assets/walkers/walkers.bin');
const asset: WalkerAsset = parseWalkerAsset(file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength));

test('the committed VAT asset fits the budget', () => {
  expect(asset.variants.map(v => v.id)).toEqual(['hoodie', 'jacket', 'skirt']);
  expect(asset.vat.data.byteLength).toBeLessThanOrEqual(2_000_000);
  expect(asset.fps).toBe(30);
  for (const v of asset.variants) {
    expect(v.walk.frames).toBe(40); expect(v.idle.frames).toBe(75); // the whole UAL loops at 30 fps
    expect(v.vertices).toBeLessThanOrEqual(asset.vat.width);
    expect(Math.max(...v.indices)).toBeLessThan(v.vertices);
    expect(Math.max(...v.regions)).toBeLessThan(REGIONS.length);
    expect(v.height).toBeGreaterThan(1.5); expect(v.height).toBeLessThan(1.9);
  }
});

test('walk phase from walked distance keeps planted soles still (no foot sliding)', () => {
  const width = asset.vat.width, data = asset.vat.data, half = (i: number) => THREE.DataUtils.fromHalfFloat(data[i]);
  /** Median world-space slip of sole vertices touching the ground, relative to the distance walked (0: planted). */
  const slip = (v: WalkerAsset['variants'][number], metresPerLoop: number) => {
    const shoes = [...v.regions.keys()].filter(i => REGIONS[v.regions[i]] === 'shoes');
    const at = (i: number, phase: number) => {
      const f = phase * v.walk.frames, f0 = Math.floor(f) % v.walk.frames, f1 = (f0 + 1) % v.walk.frames, t = f - Math.floor(f);
      const a = 4 * ((v.rowBase + v.walk.row + f0) * width + i), b = 4 * ((v.rowBase + v.walk.row + f1) * width + i);
      return { y: half(a + 1) + (half(b + 1) - half(a + 1)) * t, z: half(a + 2) + (half(b + 2) - half(a + 2)) * t };
    };
    const step = metresPerLoop / 240, ratios: number[] = [];
    for (const i of shoes) {
      for (let k = 0; k < 240; k++) {
        const d = k * step, p = at(i, (d / metresPerLoop) % 1), q = at(i, ((d + step) / metresPerLoop) % 1);
        if (p.y < 0.02 && q.y < 0.02) ratios.push(Math.abs(step + q.z - p.z) / step); // the walker moves +Z
      }
    }
    ratios.sort((a, b) => a - b);
    return { median: ratios[ratios.length >> 1], samples: ratios.length };
  };
  for (const v of asset.variants) {
    const planted = slip(v, v.metresPerLoop);
    expect(planted.samples).toBeGreaterThan(200);
    expect(planted.median).toBeLessThan(0.06);
    expect(slip(v, v.metresPerLoop * 0.9).median).toBeGreaterThan(0.08); // a 10% stride error would show
  }
});

describe('rendering', () => {
  type ShaderBuilder = NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; build(): void; vertexShader: string; fragmentShader: string };
  const shaders = (velocityPass: boolean, counts: number[]) => {
    const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
    Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
    Object.assign(renderer, { hasFeature: () => false });
    if (velocityPass) { renderer.setRenderTarget(new THREE.RenderTarget()); renderer.setMRT(mrt({ output: normalView, velocity })); }
    const meshes = new WalkerMeshes(asset, 50 + RING.margin);
    const out = counts.flatMap(count => meshes.meshes.map(mesh => {
      mesh.count = count;
      const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(o: THREE.Object3D, r: THREE.WebGPURenderer): ShaderBuilder };
      const builder = backend.createNodeBuilder(mesh, renderer);
      builder.scene = new THREE.Scene(); builder.camera = new THREE.PerspectiveCamera();
      renderer.debug.onNodeBuilderCreated!(builder, { object: mesh });
      builder.build();
      return `${builder.vertexShader}\n----\n${builder.fragmentShader}`;
    }));
    meshes.dispose();
    return out;
  };

  test.each([false, true])('one program for every variant and 0 to 50 walkers (velocity=%s)', motion => {
    const programs = shaders(motion, [0, 1, 10, 30, 50]);
    expect(new Set(programs).size).toBe(1);
    expect(programs[0]).toMatch(/textureLoad/);
    expect(programs[0]).not.toMatch(/array<mat4x4<f32>,\s*\d+>/);
    if (motion) expect(programs[0]).toMatch(/positionPrevious = \( mix/);
  });

  test('the depth/normal prepass keeps the VAT program and nothing casts walker shadows', () => {
    const meshes = new WalkerMeshes(asset, 10);
    expect(new NormalPrepass().select(meshes.material)).toBeNull();
    expect(meshes.meshes.every(m => !m.castShadow && m.material === meshes.material)).toBe(true);
    meshes.update(Array.from({ length: 12 }, (_, i) => ({ variant: i % 3, x: i, y: 0, z: 0, yaw: 0, scale: 1, rowStart: 0, frames: 40,
      phase: 0, previousPhase: 0, skin: 0, top: 0, bottom: 0, hair: 0, shoes: 0, inner: 0, legwear: 0, fade: 1 })));
    expect(meshes.meshes.map(m => m.count)).toEqual([4, 4, 4]);
    meshes.update([]);
    expect(meshes.meshes.every(m => m.count === 0 && !m.visible)).toBe(true);
    meshes.dispose();
  });
});

test('hash32 values are pinned: a change re-dresses and re-routes every walker for every client', () => {
  expect([hash32(12345, 678), hash32(0), hash32(-5, 1 << 30)]).toMatchInlineSnapshot(`
    [
      3602965835,
      4172853615,
      3439941801,
    ]
  `);
  expect(hash32(1, 2, 3)).not.toBe(hash32(3, 2, 1));
});
