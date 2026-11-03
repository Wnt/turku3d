// Bench lab URL contract (tools/perf/README.md "Bench lab"): isolated, repeatable scenarios over the real engine.
//   ?bench=static  [&tiles=t0_0_0,...][&cam=E,N,H,yaw,pitch,fov | &id=<street camera>][&frames=600][&warm=120]
//   ?bench=install [&tiles=...][&cam=...][&quiet=1500]
//   ?bench=fly     [&tiles=...][&seconds=24][&height=40][&stream=1]
// Every scenario takes &off=<ablations>, &on=<ablations> (re-enables a scenario default), &record=0 (no capture
// folder), &size=WxH and &dpr=N (fixed drawing buffer), and the ordinary &quality= &aa= &time= &traffic= &walkers=.
import { VISUAL_L0 } from '../../core/origin';

export const LAB_MODES = ['static', 'install', 'fly'] as const;
export type LabMode = typeof LAB_MODES[number];

/** Subsystems a run can switch off: `?off=shadows,post`. Each maps to one construction-time switch (qa/lab/ablate.ts). */
export const ABLATIONS = ['shadows', 'reflection', 'traffic', 'walkers', 'vegetation', 'scenery', 'post', 'bloom', 'traa',
  'lights', 'facades', 'props'] as const;
export type Ablation = typeof ABLATIONS[number];

/** Install and fly isolate streaming and LOD: no ambient population unless `on=traffic,walkers`. */
const MODE_OFF: Record<LabMode, Ablation[]> = { static: [], install: ['traffic', 'walkers'], fly: ['traffic', 'walkers'] };

/** The street camera all scenarios use without ?cam= or ?id=: Läntinen Rantakatu, the pilot block (street-cameras.json). */
export const DEFAULT_CAMERA = 'riverside';
export const DEFAULT_CENTER = 't0_0_0';

export interface LabParams {
  mode: LabMode;
  /** L0 tiles the run installs, in install order (nearest to the camera first unless ?tiles= gives an order). */
  tiles: string[];
  tilesGiven: boolean;
  /** Measured frames (static) after `warm` unmeasured ones. */
  frames: number;
  warm: number;
  off: Set<Ablation>;
  /** Save a capture folder (telemetry/capture.ts) holding the frames, trace and the result. */
  record: boolean;
  /** Install: quiet time (ms) after each stage before the next one starts. */
  quietMs: number;
  /** Settle: the scene is unchanged this long (ms) before measuring; gives up after `settleLimitMs`. */
  settleMs: number;
  settleLimitMs: number;
  /** Fly: path duration (s), cruise height above the start camera (m) and whether the tile scheduler streams. */
  seconds: number;
  height: number;
  stream: boolean;
  /** Fixed drawing buffer in CSS pixels and pixel ratio; null keeps the window's and the play-mode ratio. */
  size: [number, number] | null;
  dpr: number | null;
  seed: number;
}

const TILE = /^t0_(-?\d+)_(-?\d+)$/;

export function tileCoords(id: string): [number, number] {
  const m = TILE.exec(id);
  if (!m) throw new Error(`bad tile id (want t0_<i>_<j>): ${id}`);
  return [Number(m[1]), Number(m[2])];
}

/** Centre of an L0 tile in engine x/z. */
export function tileCenter(id: string) {
  const [i, j] = tileCoords(id);
  return { x: (i + 0.5) * VISUAL_L0, z: (j + 0.5) * VISUAL_L0 };
}

/** The 3x3 window around `center`, row by row. */
export function window3x3(center: string): string[] {
  const [i, j] = tileCoords(center);
  const out: string[] = [];
  for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) out.push(`t0_${i + di}_${j + dj}`);
  return out;
}

/** Nearest tile first (ties by id), as the streamer orders a window by distance. */
export function byDistance(tiles: string[], x: number, z: number): string[] {
  const d = (id: string) => {
    const [i, j] = tileCoords(id), x0 = i * VISUAL_L0, z0 = j * VISUAL_L0;
    return Math.hypot(Math.max(x0 - x, 0, x - x0 - VISUAL_L0), Math.max(z0 - z, 0, z - z0 - VISUAL_L0));
  };
  return [...tiles].sort((a, b) => d(a) - d(b) || a.localeCompare(b));
}

export function parseAblations(value: string | null): Ablation[] {
  if (value === null || value.trim() === '') return [];
  const names = value.split(',').map(s => s.trim()).filter(Boolean);
  for (const name of names) if (!(ABLATIONS as readonly string[]).includes(name)) throw new Error(`bad ablation ${name} (${ABLATIONS.join(', ')})`);
  return [...new Set(names)] as Ablation[];
}

const int = (p: URLSearchParams, key: string, fallback: number, min: number, max: number) => {
  if (!p.has(key)) return fallback;
  const v = Number(p.get(key));
  if (!Number.isInteger(v) || v < min || v > max) throw new Error(`bad ?${key}= (integer ${min}..${max}): ${p.get(key)}`);
  return v;
};
const num = (p: URLSearchParams, key: string, fallback: number, min: number, max: number) => {
  if (!p.has(key)) return fallback;
  const v = Number(p.get(key));
  if (!Number.isFinite(v) || v < min || v > max) throw new Error(`bad ?${key}= (${min}..${max}): ${p.get(key)}`);
  return v;
};

export function isLabMode(mode: string | null): mode is LabMode {
  return mode !== null && (LAB_MODES as readonly string[]).includes(mode);
}

/** The lab parameters of `?bench=static|install|fly`, or null for any other page. */
export function parseLab(search: string | URLSearchParams): LabParams | null {
  const p = typeof search === 'string' ? new URLSearchParams(search) : search;
  const mode = p.get('bench');
  if (!isLabMode(mode)) return null;
  const tilesStr = p.get('tiles');
  const tiles = tilesStr ? [...new Set(tilesStr.split(',').map(s => s.trim()).filter(Boolean))] : window3x3(DEFAULT_CENTER);
  for (const id of tiles) tileCoords(id);
  if (!tiles.length || tiles.length > 49) throw new Error('bad ?tiles= (1..49 L0 tile ids)');
  const on = new Set(parseAblations(p.get('on')));
  const off = new Set<Ablation>([...MODE_OFF[mode].filter(a => !on.has(a)), ...parseAblations(p.get('off'))]);
  let size: [number, number] | null = null;
  if (p.has('size')) {
    const m = /^(\d{2,5})x(\d{2,5})$/.exec(p.get('size') ?? '');
    if (!m) throw new Error(`bad ?size= (WxH CSS pixels): ${p.get('size')}`);
    size = [Number(m[1]), Number(m[2])];
  }
  return {
    mode, tiles, tilesGiven: !!tilesStr,
    frames: int(p, 'frames', 600, 30, 20000), warm: int(p, 'warm', 120, 0, 5000),
    off, record: p.get('record') !== '0',
    quietMs: int(p, 'quiet', 1500, 0, 60000), settleMs: int(p, 'settle', 5000, 0, 120000),
    settleLimitMs: int(p, 'settleLimit', 180000, 1000, 900000),
    seconds: num(p, 'seconds', 24, 5, 300), height: num(p, 'height', 40, 0, 2000), stream: p.get('stream') === '1',
    size, dpr: p.has('dpr') ? num(p, 'dpr', 1, 0.25, 4) : null,
    seed: int(p, 'seed', 20261102, 0, 2 ** 31 - 1),
  };
}

/** One line naming a run's configuration, for tables and logs. */
export function labLabel(lab: Pick<LabParams, 'mode' | 'off'>, quality: string) {
  const off = [...lab.off].sort().join(',');
  return `${lab.mode}/${quality}${off ? ` off=${off}` : ''}`;
}
