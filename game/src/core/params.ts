// URL parameters. See game/README.md for the contract.
import { OFFICE_EN } from './origin';
import { mobileDevice, parseQuality, type QualityTier } from '../render/quality';
import type { Antialiasing } from '../render/post';
import { DAY_MS, cycleAt, parseClockHour } from './dayCycle';

/** QA camera pose, qa/README.md §2 + qa/qalib/camera.py: yaw = compass bearing, pitch + up, roll + = camera turned
 *  clockwise seen from behind, fov = VERTICAL degrees (three.js camera.fov), H = absolute camera height N2000. */
interface QaCam { E: number; N: number; H: number; yawDeg: number; pitchDeg: number; fovDeg: number; rollDeg: number }
interface Params {
  backend: 'webgpu' | 'webgl';
  /** ?backend= or ?webgl was in the URL (the touch-device default does not override it). */
  backendGiven: boolean;
  qa: QaCam | null;
  /** QA render pass: 'color' (default) or 'id' (flat unlit object ids, qa/README.md §2). */
  pass: 'color' | 'id';
  time: Date;
  timeGiven: boolean;
  /** ?time=HH:MM: a frozen local view of the cycle at that clock hour (null otherwise). */
  hour: number | null;
  /** ?cycle=MIN: real minutes per day cycle. The default 30 is the shared cycle; other values are a local view. */
  cycleMinutes: number;
  spawnGiven: boolean;
  spawn: { E: number; N: number; headingDeg: number };
  hud: boolean;
  test: boolean;
  collisionRadius: number;
  quality: QualityTier;
  aa: Antialiasing;
  mobile: boolean;
}

/** Fixed clear autumn afternoon; never depend on the machine's wall clock for rendering. */
const DEFAULT_TIME = '2026-10-15T14:00:00+03:00';

export function parseParams(search: string, device = { userAgent: '', maxTouchPoints: 0 }): Params {
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
  const hour = timeStr === null ? null : parseClockHour(timeStr);
  const time = hour !== null ? cycleAt(hour).time : new Date(timeStr ?? DEFAULT_TIME);
  if (Number.isNaN(time.getTime())) throw new Error(`bad ?time= (want HH:MM or ISO-8601): ${timeStr}`);
  const cycleMinutes = Number(p.get('cycle') ?? DAY_MS / 60_000);
  if (!(cycleMinutes > 0 && cycleMinutes <= 1440)) throw new Error(`bad ?cycle= (real minutes per day, 0..1440): ${p.get('cycle')}`);
  const sp = nums(p.get('spawn'));
  const colRadius = Number(p.get('colRadius') ?? 200);
  if (!(colRadius > 0)) throw new Error(`bad ?colRadius= (metres > 0): ${p.get('colRadius')}`);
  const backend = (p.get('backend') ?? '').toLowerCase() === 'webgl' || p.has('webgl') ? 'webgl' : 'webgpu';
  const mobile = !qa && mobileDevice(device.userAgent, device.maxTouchPoints);
  const quality = parseQuality(p.get('quality'), mobile ? 'mobile' : 'medium');
  const aa = p.get('aa') ?? (quality === 'mobile' ? 'fxaa' : 'taa');
  if (aa !== 'off' && aa !== 'taa' && aa !== 'fxaa') throw new Error(`bad ?aa= (off|taa|fxaa): ${aa}`);
  return {
    backend,
    backendGiven: p.has('backend') || p.has('webgl'),
    qa,
    pass: passStr,
    time,
    timeGiven: timeStr !== null,
    hour,
    cycleMinutes,
    spawnGiven: sp !== null,
    spawn: { E: sp?.[0] ?? OFFICE_EN.E, N: sp?.[1] ?? OFFICE_EN.N, headingDeg: sp?.[2] ?? 0 },
    hud: p.get('hud') !== '0',
    test: p.has('test'),
    collisionRadius: colRadius,
    quality,
    aa,
    mobile,
  };
}
