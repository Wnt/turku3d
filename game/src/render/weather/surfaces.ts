// Wet mineral surfaces share uniforms: transitions and tile streaming never rebuild their shader graphs.
import * as THREE from 'three/webgpu';
import { Fn, If, float, materialColor, materialRoughness, mix, normalWorldGeometry, positionWorld, smoothstep, texture, vec2, vec4 } from 'three/tsl';

import { weatherHeightField, weatherUniforms } from './surfaceState';
export { weatherUniforms } from './surfaceState';
let detail: THREE.DataTexture | undefined;
export function weatherNoise() {
  if (detail) return detail;
  const size = 64, data = new Uint8Array(size * size * 4);
  let state = 823941;
  for (let i = 0; i < data.length; i += 4) { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; data.set([state >>> 24, state >>> 16 & 255, state >>> 8 & 255, 255], i); }
  detail = new THREE.DataTexture(data, size, size);
  detail.wrapS = detail.wrapT = THREE.RepeatWrapping;
  detail.magFilter = THREE.LinearFilter; detail.minFilter = THREE.LinearMipmapLinearFilter;
  detail.generateMipmaps = true; detail.needsUpdate = true;
  return detail;
}
/** Exposure and signed depression, bilinearly reconstructed from the worker's two-metre roof/terrain grid. */
export function weatherSurface(position: THREE.Node<'vec3'>) {
  return Fn(() => {
    const result = vec2(1, 0).toVar();
    const coord = position.xz.sub(weatherHeightField.origin).div(64);
    If(weatherHeightField.ready.greaterThan(0).and(coord.x.greaterThan(0)).and(coord.x.lessThan(1))
      .and(coord.y.greaterThan(0)).and(coord.y.lessThan(1)), () => {
      const cell = coord.mul(32).sub(.5), base = cell.floor(), fraction = cell.fract();
      const at = (x: number, y: number) => weatherHeightField.map.sample(base.add(vec2(x + .5, y + .5)).div(32)).level(float(0));
      const a = at(0, 0), b = at(1, 0), c = at(0, 1), d = at(1, 1);
      const exposedAt = (value: THREE.Node<'vec4'>) => float(1).sub(smoothstep(.25, .65, value.r.sub(position.y)));
      const exposed = mix(mix(exposedAt(a), exposedAt(b), fraction.x), mix(exposedAt(c), exposedAt(d), fraction.x), fraction.y);
      const basinAt = (value: THREE.Node<'vec4'>) => value.b.mul(float(1).sub(smoothstep(.25, .65, value.r.sub(position.y).abs())));
      const basin = mix(mix(basinAt(a), basinAt(b), fraction.x), mix(basinAt(c), basinAt(d), fraction.x), fraction.y);
      const inside = coord.min(float(1).sub(coord));
      const edge = inside.x.min(inside.y).mul(64);
      const blend = smoothstep(2, 7, edge);
      result.assign(vec2(mix(1, exposed, blend), basin.mul(blend)));
    });
    return result;
  })();
}
/** The same world-space puddle pattern drives the material film, impact rings and screen reflections. */
export function puddleMask(position: THREE.Node<'vec3'>, wet: THREE.Node<'float'>, up: THREE.Node<'float'>, lod?: number, field: THREE.Node<'vec2'> = weatherSurface(position)) {
  const broad = texture(weatherNoise(), position.xz.mul(.026));
  const fine = texture(weatherNoise(), position.xz.mul(.18));
  const noise = lod === undefined ? broad.r : broad.level(float(lod)).r;
  const grain = lod === undefined ? fine.g : fine.level(float(lod)).g;
  return smoothstep(float(.83).sub(wet.mul(.5)), float(.98).sub(wet.mul(.5)), noise.mul(.7).add(grain.mul(.3)).add(field.y.mul(3)))
    .mul(up).mul(field.x).mul(smoothstep(.35, .95, wet));
}
export function wetSurface(material: THREE.MeshStandardNodeMaterial, facade = false, mineral: THREE.Node<'float'> = float(1)) {
  if (material.userData.weatherWetness) return material;
  material.userData.weatherWetness = true;
  const baseColor = (material.colorNode ?? materialColor.rgb) as THREE.Node<'vec3'>;
  const baseRoughness = (material.roughnessNode ?? materialRoughness) as THREE.Node<'float'>;
  const surface = Fn(() => {
    const result = vec4(baseColor, baseRoughness).toVar();
    // Facades have no pools: do not add an otherwise unused geometric-normal varying to their rich shader.
    const up = facade ? float(0) : smoothstep(.8, .98, normalWorldGeometry.y).toVar();
    // No wet-noise sampling in dry weather. The branch is uniform across the whole draw.
    If(weatherUniforms.wetness.greaterThan(.0001), () => {
      const field = facade ? vec2(1, 0) : weatherSurface(positionWorld);
      const wet = weatherUniforms.wetness.mul(mineral).mul(facade ? float(1) : field.x);
      // Connected low-basin proxy on flat mineral surfaces. No puddles on walls, grass or gravel.
      const pools = facade ? float(0) : puddleMask(positionWorld, wet, up, 1, field);
      const film = facade ? wet.mul(.22) : wet.mul(.8);
      const runoff = facade ? texture(weatherNoise(), vec2(positionWorld.x.add(positionWorld.z).mul(.19), positionWorld.y.mul(.013))).b.mul(wet).mul(.11) : float(0);
      result.assign(vec4(baseColor.mul(float(1).sub(film.mul(facade ? .38 : .6)).sub(runoff)).mul(mix(1, .8, pools)),
        mix(mix(baseRoughness, .27, film), .07, pools)));
    });
    return result;
  })();
  material.colorNode = surface.rgb;
  material.roughnessNode = surface.a;
  return material;
}
