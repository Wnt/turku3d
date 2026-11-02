// Wet mineral surfaces share uniforms: transitions and tile streaming never rebuild their shader graphs.
import * as THREE from 'three/webgpu';
import { Fn, If, float, materialColor, materialRoughness, mix, normalWorldGeometry, positionWorld, smoothstep, texture, uniform, vec2, vec4 } from 'three/tsl';

export const weatherUniforms = { wetness: uniform(0), rain: uniform(0), seconds: uniform(0) };
let detail: THREE.DataTexture | undefined;
function weatherNoise() {
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
export function wetSurface(material: THREE.MeshStandardNodeMaterial, facade = false, mineral: THREE.Node<'float'> = float(1)) {
  if (material.userData.weatherWetness) return material;
  material.userData.weatherWetness = true;
  const baseColor = (material.colorNode ?? materialColor.rgb) as THREE.Node<'vec3'>;
  const baseRoughness = (material.roughnessNode ?? materialRoughness) as THREE.Node<'float'>;
  const surface = Fn(() => {
    const result = vec4(baseColor, baseRoughness).toVar();
    const up = smoothstep(.75, .98, normalWorldGeometry.y).toVar();
    // No wet-noise sampling in dry weather. The branch is uniform across the whole draw.
    If(weatherUniforms.wetness.greaterThan(.0001), () => {
      const noise = texture(weatherNoise(), positionWorld.xz.mul(.026)).r;
      const grain = texture(weatherNoise(), positionWorld.xz.mul(.18)).g;
      const wet = weatherUniforms.wetness.mul(mineral);
      // Connected low-basin proxy on flat mineral surfaces. No puddles on walls, grass or gravel.
      const pools = smoothstep(float(.83).sub(wet.mul(.5)), float(.98).sub(wet.mul(.5)), noise.mul(.7).add(grain.mul(.3)))
        .mul(up).mul(smoothstep(.35, .95, wet));
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
