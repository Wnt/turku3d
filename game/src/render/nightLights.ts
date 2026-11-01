// Night-only lit windows inside the existing facade graphs. Every term is scaled by uniforms, so day and night
// share one pipeline per material: nothing compiles at dusk and no draw, asset or per-building attribute is added.
import type * as THREE from 'three/webgpu';
import { dot, float, floor, fract, length, luminance, materialColor, materialRoughness, max, mix, normalWorldGeometry,
  positionWorld, renderGroup, smoothstep, step, uint, uniform, vec2, vec3 } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import { WINDOW_LIGHT } from './timecycle';

/** Global night uniforms for every material (and later light sprites). Environment.setSun writes them from the
 *  shared clock, so every client renders the same night factor and the same lit windows. */
export const nightUniforms = {
  /** 0 by day, 1 at full night. */
  night: uniform(0).setGroup(renderGroup),
  /** Local clock hour, 0..24. */
  hour: uniform(13).setGroup(renderGroup),
  /** Share of windows lit at this hour. */
  litFraction: uniform(0.25).setGroup(renderGroup),
};

type U32 = Node<'uint'>;
/** PCG integer hash: exact on every GPU and backend, unlike float sine hashes. */
function pcg(value: U32): U32 {
  const state = value.mul(747796405).add(2891336453);
  const word = state.shiftRight(state.shiftRight(28).add(4)).bitXor(state).mul(277803737);
  return word.shiftRight(22).bitXor(word);
}
const unit = (k: U32) => k.toFloat().mul(1 / 4294967296);
const salt = (k: U32, s: number) => pcg(k.bitXor(uint(s)));

/** Fold integer-valued floats into one key. Inputs must be exact integers on every GPU: floors of interpolated
 *  cell coordinates, or `quantise`d per-triangle constants. */
export function facadeKey(values: Node<'float'>[], seed = 0x9e3779b9): U32 {
  return values.reduce<U32>((k, v) => pcg(k.bitXor(v.toInt().toUint())), uint(seed));
}
/** A per-facade constant as an exact integer: a power-of-two scale cannot round, then floor. */
export const quantise = (v: Node<'float'>, scale: 64 | 1024 | 4096) => floor(v.mul(scale));

/**
 * Emitted window light for one cell (a bay on one storey) of a facade. `facade` identifies the wall,
 * `column`/`storey` are integer cell indices, `rise` is 0..1 up the storey and `glass` the glazing mask.
 * Each window re-rolls once per two clock hours at its own phase, against the hour's lit fraction plus a
 * per-storey bias: stable per window, changing through the night, deterministic from the shared clock.
 */
export function windowEmission(facade: U32, column: Node<'float'>, storey: Node<'float'>, rise: Node<'float'>, glass: Node<'float'>) {
  const window = pcg(facade.bitXor(facadeKey([column, storey], 0x85ebca6b)));
  const epoch = floor(nightUniforms.hour.mul(0.5).add(unit(salt(window, 0x68e31da4))));
  const roll = unit(pcg(window.bitXor(epoch.toInt().toUint())));
  const storeyBias = unit(salt(pcg(facade.bitXor(storey.toInt().toUint())), 0x51ed2701)).sub(0.5).mul(0.3);
  const lit = step(roll, nightUniforms.litFraction.add(storeyBias));
  const tone = unit(salt(window, 0x2545f491));
  const tint = mix(vec3(...WINDOW_LIGHT.warm), vec3(...WINDOW_LIGHT.cool), step(1 - WINDOW_LIGHT.coolShare, tone));
  const level = unit(salt(window, 0x7f4a7c15)).mul(0.7).add(0.65).mul(mix(0.75, 1.15, rise.clamp(0, 1)));
  return tint.mul(level.mul(glass).mul(lit).mul(nightUniforms.night).mul(WINDOW_LIGHT.intensity));
}

/** Kit glazing in the shared atlas: panes are authored at roughness 0.08 and metalness 0, frames at 0.6 and every
 *  wall material at 0.5 or above, so roughness alone separates glass on every kit version. */
export const glassMask = (roughness: Node<'float'>) => float(1).sub(smoothstep(0.2, 0.5, roughness));

/** World-space wall frame for facades without per-wall attributes (v1 kit, non-kit walls). */
export function wallFrame() {
  const n = normalWorldGeometry;
  const horizontal = n.xz.div(max(length(n.xz), 1e-3));
  const along = dot(positionWorld.xz, vec2(horizontal.y.negate(), horizontal.x));
  const plane = facadeKey([floor(dot(positionWorld.xz, horizontal).div(8))], 0xc2b2ae35);
  const wall = float(1).sub(smoothstep(0.3, 0.7, n.y.abs()));
  return { along, plane, wall };
}

/** Non-kit facades (stored/generated facade atlases, texture-library walls): a 2.6 m x 3.2 m world grid gives the
 *  cells; glazing comes from the existing ORM (generated facades share the kit's glass roughness) or, without one,
 *  from dark albedo. Library walls without windows stay dark. */
export function draftWindows(material: THREE.MeshStandardNodeMaterial) {
  const frame = wallFrame();
  const glass = material.roughnessMap ? glassMask(materialRoughness)
    : float(1).sub(smoothstep(0.14, 0.22, luminance(materialColor.rgb)));
  const storey = positionWorld.y.div(3.2);
  material.emissiveNode = windowEmission(frame.plane, floor(frame.along.div(2.6)), floor(storey), fract(storey),
    glass.mul(frame.wall));
  return material;
}
