import { describe, expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { LightSprites } from '../../src/render/lights/sprites';
import { areaVenues } from '../../src/render/lights/floodArea';
import { FloodGlow, GLOW, beamsOf, domeIntegral, domeOf, domeScale, erf, mistOf } from '../../src/render/lights/glow';
import { WEATHER_PRESETS } from '../../src/render/weather/schedule';
import type { FloodMast } from '../../src/render/lights/floodlights';

const [V] = areaVenues([{ id: 'v', kind: 'stadium', x: 100, y: 20, z: -50, ux: 0.6, uz: 0.8, length: 104, width: 64, level: 1 }], []);
const D = domeOf(V);
const MAST: FloodMast = { id: 'm', venue: 'v', x: 160, y: 20, z: 0, top: 50, aim: [100, -50], lamps: 8, mesh: 0 };

/** Numeric line integral of the dome's unit Gaussian density. */
function quadrature(o: THREE.Vector3, dir: THREE.Vector3, tEnd: number, steps = 20000) {
  const u = new THREE.Vector3(D.ux, 0, D.uz), w = new THREE.Vector3(-D.uz, 0, D.ux), p = new THREE.Vector3();
  let sum = 0;
  const dt = tEnd / steps;
  for (let i = 0; i < steps; i++) {
    p.copy(o).addScaledVector(dir, (i + 0.5) * dt).sub(new THREE.Vector3(D.x, D.y, D.z));
    sum += Math.exp(-((p.dot(u) / D.sa) ** 2 + (p.dot(w) / D.sb) ** 2 + (p.y / D.sy) ** 2)) * dt;
  }
  return sum;
}

describe('light dome over a lit venue', () => {
  test('erf matches its series within 2e-4', () => {
    for (const x of [-2, -0.5, 0, 0.3, 1, 1.7, 3]) {
      let series = 0, term = x;
      for (let n = 0; n < 60; n++) { series += term / (2 * n + 1); term *= -x * x / (n + 1); }
      expect(Math.abs(erf(x) - series * 2 / Math.sqrt(Math.PI))).toBeLessThan(2e-4);
    }
  });

  test('the closed-form glow integral equals quadrature from outside, inside and against a wall', () => {
    const cases: [THREE.Vector3, THREE.Vector3, number][] = [
      [new THREE.Vector3(-900, 40, 400), new THREE.Vector3(1000, -10, -450).normalize(), 3000],
      [new THREE.Vector3(100, 30, -50), new THREE.Vector3(0.3, 0.2, 1).normalize(), 600],
      [new THREE.Vector3(-300, 117, 600), new THREE.Vector3(400, -97, -650).normalize(), 500],
      [new THREE.Vector3(-300, 117, 600), new THREE.Vector3(400, -97, -650).normalize(), 380],
    ];
    for (const [o, dir, tEnd] of cases) {
      const exact = quadrature(o, dir, tEnd), closed = domeIntegral(D, o, dir, tEnd);
      expect(Math.abs(closed - exact)).toBeLessThan(2e-3 * Math.max(1, exact));
    }
  });

  test('the peak radiance is set along a horizontal line through the middle', () => {
    const along = new THREE.Vector3(D.ux, 0, D.uz);
    const from = new THREE.Vector3(D.x, D.y, D.z).addScaledVector(along, -2000);
    const through = domeIntegral(D, from, along, 4000) * domeScale({ ...D, sb: D.sa }, 0.2);
    expect(through).toBeCloseTo(0.2, 3);
    // The dome stands over the field and well above the masts.
    expect(D.y).toBeGreaterThan(V.y);
    expect(D.sy).toBeGreaterThan(0.5 * V.height);
    expect(D.sa).toBeGreaterThan(V.a * 0.5);
  });

  test('fog and rain thicken the air', () => {
    const [clear, fog, rain] = [WEATHER_PRESETS.clear, WEATHER_PRESETS.fog, WEATHER_PRESETS['heavy rain']].map(mistOf);
    expect(clear).toBe(0);
    expect(fog).toBe(1);
    expect(rain).toBeGreaterThan(0.6);
    expect(GLOW.misty).toBeGreaterThan(4 * GLOW.clear);
  });
});

describe('beams from the lamp heads', () => {
  test('each mast casts a widening beam toward its field', () => {
    const [beam] = beamsOf([MAST], new Map([['v', V]]));
    expect(beam.from.slice(0, 3)).toEqual([160, 49.6, 0]);
    const to = new THREE.Vector3(...beam.to.slice(0, 3));
    expect(to.x).toBeCloseTo(160 + (100 - 160) * GLOW.beamReach, 5);
    expect(to.y).toBeCloseTo(50 + (20 - 50) * GLOW.beamReach, 5);
    expect(beam.to[3]).toBeGreaterThan(beam.from[3] * 4);
    expect(beam.level).toBe(1);
  });
});

describe('glow programs', () => {
  type ShaderBuilder = NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; build(): void; vertexShader: string; fragmentShader: string };
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  Object.assign(renderer, { hasFeature: () => false });
  Object.assign(renderer.backend, { renderer });
  const build = (object: THREE.Mesh) => {
    const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: THREE.Object3D, renderer: THREE.WebGPURenderer): ShaderBuilder };
    const builder = backend.createNodeBuilder(object, renderer);
    builder.scene = new THREE.Scene(); builder.camera = new THREE.PerspectiveCamera();
    renderer.debug.onNodeBuilderCreated!(builder, { object });
    builder.build();
    return [builder.vertexShader, builder.fragmentShader];
  };

  test('domes and beams compile one program each for any venue count, weather and hour', () => {
    const programs = [[0, 0, 0], [1, 1, 1]].map(([count, night, mist]) => {
      const sprites = new LightSprites('medium');
      const glow = new FloodGlow(sprites.scene, sprites.sceneDepth);
      glow.setVenues(count ? [V] : [], count ? [MAST] : []);
      glow.update(new THREE.PerspectiveCamera(), night, mist, 800);
      expect(glow.domes.geometry.instanceCount).toBe(1);
      expect(sprites.scene.children).toContain(glow.domes);
      return [...build(glow.domes), ...build(glow.beams)];
    });
    expect(programs[1]).toEqual(programs[0]);
    expect(programs[0][1]).toMatch(/texture_depth_2d/);
    expect(programs[0][3]).toMatch(/texture_depth_2d/);
  });
});
