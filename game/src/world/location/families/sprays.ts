// The leaf sprays of a hedge or shrub mass of the world pipeline, grown from its body where it is drawn.
//
// The pipeline stores a hedge as its closed body (family `hedge`, pipeline/turku3d/world/green/hedgegen.py) and
// not the cards that cover it: every body vertex carries `_spray` (sprays a square metre of surface, loose 0 | 1,
// place -1 underside | 0 side | 1 flat top) and the body's material `params.sprays` the card constants. Here the
// sprays are laid over the body's triangles as hedgegen laid them before (2026-11-13): on a side a spray climbs the
// surface, lifted off it by the form's tilt; on a top it stands and leans any way; each card is a quad of the
// foliage atlas cell, with the foliage family's COLOR_0 (shade, brightness, warmth) and `_wind`. The far level's
// body grows a share of them, larger. The cards come out the same at every load: the random numbers are seeded from
// the body.
//
// Unmown ground is stored the same way (pipeline/turku3d/world/green/tufts.py): a flat mesh of the covered squares
// with `_tuft` (0 meadow, 1 forest floor) in the family `tufts`, whose `params.tufts` lists what grows on each; the
// cards stand on it and the mesh itself is not drawn.
import * as THREE from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { familyOf } from '../materials';

export interface SprayParams {
  cell: [number, number, number, number]; tone: [number, number]; vary: [number, number]; dense: number; share: number; grow: number;
  size: [number, number][]; tilt: [number, number][]; stand: [number, number]; lift: number[]; wind: number;
}

/** A small seeded generator (mulberry32): the same body grows the same sprays everywhere. */
function random(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function seedOf(position: THREE.BufferAttribute | THREE.InterleavedBufferAttribute) {
  let hash = 2166136261 ^ position.count;
  for (let k = 0; k < Math.min(position.count, 8); k++) {
    for (const value of [position.getX(k), position.getY(k), position.getZ(k)]) hash = Math.imul(hash ^ Math.round(value * 1000), 16777619);
  }
  return hash >>> 0;
}

const GRID = [[-0.5, 0], [0.5, 0], [-0.5, 1], [0.5, 1]] as const;

/** The spray cards of one body geometry, or null when it grows none. */
export function growSprays(body: THREE.BufferGeometry, params: SprayParams): THREE.BufferGeometry | null {
  const position = body.getAttribute('position'), normal = body.getAttribute('normal'), spray = body.getAttribute('_spray');
  const shade = body.getAttribute('color'), index = body.getIndex();
  if (!position || !normal || !spray || !index) return null;
  const next = random(seedOf(position));
  const gauss = () => Math.sqrt(-2 * Math.log(1 - next())) * Math.cos(2 * Math.PI * next());
  const [u0, u1, v0, v1] = params.cell;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), ab = new THREE.Vector3(), ac = new THREE.Vector3();
  const na = new THREE.Vector3(), nb = new THREE.Vector3(), nc = new THREE.Vector3();
  const p = new THREE.Vector3(), n = new THREE.Vector3(), climb = new THREE.Vector3(), around = new THREE.Vector3(), cross = new THREE.Vector3();
  const up = new THREE.Vector3(), side = new THREE.Vector3(), start = new THREE.Vector3(), lean = new THREE.Vector3(), corner = new THREE.Vector3();
  const UP = new THREE.Vector3(0, 1, 0), X = new THREE.Vector3(1, 0, 0);
  // First the cards per triangle (by area and density), so that the arrays are made once at their size.
  const triangles = index.count / 3, counts = new Uint32Array(triangles);
  let cards = 0;
  for (let f = 0; f < triangles; f++) {
    const i = index.getX(3 * f), j = index.getX(3 * f + 1), k = index.getX(3 * f + 2);
    if (spray.getZ(i) < -0.5 && spray.getZ(j) < -0.5 && spray.getZ(k) < -0.5) continue;
    const density = Math.min(spray.getX(i), spray.getX(j), spray.getX(k)) * params.dense * params.share;
    if (density <= 0) continue;
    a.fromBufferAttribute(position, i); b.fromBufferAttribute(position, j); c.fromBufferAttribute(position, k);
    const expected = 0.5 * cross.crossVectors(ab.subVectors(b, a), ac.subVectors(c, a)).length() * density;
    counts[f] = Math.floor(expected) + (next() < expected % 1 ? 1 : 0);
    cards += counts[f];
  }
  if (!cards) return null;
  const positions = new Float32Array(cards * 12), normals = new Float32Array(cards * 12), uvs = new Float32Array(cards * 8);
  const colors = new Float32Array(cards * 12), winds = new Float32Array(cards * 16);
  let v = 0;
  for (let f = 0; f < triangles; f++) {
    const count = counts[f];
    if (!count) continue;
    const i = index.getX(3 * f), j = index.getX(3 * f + 1), k = index.getX(3 * f + 2);
    const place = [spray.getZ(i), spray.getZ(j), spray.getZ(k)];
    a.fromBufferAttribute(position, i); b.fromBufferAttribute(position, j); c.fromBufferAttribute(position, k);
    const top = place.every(value => value > 0.5), form = spray.getY(i) > 0.5 ? 1 : 0;
    na.fromBufferAttribute(normal, i); nb.fromBufferAttribute(normal, j); nc.fromBufferAttribute(normal, k);
    const [size0, size1] = params.size[form], [tilt0, tilt1] = params.tilt[form], lift = params.lift[form];
    for (let m = 0; m < count; m++) {
      let s = next(), r = next();
      if (s + r > 1) { s = 1 - s; r = 1 - r; }
      const w = 1 - s - r;
      p.copy(a).multiplyScalar(w).addScaledVector(b, s).addScaledVector(c, r);
      if (top) n.copy(UP);
      else n.copy(na).multiplyScalar(w).addScaledVector(nb, s).addScaledVector(nc, r).normalize();
      const light = shade ? shade.getX(i) * w + shade.getX(j) * s + shade.getX(k) * r : 1;
      const size = (size0 + (size1 - size0) * next()) * params.grow;
      // On a side a spray climbs the surface; on a top it has no way up and stands in any direction.
      climb.copy(UP).addScaledVector(n, -n.y);
      const standing = climb.length() < 0.45;
      if (standing) {
        around.crossVectors(n, Math.abs(n.y) < 0.9 ? UP : X).normalize();
        const turn = next() * 2 * Math.PI;
        climb.copy(around).multiplyScalar(Math.cos(turn)).addScaledVector(cross.crossVectors(n, around), Math.sin(turn));
      }
      climb.normalize();
      const [lo, hi] = standing ? params.stand : [tilt0, tilt1];
      const tilt = THREE.MathUtils.degToRad(lo + (hi - lo) * next());
      up.copy(climb).multiplyScalar(Math.cos(tilt)).addScaledVector(n, Math.sin(tilt));
      up.x += 0.12 * gauss(); up.y += 0.12 * gauss(); up.z += 0.12 * gauss();
      up.normalize();
      side.crossVectors(up, n).normalize();
      start.copy(p).addScaledVector(n, lift * next() - 0.03).addScaledVector(up, -0.3 * size);
      lean.copy(n).multiplyScalar(0.8).addScaledVector(UP, 0.2).normalize();
      const brightness = params.tone[0] * (1 + 1.5 * params.vary[0] * (2 * next() - 1));
      const warmth = 1 + params.tone[1] + params.vary[1] * (2 * next() - 1);
      const sway = next();
      for (const [gx, gy] of GRID) {
        corner.copy(start).addScaledVector(side, gx * size).addScaledVector(up, gy * size);
        corner.toArray(positions, v * 3); lean.toArray(normals, v * 3);
        uvs[v * 2] = u0 + (u1 - u0) * (gx + 0.5); uvs[v * 2 + 1] = v1 + (v0 - v1) * gy;
        colors[v * 3] = 0.45 + 0.55 * light; colors[v * 3 + 1] = brightness; colors[v * 3 + 2] = warmth;
        winds[v * 4] = params.wind; winds[v * 4 + 1] = gy * 0.5; winds[v * 4 + 2] = sway; winds[v * 4 + 3] = 1;
        v++;
      }
    }
  }
  return cardGeometry(cards, positions, normals, uvs, colors, winds);
}

function cardGeometry(cards: number, positions: Float32Array, normals: Float32Array, uvs: Float32Array, colors: Float32Array, winds: Float32Array) {
  const faces = new Uint32Array(cards * 6);
  for (let q = 0; q < cards; q++) faces.set([q * 4, q * 4 + 1, q * 4 + 2, q * 4 + 1, q * 4 + 3, q * 4 + 2], q * 6);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  geometry.setAttribute('_wind', new THREE.BufferAttribute(winds, 4));
  geometry.setIndex(new THREE.BufferAttribute(faces, 1));
  geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  return geometry;
}

export interface TuftParams {
  plants: { cell: [number, number, number, number]; density: number; across: [number, number]; tone: [number, number] }[][];
  share: number; grow: number; vary: [number, number];
}

const SHADE = [0.55, 0.55, 1, 1];

/** The grass, flower and undergrowth cards standing on a ground mesh of unmown squares, or null. */
export function growTufts(ground: THREE.BufferGeometry, params: TuftParams): THREE.BufferGeometry | null {
  const position = ground.getAttribute('position'), kind = ground.getAttribute('_tuft'), index = ground.getIndex();
  if (!position || !kind || !index) return null;
  const next = random(seedOf(position) ^ 0x9e3779b9);
  const gauss = () => Math.sqrt(-2 * Math.log(1 - next())) * Math.cos(2 * Math.PI * next());
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), ab = new THREE.Vector3(), ac = new THREE.Vector3();
  const foot = new THREE.Vector3(), up = new THREE.Vector3(), corner = new THREE.Vector3();
  const triangles = index.count / 3, counts = new Uint32Array(triangles * 2);
  let cards = 0;
  for (let f = 0; f < triangles; f++) {
    const i = index.getX(3 * f), j = index.getX(3 * f + 1), k = index.getX(3 * f + 2);
    const plants = params.plants[Math.round(kind.getX(i))] ?? [];
    a.fromBufferAttribute(position, i); b.fromBufferAttribute(position, j); c.fromBufferAttribute(position, k);
    const area = 0.5 * ab.subVectors(b, a).cross(ac.subVectors(c, a)).length();
    plants.forEach((plant, p) => {
      const expected = plant.density * area * params.share;
      counts[f * 2 + p] = Math.floor(expected) + (next() < expected % 1 ? 1 : 0);
      cards += counts[f * 2 + p];
    });
  }
  if (!cards) return null;
  const positions = new Float32Array(cards * 12), normals = new Float32Array(cards * 12), uvs = new Float32Array(cards * 8);
  const colors = new Float32Array(cards * 12), winds = new Float32Array(cards * 16);
  let v = 0;
  for (let f = 0; f < triangles; f++) {
    const i = index.getX(3 * f), j = index.getX(3 * f + 1), k = index.getX(3 * f + 2);
    const plants = params.plants[Math.round(kind.getX(i))] ?? [];
    a.fromBufferAttribute(position, i); b.fromBufferAttribute(position, j); c.fromBufferAttribute(position, k);
    plants.forEach((plant, p) => {
      const [u0, u1, v0, v1] = plant.cell;
      for (let m = 0; m < counts[f * 2 + p]; m++) {
        let s = next(), r = next();
        if (s + r > 1) { s = 1 - s; r = 1 - r; }
        foot.copy(a).multiplyScalar(1 - s - r).addScaledVector(b, s).addScaledVector(c, r);
        foot.y -= 0.04;
        const size = (plant.across[0] + (plant.across[1] - plant.across[0]) * next()) * params.grow;
        const yaw = next() * 2 * Math.PI, sx = Math.cos(yaw), sz = Math.sin(yaw);
        up.set(0.12 * gauss(), 1, 0.12 * gauss()).normalize();
        const brightness = plant.tone[0] * (1 + 1.5 * params.vary[0] * (2 * next() - 1));
        const warmth = 1 + plant.tone[1] + params.vary[1] * (2 * next() - 1);
        const sway = next();
        GRID.forEach(([gx, gy], q) => {
          corner.set(foot.x + sx * gx * size, foot.y, foot.z + sz * gx * size).addScaledVector(up, gy * size);
          corner.toArray(positions, v * 3);
          normals[v * 3 + 1] = 1;
          uvs[v * 2] = u0 + (u1 - u0) * (gx + 0.5); uvs[v * 2 + 1] = v1 + (v0 - v1) * gy;
          colors[v * 3] = SHADE[q]; colors[v * 3 + 1] = brightness; colors[v * 3 + 2] = warmth;
          winds[v * 4] = 0.05; winds[v * 4 + 1] = gy * 0.6; winds[v * 4 + 2] = sway; winds[v * 4 + 3] = 1;
          v++;
        });
      }
    });
  }
  return cardGeometry(cards, positions, normals, uvs, colors, winds);
}

/** The glTF-like material the sprays are drawn with: the foliage family on the nature atlas (the location's
 *  material pass turns it into the shared leaf material, as it does a tree's leaves). */
let leafMaterial: THREE.MeshStandardMaterial | null = null;
function leaves() {
  if (!leafMaterial) {
    leafMaterial = new THREE.MeshStandardMaterial({ roughness: 0.92, metalness: 0, side: THREE.DoubleSide });
    leafMaterial.color.setRGB(0.07, 0.13, 0.035, THREE.LinearSRGBColorSpace);
    leafMaterial.name = 'foliage';
    leafMaterial.userData = { turku: { family: 'foliage', params: { atlas: 'nature', uv: 'atlas' } } };
  }
  return leafMaterial;
}

const LEAF_LAYOUT = ['_wind:4', 'color:3', 'normal:3', 'position:3', 'uv:2'].join();
const layout = (geometry: THREE.BufferGeometry) => Object.entries(geometry.attributes).map(([name, a]) => `${name}:${a.itemSize}`).sort().join();

/** Gives the hedge bodies of a loaded model their sprays and its unmown ground its cards: one leaf mesh for the whole model, merged with the
 *  model's own leaf cards (a far cell's shrubs and tufts) when it has a mesh of them, so that a cell costs one leaf
 *  draw. Called once per loaded glTF scene, before its materials are dressed. Returns the number of cards grown. */
export function growHedges(scene: THREE.Object3D) {
  const bodies: THREE.Mesh[] = [], grounds: THREE.Mesh[] = [];
  let foliage: THREE.Mesh | null = null;
  scene.updateMatrixWorld(true);
  scene.traverse(node => {
    const mesh = node as THREE.Mesh;
    if (!mesh.isMesh || Array.isArray(mesh.material)) return;
    if (mesh.geometry.getAttribute('_spray')) bodies.push(mesh);
    else if (mesh.geometry.getAttribute('_tuft')) grounds.push(mesh);
    else if (!foliage && familyOf(mesh.material)?.family === 'foliage' && layout(mesh.geometry) === LEAF_LAYOUT && mesh.geometry.index) foliage = mesh;
  });
  const grown: THREE.BufferGeometry[] = [];
  for (const body of bodies) {
    const params = familyOf(body.material as THREE.Material)?.params?.sprays as SprayParams | undefined;
    const geometry = params ? growSprays(body.geometry, params) : null;
    if (geometry) grown.push(geometry.applyMatrix4(body.matrixWorld));
  }
  for (const ground of grounds) {
    const params = familyOf(ground.material as THREE.Material)?.params?.tufts as TuftParams | undefined;
    const geometry = params ? growTufts(ground.geometry, params) : null;
    if (geometry) grown.push(geometry.applyMatrix4(ground.matrixWorld));
    // The squares only carry the cards: nothing draws them.
    ground.removeFromParent();
    ground.geometry.dispose();
  }
  if (!grown.length) return 0;
  const cards = grown.reduce((sum, geometry) => sum + geometry.getIndex()!.count / 6, 0);
  const host = foliage as THREE.Mesh | null;
  if (host) {
    const own = host.geometry.clone().applyMatrix4(host.matrixWorld);
    const merged = mergeGeometries([own, ...grown], false)!;
    for (const geometry of [own, ...grown]) geometry.dispose();
    host.geometry.dispose();
    host.geometry = merged.applyMatrix4(host.matrixWorld.clone().invert());
  } else {
    const merged = grown.length > 1 ? mergeGeometries(grown, false)! : grown[0];
    if (grown.length > 1) for (const geometry of grown) geometry.dispose();
    const mesh = new THREE.Mesh(merged, leaves());
    mesh.name = 'sprays';
    scene.add(mesh);
  }
  scene.updateMatrixWorld(true);
  return cards;
}
