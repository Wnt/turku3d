import * as THREE from 'three/webgpu';
import { Fn, instancedBufferAttribute, materialColor, mix, sRGBTransferEOTF, step, uniform, uv } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';

// r186 declares the colour-space Fn without its vec3 layout.
const decode = sRGBTransferEOTF as unknown as (srgb: Node<'vec3'>) => Node<'vec3'>;

// The draft generator bakes one paint into each parked-car GLB (pipeline/turku3d/parked/models.py):
// COLORS (sRGB bytes) fill the model's 64 px column, rows 128..255 of the 512 px Kenney colormap,
// shaded linearly from 1.0 to 0.78. The rest of the texture (glass, tyres, lamps, trim) is identical
// for every paint, so one batch per body model redraws only that swatch from a per-instance colour.
export const PAINT_COLORS: Readonly<Record<string, readonly [number, number, number]>> = {
  silver: [165, 174, 181], white: [224, 228, 225], charcoal: [54, 61, 69],
  blue: [38, 78, 126], red: [138, 38, 39], beige: [154, 143, 118],
};
export const PAINT_COLUMNS: Readonly<Record<string, number>> = { sedan: 6, suv: 3, 'hatchback-sports': 3, van: 7,
  'sedan-sports': 6, 'suv-luxury': 4 };
export const PAINT_SWATCH = { atlas: 512, column: 64, firstRow: 128, rows: 128, darkest: 0.78 } as const;
const { atlas: ATLAS, column: COLUMN, firstRow: FIRST_ROW, rows: ROWS, darkest: DARKEST } = PAINT_SWATCH;

/** Batch identity: recolourable models share one draw for every paint; any other model keeps
 * one draw per authored GLB (`model/color`), exactly as supplied. */
export function paintBatch(model: string, color: string): string {
  return model in PAINT_COLUMNS && color in PAINT_COLORS ? model : `${model}/${color}`;
}

export function recolourable(batch: string) { return batch in PAINT_COLUMNS; }

/** Paint as the shader reads it: sRGB in 0..1. */
export function paintOf(color: string): readonly [number, number, number] {
  const [r, g, b] = PAINT_COLORS[color];
  return [r / 255, g / 255, b / 255];
}

// A batch's per-instance paints are a node attribute of that draw, like its instance matrices:
// r186 binds the InstancedBufferAttribute itself (instance step, re-uploaded on needsUpdate), and
// the renderer releases it with the InstancedMesh. A driven or remote car
// carries its paint as an object uniform, so it needs no buffer or geometry of its own.
const instancePaints = new WeakMap<THREE.Object3D, THREE.InstancedBufferAttribute>();
const actorPaints = new WeakMap<THREE.Object3D, THREE.Vector3>();

/** Writes `colors` as the paints of instances offset..offset+n-1 of `mesh` (0..n-1 by default). */
export function paintInstances(mesh: THREE.InstancedMesh, colors: readonly string[], offset = 0) {
  let paints = instancePaints.get(mesh);
  if (!paints) {
    paints = new THREE.InstancedBufferAttribute(new Float32Array(mesh.instanceMatrix.count * 3), 3);
    instancePaints.set(mesh, paints);
  }
  colors.forEach((color, i) => paints.setXYZ(offset + i, ...paintOf(color)));
  paints.needsUpdate = true;
}

export function paintActor(mesh: THREE.Mesh, color: string) {
  actorPaints.set(mesh, new THREE.Vector3(...paintOf(color)));
}

export function instancePaintOf(mesh: THREE.Object3D) { return instancePaints.get(mesh); }
export function actorPaintOf(mesh: THREE.Object3D) { return actorPaints.get(mesh); }

// Node graphs build per object: an instanced batch binds its own paint buffer, a standalone draw
// its object uniform. Neither the count nor the paints enter the shader text.
const paintNode = Fn((builder: NodeBuilder) => {
  const instances = instancePaints.get(builder.object);
  if (instances) return instancedBufferAttribute(instances, 'vec3');
  if (!actorPaints.has(builder.object)) throw new Error(`parked paint missing for ${builder.object.name}`);
  return uniform(new THREE.Vector3()).onObjectUpdate(({ object }) => actorPaints.get(object!));
}) as unknown as () => Node<'vec3'>;

/** The body material of `model` with its paint swatch taken from the draw's paint. Every model
 * compiles to the same program; only the swatch uniform differs. */
export function paintMaterial(source: THREE.MeshStandardMaterial, model: string): THREE.MeshStandardNodeMaterial {
  const material = new THREE.MeshStandardNodeMaterial({
    name: `${model}/paint`, color: source.color, map: source.map, roughness: source.roughness, metalness: source.metalness,
    roughnessMap: source.roughnessMap, metalnessMap: source.metalnessMap, normalMap: source.normalMap, normalScale: source.normalScale,
    aoMap: source.aoMap, emissive: source.emissive, emissiveMap: source.emissiveMap, side: source.side,
  });
  const column = PAINT_COLUMNS[model];
  const swatch = uniform(new THREE.Vector4(column * COLUMN / ATLAS, FIRST_ROW / ATLAS, (column + 1) * COLUMN / ATLAS, (FIRST_ROW + ROWS) / ATLAS));
  const p = uv();
  // A branch-free mask keeps the texture sample in uniform control flow for its mip derivatives.
  // step(edge, x) is x >= edge: the swatch spans [x, z) × [y, w).
  const inside = step(swatch.x, p.x).mul(step(swatch.z, p.x).oneMinus()).mul(step(swatch.y, p.y)).mul(step(swatch.w, p.y).oneMinus());
  // Texel centres carry the authored shade; bilinear sampling of that ramp is the same line.
  const shade = p.y.mul(ATLAS).sub(FIRST_ROW + 0.5).div(ROWS - 1).clamp(0, 1).mul(DARKEST - 1).add(1);
  const paint = decode(paintNode().mul(shade)).mul(uniform(material.color));
  material.colorNode = mix(materialColor, paint, inside);
  return material;
}
