import * as THREE from 'three/webgpu';
import { attribute, materialColor, sRGBTransferEOTF, select, uniform, uv } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';

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
export const PAINT_COLUMNS: Readonly<Record<string, number>> = { sedan: 6, suv: 3, 'hatchback-sports': 3, van: 7 };
const ATLAS = 512, COLUMN = 64, FIRST_ROW = 128, ROWS = 128, DARKEST = 0.78;

/** Batch identity: recolourable models share one draw for every paint; any other model keeps
 * one draw per authored GLB (`model/color`), exactly as supplied. */
export function paintBatch(model: string, color: string): string {
  return model in PAINT_COLUMNS && color in PAINT_COLORS ? model : `${model}/${color}`;
}

export function recolourable(batch: string) { return batch in PAINT_COLUMNS; }

/** Paint as stored in the `paint` attribute: sRGB in 0..1. */
export function paintOf(color: string): readonly [number, number, number] {
  const [r, g, b] = PAINT_COLORS[color];
  return [r / 255, g / 255, b / 255];
}

/** The body material of `model` with its paint swatch taken from the per-instance `paint`
 * attribute. Every model compiles to the same program; only the swatch uniform differs. */
export function paintMaterial(source: THREE.MeshStandardMaterial, model: string): THREE.MeshStandardNodeMaterial {
  const material = new THREE.MeshStandardNodeMaterial({
    name: `${model}/paint`, color: source.color, map: source.map, roughness: source.roughness, metalness: source.metalness,
    roughnessMap: source.roughnessMap, metalnessMap: source.metalnessMap, normalMap: source.normalMap, normalScale: source.normalScale,
    aoMap: source.aoMap, emissive: source.emissive, emissiveMap: source.emissiveMap, side: source.side,
  });
  const column = PAINT_COLUMNS[model];
  const swatch = uniform(new THREE.Vector4(column * COLUMN / ATLAS, FIRST_ROW / ATLAS, (column + 1) * COLUMN / ATLAS, (FIRST_ROW + ROWS) / ATLAS));
  const p = uv();
  const inside = p.x.greaterThanEqual(swatch.x).and(p.x.lessThan(swatch.z)).and(p.y.greaterThanEqual(swatch.y)).and(p.y.lessThan(swatch.w));
  // Texel centres carry the authored shade; bilinear sampling of that ramp is the same line.
  const shade = p.y.mul(ATLAS).sub(FIRST_ROW + 0.5).div(ROWS - 1).clamp(0, 1).mul(DARKEST - 1).add(1);
  const paint = decode(attribute('paint', 'vec3').mul(shade)).mul(uniform(material.color));
  material.colorNode = select(inside, paint, materialColor);
  return material;
}

/** A draw's own view of `source`: shared vertex buffers plus its per-instance paint. Never dispose
 * it: r186 geometry disposal also destroys the shared buffers; the paint buffer is collected with it. */
export function paintGeometry(source: THREE.BufferGeometry, capacity: number): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry();
  geometry.setIndex(source.index);
  for (const [name, value] of Object.entries(source.attributes)) geometry.setAttribute(name, value);
  geometry.setAttribute('paint', new THREE.InstancedBufferAttribute(new Float32Array(capacity * 3), 3));
  if (!source.boundingBox) source.computeBoundingBox();
  if (!source.boundingSphere) source.computeBoundingSphere();
  geometry.boundingBox = source.boundingBox!.clone();
  geometry.boundingSphere = source.boundingSphere!.clone();
  return geometry;
}

export function setPaint(geometry: THREE.BufferGeometry, colors: readonly string[]) {
  const attribute = geometry.getAttribute('paint') as THREE.InstancedBufferAttribute;
  colors.forEach((color, i) => attribute.setXYZ(i, ...paintOf(color)));
  attribute.needsUpdate = true;
}
