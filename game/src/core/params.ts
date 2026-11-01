// URL parameters. See game/README.md for the contract.
import { OFFICE_EN } from './origin';
import { mobileDevice, parseQuality, type QualityTier } from '../render/quality';
import type { Antialiasing } from '../render/post';
import { benchPose, type BenchPose } from '../qa/bench';
import { DAY_MS, cycleAt, parseClockHour } from './dayCycle';

/** QA camera pose, qa/README.md §2 + qa/qalib/camera.py: yaw = compass bearing, pitch + up, roll + = camera turned
 *  clockwise seen from behind, fov = VERTICAL degrees (three.js camera.fov), H = absolute camera height N2000. */
interface QaCam { E: number; N: number; H: number; yawDeg: number; pitchDeg: number; fovDeg: number; rollDeg: number }
interface Params {
  backend: 'webgpu' | 'webgl';
  /** ?backend= or ?webgl was in the URL (the touch-device default does not override it). */
  backendGiven: boolean;
  qa: QaCam | null;
  /** ?bench=camera&id=<street camera>[&at=HH:MM]: the fixed-camera bench probe (qa/bench.ts); implies its QA pose. */
  bench: BenchPose | null;
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
  /** ?lights=0..1 fixes the street-light night level (?lights alone is 1); null follows the day cycle. */
  lights: number | null;
  /** ?traffic=N overrides the quality tier's moving-car cap (0 turns ambient traffic off); null keeps the tier's. */
  traffic: number | null;
  /** `?walkers=N`: ambient walker cap override (0 disables them); null keeps the quality tier's cap. */
  walkers: number | null;
  /** `?motion=1` with `?cam=`: the QA camera keeps the play-mode water clock and reflection cadence (video capture). */
  motion: boolean;
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
  let bench: BenchPose | null = null;
  if (p.has('bench')) {
    if (p.get('bench') !== 'camera') throw new Error(`bad ?bench= (camera): ${p.get('bench')}`);
    if (cam) throw new Error('?bench=camera takes its pose from ?id=, not ?cam=');
    bench = benchPose(p.get('id') ?? '', p.get('at'));
    qa = bench.qa;
  }
  const passStr = (p.get('pass') ?? 'color').toLowerCase();
  if (passStr !== 'color' && passStr !== 'id') throw new Error(`bad ?pass= (color|id): ${passStr}`);
  const timeStr = p.get('time');
  const hour = timeStr === null ? null : parseClockHour(timeStr);
  const time = bench && timeStr === null ? bench.time : hour !== null ? cycleAt(hour).time : new Date(timeStr ?? DEFAULT_TIME);
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
  const lightsStr = p.get('lights');
  const lights = lightsStr === null ? null : lightsStr === '' ? 1 : Number(lightsStr);
  if (lights !== null && !(lights >= 0 && lights <= 1)) throw new Error(`bad ?lights= (night level 0..1): ${lightsStr}`);
  const trafficStr = p.get('traffic');
  const traffic = trafficStr === null ? null : Number(trafficStr);
  if (traffic !== null && !(Number.isInteger(traffic) && traffic >= 0 && traffic <= 200)) throw new Error(`bad ?traffic= (moving-car cap 0..200): ${trafficStr}`);
  const walkers = p.has('walkers') ? Number(p.get('walkers')) : null;
  if (walkers !== null && !(Number.isInteger(walkers) && walkers >= 0 && walkers <= 200)) throw new Error(`bad ?walkers= (0..200): ${p.get('walkers')}`);
  return {
    backend,
    backendGiven: p.has('backend') || p.has('webgl'),
    qa,
    bench,
    pass: passStr,
    time,
    timeGiven: timeStr !== null || bench !== null,
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
    lights,
    traffic,
    walkers,
    motion: !!qa && p.get('motion') === '1',
  };
}
