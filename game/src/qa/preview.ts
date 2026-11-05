// ?preview=1: look at a city while it is being generated. The QA renderer (no player, physics, traffic or walkers, no
// start-set wait) with a free-fly camera and an overlay of the tile service's progress. Works for every city
// (?city=...). Keys: WASD/arrows move, Q/E or Space/C down/up, Shift x4, mouse drag looks, wheel changes speed.
import { draftsUrl } from '../net/config';
import { engineToEN } from '../core/origin';

export interface PreviewPose { E: number; N: number; H: number; yawDeg: number; pitchDeg: number; fovDeg: number }
type Move = (pose: { E: number; N: number; H: number; yaw: number; pitch: number; fov: number }) => void;

/** Horizontal step (E, N) for forward/right input at a compass heading (deg, clockwise from north). */
export function stepEN(headingDeg: number, forward: number, right: number, metres: number): [number, number] {
  const h = (headingDeg * Math.PI) / 180;
  return [(Math.sin(h) * forward + Math.cos(h) * right) * metres, (Math.cos(h) * forward - Math.sin(h) * right) * metres];
}

interface ServiceStatus { state?: string; building?: string[]; upgrading?: string[]; queue?: string[]; queued?: number; built?: number;
  failed?: Record<string, unknown>; pending?: Record<string, unknown> }

export function statusText(s: ServiceStatus | null, loaded: string[]): string {
  if (!s) return `service: no answer\ndrawn: ${loaded.length} tiles`;
  const list = (a?: string[]) => (a && a.length ? a.slice(0, 8).join(' ') + (a.length > 8 ? ` +${a.length - 8}` : '') : '-');
  return [`service: ${s.state ?? '?'}`, `building: ${list(s.building)}`, `upgrading: ${list(s.upgrading)}`,
    `queued (${s.queued ?? s.queue?.length ?? 0}): ${list(s.queue)}`,
    `failed: ${Object.keys(s.failed ?? {}).length}  waiting for data: ${Object.keys(s.pending ?? {}).length}`,
    `drawn (${loaded.length}): ${list(loaded)}`].join('\n');
}

export function startPreview(q: PreviewPose, move: Move, loaded: () => string[]) {
  const keys = new Set<string>();
  let speed = 40; // m/s
  addEventListener('keydown', e => { keys.add(e.code); });
  addEventListener('keyup', e => { keys.delete(e.code); });
  addEventListener('blur', () => keys.clear());
  addEventListener('wheel', e => { speed = Math.min(400, Math.max(5, speed * (e.deltaY > 0 ? 0.8 : 1.25))); }, { passive: true });
  let drag: [number, number] | null = null;
  addEventListener('pointerdown', e => { drag = [e.clientX, e.clientY]; });
  addEventListener('pointerup', () => { drag = null; });
  addEventListener('pointermove', e => {
    if (!drag) return;
    q.yawDeg = (q.yawDeg + (e.clientX - drag[0]) * 0.2 + 360) % 360;
    q.pitchDeg = Math.max(-89.9, Math.min(60, q.pitchDeg - (e.clientY - drag[1]) * 0.2));
    drag = [e.clientX, e.clientY];
    apply();
  });
  const apply = () => move({ E: q.E, N: q.N, H: q.H, yaw: q.yawDeg, pitch: q.pitchDeg, fov: q.fovDeg });
  const overlay = document.createElement('pre');
  overlay.id = 'preview-status';
  overlay.style.cssText = 'position:fixed;left:8px;bottom:8px;margin:0;padding:6px 8px;background:rgba(0,0,0,.6);color:#fff;'
    + 'font:11px/1.35 monospace;z-index:20;pointer-events:none;max-width:70vw;white-space:pre-wrap';
  document.body.appendChild(overlay);
  let status: ServiceStatus | null = null;
  const root = draftsUrl(location) ?? '/coarse/';
  const poll = async () => {
    try { status = await (await fetch(`${root}status`, { cache: 'no-store' })).json() as ServiceStatus; } catch { status = null; }
  };
  void poll();
  setInterval(() => void poll(), 3000);
  let last = performance.now();
  const tick = (now: number) => {
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    const k = (...codes: string[]) => codes.some(c => keys.has(c)) ? 1 : 0;
    const forward = k('KeyW', 'ArrowUp') - k('KeyS', 'ArrowDown'), right = k('KeyD', 'ArrowRight') - k('KeyA', 'ArrowLeft');
    const up = k('KeyE', 'Space') - k('KeyQ', 'KeyC');
    if (forward || right || up) {
      const m = speed * dt * (keys.has('ShiftLeft') || keys.has('ShiftRight') ? 4 : 1);
      const [dE, dN] = stepEN(q.yawDeg, forward, right, m);
      q.E += dE; q.N += dN; q.H = Math.max(-5, q.H + up * m);
      apply();
    }
    const en = engineToEN(0, 0, 0);
    overlay.textContent = `PREVIEW  E ${q.E.toFixed(0)} N ${q.N.toFixed(0)} H ${q.H.toFixed(0)}  speed ${speed.toFixed(0)} m/s`
      + `  (O ${en.E} ${en.N})\n${statusText(status, loaded())}`;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
