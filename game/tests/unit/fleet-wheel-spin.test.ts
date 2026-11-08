import './fleet-assets.fixture';
import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { AUTHORED_MODELS } from '../../src/actors/fleet/registry';
import { loadAuthoredFleetParts } from '../../src/actors/fleet/authored';

/** Rubber triangles (fleet_rubber: roughness 0.85, metalness 0 in fleetSurface): the tyre without rim, caliper or
 *  stray parts, whose asymmetry skews the measured axle by a few degrees. */
function tyreTriangles(g: THREE.BufferGeometry) {
  const surface = g.getAttribute('fleetSurface'), index = g.index!, rubber: number[] = [], all: number[] = [];
  for (let i = 0; i < index.count; i += 3) {
    const v = index.getX(i), tri = [v, index.getX(i + 1), index.getX(i + 2)];
    all.push(...tri);
    if (Math.abs(surface.getY(v) - .85) < .03 && surface.getZ(v) < .03) rubber.push(...tri);
  }
  return rubber.length >= 120 ? rubber : all;
}
/** The tyre axle of a hub-centred wheel part: the least-spread direction of its area-weighted tyre surface. */
function axleOf(g: THREE.BufferGeometry) {
  const p = g.getAttribute('position'), index = tyreTriangles(g), a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  const m = new THREE.Vector3(), e = new THREE.Vector3(), sum = new THREE.Vector3(), cov = new Array(9).fill(0);
  let area = 0;
  for (let i = 0; i < index.length; i += 3) {
    a.fromBufferAttribute(p, index[i]); b.fromBufferAttribute(p, index[i + 1]); c.fromBufferAttribute(p, index[i + 2]);
    const w = e.subVectors(b, a).cross(m.subVectors(c, a)).length() / 2;
    m.copy(a).add(b).add(c).divideScalar(3); area += w; sum.addScaledVector(m, w);
    const v = m.toArray(); for (let r = 0; r < 3; r++) for (let k = 0; k < 3; k++) cov[r * 3 + k] += w * v[r] * v[k];
  }
  const mean = sum.divideScalar(area).toArray();
  const s = [0, 1, 2].map(r => [0, 1, 2].map(k => cov[r * 3 + k] / area - mean[r] * mean[k])), v = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  for (let sweep = 0; sweep < 50; sweep++) for (let i = 0; i < 2; i++) for (let j = i + 1; j < 3; j++) {
    if (Math.abs(s[i][j]) < 1e-15) continue;
    const th = (s[j][j] - s[i][i]) / (2 * s[i][j]), t = Math.sign(th || 1) / (Math.abs(th) + Math.sqrt(th * th + 1));
    const cs = 1 / Math.sqrt(t * t + 1), sn = t * cs;
    for (let k = 0; k < 3; k++) { const pi = s[k][i], pj = s[k][j]; s[k][i] = cs * pi - sn * pj; s[k][j] = sn * pi + cs * pj; }
    for (let k = 0; k < 3; k++) { const pi = s[i][k], pj = s[j][k]; s[i][k] = cs * pi - sn * pj; s[j][k] = sn * pi + cs * pj; }
    for (let k = 0; k < 3; k++) { const pi = v[k][i], pj = v[k][j]; v[k][i] = cs * pi - sn * pj; v[k][j] = sn * pi + cs * pj; }
  }
  const k = [0, 1, 2].sort((x, y) => s[x][x] - s[y][y])[0];
  return new THREE.Vector3(v[0][k], v[1][k], v[2][k]).normalize();
}

/** Spins a wheel part through 360° at steer -0.5, 0 and +0.5 rad with the hub transform of fleet/visuals.ts (Euler
 *  YXZ, x = spin, y = steer; fleet/wheels.ts nests the same two rotations) and returns the largest angle between the
 *  tyre's axle and the axle the steer angle asks for (degrees): a posed (steered or cambered) source wheel wobbles
 *  like a tossed coin by this much. */
function lean(g: THREE.BufferGeometry) {
  const axle = axleOf(g), euler = new THREE.Euler(0, 0, 0, 'YXZ'), q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0);
  let worst = 0;
  for (const steer of [-.5, 0, .5]) {
    const expected = new THREE.Vector3(1, 0, 0).applyAxisAngle(up, steer);
    for (let deg = 0; deg < 360; deg += 15) {
      q.setFromEuler(euler.set(deg * Math.PI / 180, steer, 0, 'YXZ'));
      worst = Math.max(worst, Math.acos(Math.min(1, Math.abs(axle.clone().applyQuaternion(q).dot(expected)))) * 180 / Math.PI);
    }
  }
  return worst;
}

/** Radial runout about the spin axis (model X through the hub), metres: the spread of the outline radius over 10°
 *  sectors, sampled along every edge. A tyre off its hub bobs by twice the offset; a part outside the tyre (a mudguard,
 *  a second axle) sweeps around it; a flattened or bulged tyre rolls its flat spot round. */
function runout(g: THREE.BufferGeometry) {
  const p = g.getAttribute('position'), index = g.index!, sectors = new Float64Array(36);
  for (let i = 0; i < index.count; i += 3) for (let e = 0; e < 3; e++) {
    const a = index.getX(i + e), b = index.getX(i + (e + 1) % 3);
    for (let s = 0; s < 8; s++) {
      const t = s / 8, y = p.getY(a) * (1 - t) + p.getY(b) * t, z = p.getZ(a) * (1 - t) + p.getZ(b) * t;
      const k = Math.floor((Math.atan2(z, y) + Math.PI) / (2 * Math.PI) * 36) % 36;
      sectors[k] = Math.max(sectors[k], Math.hypot(y, z));
    }
  }
  return Math.max(...sectors) - Math.min(...sectors);
}

/** Known wheel faults that a later converter pass removes; each entry must still fail (a fixed model leaves the list). */
const PENDING: Record<string, string> = {
  'carla-carlamotors-european-hgv': 'the rear part holds both tandem axles (Wheel_Middle_* bones went to the nearest hub)',
  'carla-carlamotors-firetruck': 'hub 6 cm below the tyre centre; mudguards skinned to the wheel bones spin',
  'carla-ford-ambulance': 'arch parts skinned to the wheel bones spin with the tyres',
  'carla-mitsubishi-fusorosa': 'registry radius 0.47 m is the rim face, the tyre is ~0.62 m',
  'carla-carlamotors-carlacola': 'tyre outline not round in the source',
  'carla-audi-tt': 'stray rubber parts skinned to the rear wheel bones',
  'transit-bus': 'tyre outline not round in the source (flattened bottoms)',
  'garbage-truck': 'tyre outline not round in the source (tread blocks, flattened bottoms)',
  'octavia-rs-2020': 'coarse tyre outline in the source',
  'octavia-rs-2020-police': 'coarse tyre outline in the source',
  'yaris-2001': 'oval front tyres in the source',
  'carla-bmw-grandtourer': 'tyre outline not round in the source',
};

test('every fleet wheel rolls about its own axle and stays round at every steer angle', async () => {
  const bad: string[] = [], fixed: string[] = [];
  for (const model of AUTHORED_MODELS) {
    const parts = (await loadAuthoredFleetParts(model.id, 0))!, faults: string[] = [];
    for (const wheel of parts.wheels) {
      const tilt = lean(wheel.geometry), round = runout(wheel.geometry);
      // 1° of wobble moves the tread edge ~0.5 cm; 2 cm of outline bob reads as an egg on a 60 cm tyre.
      if (tilt > 1 || round > .02) faults.push(`${model.id} ${wheel.name} lean ${tilt.toFixed(1)}° runout ${(round * 100).toFixed(1)} cm`);
      wheel.geometry.dispose();
    }
    parts.body.dispose();
    if (model.id in PENDING) { if (!faults.length) fixed.push(model.id); } else bad.push(...faults);
  }
  expect(bad).toEqual([]);
  expect(fixed, 'pending models that now pass: remove them from PENDING').toEqual([]);
}, 60_000);
