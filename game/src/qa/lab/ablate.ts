// Bench lab ablations (`?off=`): each switches one subsystem off at construction, through the same settings the game
// already has (quality tier fields, AA mode, population caps), so a run measures the game minus that subsystem.
//   shadows     renderer.shadowMap off, the sun casts nothing (no CSM cascades)
//   reflection  quality reflectionScale 0: river reaches shade from the sky, no reflector pass
//   bloom       quality bloomStrength 0: no bloom node
//   traa        AA off (no TRAA resolve or history; ?aa=fxaa still picks FXAA)
//   post        no post pipeline: one direct renderer.render (graphics preparation then warms no view passes)
//   lights      no street lights, lamp pools or light sprites; no dynamic point-light slots
//   vegetation  no nature library: no trees, grass or bushes
//   props       no street furniture LOD
//   facades     no generated facade modules (render/facadeModules.ts)
//   scenery     no distant city (scenery radius 0, cells not updated)
//   bundles     no static render bundles: every resident tile draw is projected and encoded each frame
//               (render/staticBundles.ts)
//   traffic / walkers  population caps 0 (install and fly default to both off; ?on= re-enables)
import type * as THREE from 'three/webgpu';
import { QUALITY, type QualityTier } from '../../render/quality';
import { facadeModuleSwitch } from '../../render/facadeModules';
import type { Ablation } from './params';

type Mutable<T> = { -readonly [K in keyof T]: T[K] extends object ? Mutable<T[K]> : T[K] };

/** Quality-tier and parameter switches; call before the renderer, environment, post and water are built. */
export function ablateSettings(off: ReadonlySet<Ablation>, tier: QualityTier,
  params: { aa: string; traffic: number | null; walkers: number | null }, aaGiven: boolean) {
  const q = QUALITY[tier] as unknown as Mutable<typeof QUALITY[QualityTier]>;
  if (off.has('reflection')) q.reflectionScale = 0;
  if (off.has('bloom')) q.bloomStrength = 0;
  if (off.has('lights')) q.lights.point = 0;
  if (off.has('traa') && (!aaGiven || params.aa === 'taa')) params.aa = 'off';
  if (off.has('traffic')) params.traffic = 0;
  if (off.has('walkers')) params.walkers = 0;
  facadeModuleSwitch.on = !off.has('facades');
}

/** Scene-level switches once the renderer and environment exist. */
export function ablateScene(off: ReadonlySet<Ablation>, renderer: THREE.WebGPURenderer, sun: THREE.DirectionalLight) {
  if (off.has('shadows')) { renderer.shadowMap.enabled = false; sun.castShadow = false; }
}
