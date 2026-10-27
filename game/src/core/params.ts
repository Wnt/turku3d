// URL parameters. See game/README.md for the contract.
import { OFFICE_EN } from './origin';
import { parseQuality, type QualityTier } from '../render/quality';
import type { Antialiasing } from '../render/post';

/** QA camera pose, qa/README.md §2 + qa/qalib/camera.py: yaw = compass bearing, pitch + up, roll + = camera turned
 *  clockwise seen from behind, fov = VERTICAL degrees (three.js camera.fov), H = absolute camera height N2000. */
interface QaCam { E: number; N: number; H: number; yawDeg: number; pitchDeg: number; fovDeg: number; rollDeg: number }
interface Params {
  backend: 'webgpu' | 'webgl';
  tileset: string;
  manifest: string;
  qa: QaCam | null;
  /** QA render pass: 'color' (default) or 'id' (flat unlit object ids, qa/README.md §2). */
  pass: 'color' | 'id';
  time: Date;
  timeGiven: boolean;
  spawnGiven: boolean;
  spawn: { E: number; N: number; headingDeg: number };
  hud: boolean;
  test: boolean;
  collisionRadius: number;
  quality: QualityTier;
  aa: Antialiasing;
}

/** Fixed clear autumn afternoon; never depend on the machine's wall clock for rendering. */
const DEFAULT_TIME = '2026-10-15T14:00:00+03:00';

/** manifest.json next to the tileset. Same-origin paths stay page-relative (as before); an absolute tileset URL on
 *  another host (e.g. `make game-serve-dist` on the sandbox dist port) keeps its origin, otherwise the manifest would be fetched from
 *  the game server. */
function defaultManifest(tileset: string): string {
  // A page-relative tileset (e.g. the Pages build's `../tiles/tileset.json`) keeps its own directory.
  if (!/^([a-z][a-z\d+.-]*:|\/)/i.test(tileset)) return tileset.replace(/[^/]*$/, 'manifest.json');
  const u = new URL('manifest.json', new URL(tileset, 'http://x/'));
  return u.origin === 'http://x' ? u.pathname.replace(/^\//, '') : u.href;
}

export function parseParams(search: string): Params {
  const p = new URLSearchParams(search);
  const nums = (s: string | null) => (s ? s.split(',').map((v) => Number(v.trim())) : null);
  let qa: QaCam | null = null;
  const cam = nums(p.get('cam'));
  if (cam) {
    if (cam.length < 6 || cam.length > 7 || cam.some((v) => !Number.isFinite(v))) throw new Error(`bad ?cam= (want E,N,H,yaw,pitch,fov[,roll]): ${p.get('cam')}`);
    const roll = p.has('roll') ? Number(p.get('roll')) : cam[6] ?? 0;
    if (!Number.isFinite(roll)) throw new Error(`bad ?roll=: ${p.get('roll')}`);
    if (!(cam[5] > 0 && cam[5] < 180)) throw new Error(`bad ?cam= fov (vertical degrees, 0..180): ${cam[5]}`);
    qa = { E: cam[0], N: cam[1], H: cam[2], yawDeg: cam[3], pitchDeg: cam[4], fovDeg: cam[5], rollDeg: roll };
  }
  const passStr = (p.get('pass') ?? 'color').toLowerCase();
  if (passStr !== 'color' && passStr !== 'id') throw new Error(`bad ?pass= (color|id): ${passStr}`);
  const timeStr = p.get('time');
  const time = new Date(timeStr ?? DEFAULT_TIME);
  if (Number.isNaN(time.getTime())) throw new Error(`bad ?time= (want ISO-8601): ${timeStr}`);
  const sp = nums(p.get('spawn'));
  const tileset = p.get('tiles') ?? 'dev-tiles/tileset.json';
  const colRadius = Number(p.get('colRadius') ?? 200);
  if (!(colRadius > 0)) throw new Error(`bad ?colRadius= (metres > 0): ${p.get('colRadius')}`);
  const backend = (p.get('backend') ?? '').toLowerCase() === 'webgl' || p.has('webgl') ? 'webgl' : 'webgpu';
  const aa = p.get('aa') ?? 'taa';
  if (aa !== 'off' && aa !== 'taa' && aa !== 'fxaa') throw new Error(`bad ?aa= (off|taa|fxaa): ${aa}`);
  return {
    backend,
    tileset,
    manifest: p.get('manifest') ?? defaultManifest(tileset),
    qa,
    pass: passStr,
    time,
    timeGiven: timeStr !== null,
    spawnGiven: sp !== null,
    spawn: { E: sp?.[0] ?? OFFICE_EN.E, N: sp?.[1] ?? OFFICE_EN.N, headingDeg: sp?.[2] ?? 0 },
    hud: p.get('hud') !== '0',
    test: p.has('test'),
    collisionRadius: colRadius,
    quality: parseQuality(p.get('quality')),
    aa,
  };
}
