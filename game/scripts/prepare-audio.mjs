// Original Turku3D procedural sound bank. Source and generated audio dedicated to CC0-1.0.
// No samples, voices, field recordings or external assets are used.
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const output = fileURLToPath(new URL('../public/assets/audio/', import.meta.url));
mkdirSync(output, { recursive: true });
const rate = 24000, tau = Math.PI * 2;
let seed = 1818, low = 0;
const noise = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 2147483648 - 1; };
const brown = () => (low = low * 0.97 + noise() * 0.03);
const sine = (hz, t) => Math.sin(tau * hz * t);
const pulse = (t, decay) => (1 - Math.exp(-t * 1200)) * Math.exp(-t * decay);
const definitions = {
  'step-asphalt': [0.23, t => pulse(t, 22) * (0.5 * noise() + sine(110, t))],
  'step-stone': [0.25, t => pulse(t, 27) * (noise() * 0.4 + sine(200, t) + 0.3 * sine(870, t))],
  'step-grass': [0.32, t => pulse(t, 17) * (brown() * 5 + noise() * 0.3)],
  'step-gravel': [0.34, t => pulse(t, 14) * noise() * (0.3 + Math.abs(sine(37, t)))],
  'step-wood': [0.3, t => pulse(t, 20) * (sine(180, t) + 0.4 * sine(410, t) + 0.1 * noise())],
  engine: [4, t => (sine(50, t) + 0.35 * sine(100, t) + 0.22 * sine(150, t) + brown()) * (0.85 + 0.15 * sine(25, t))],
  roll: [4, t => brown() * 5 * (0.8 + 0.2 * sine(3, t))],
  screech: [4, t => sine(1400, t + 0.00015 * sine(33, t)) * 0.5 + noise() * 0.2],
  curb: [0.4, t => pulse(t, 15) * (sine(62, t) + brown() * 4)],
  impact: [0.7, t => pulse(t, 12) * (sine(48, t) + noise() * 0.7 + sine(287, t) * 0.25)],
  'door-open': [0.45, t => pulse(t, 17) * noise() + 0.2 * Math.sin(Math.PI * t / 0.45) * sine(370 - t * 180, t)],
  'door-close': [0.45, t => pulse(t, 20) * (sine(82, t) + noise() * 0.4)],
  city: [8, t => brown() * 3 + 0.07 * sine(80, t) * (1 + sine(0.5, t))],
  river: [8, t => (brown() * 4 + noise() * 0.12) * (0.7 + 0.2 * sine(0.5, t) + 0.1 * sine(1.25, t))],
  gulls: [8, t => {
    const call = t % 4;
    if (call > 0.8) return 0;
    const envelope = Math.sin(Math.PI * call / 0.8) ** 2;
    return envelope * (sine(1250 - call * 500, call) + 0.25 * sine(2400 - call * 700, call));
  }],
  traffic: [8, t => (brown() * 3 + sine(72, t) * 0.12) * (0.6 + 0.4 * sine(0.125, t))],
  wind: [8, t => brown() * 4 * (0.7 + 0.3 * sine(0.25, t))],
  connect: [0.2, t => pulse(t, 18) * sine(t < 0.09 ? 660 : 990, t)],
  // Last, so the seeded noise of the earlier sounds is unchanged. A 50 cc two-stroke: a nasal buzz at a higher pitch than the car engine, with a fast exhaust flutter.
  scooter: [4, t => (Math.sign(sine(90, t)) * 0.35 + sine(180, t) * 0.5 + 0.3 * sine(270, t) + noise() * 0.12) * (0.75 + 0.25 * sine(45, t))],
  // A 3-litre V6 at idle: 45 Hz firing pulses (three per crank turn at 900 rpm) with an open-exhaust rasp, the
  // half-order burble of the two banks, and a deep body under it.
  truck: [4, t => {
    const phase = (t * 45) % 1, fire = Math.exp(-phase * 7);
    return fire * (0.8 + 0.45 * noise()) + 0.55 * sine(45, t) + 0.4 * sine(90, t) + 0.22 * sine(135, t)
      + 0.3 * sine(22.5, t) + brown() * 2 * (0.85 + 0.15 * sine(7.5, t));
  }],
};
const manifest = {};
for (const [name, [seconds, sample]] of Object.entries(definitions)) {
  low = 0;
  let data = Float32Array.from({ length: Math.round(seconds * rate) }, (_, i) => sample(i / rate));
  if (seconds >= 4) {
    // Overlap the loop boundary and trim the overlapped head. The final sample leads into the first.
    const fade = rate / 10;
    for (let i = 0; i < fade; i++) {
      const a = i / fade;
      data[data.length - fade + i] = data[data.length - fade + i] * (1 - a) + data[i] * a;
    }
    data = data.slice(fade);
  } else {
    for (let i = 0; i < data.length; i++) data[i] *= Math.min(1, (data.length - i) / 240);
  }
  const peak = data.reduce((a, b) => Math.max(a, Math.abs(b)), 0);
  const pcm = Buffer.alloc(data.length * 4);
  data.forEach((v, i) => pcm.writeFloatLE(v / (peak || 1) * 0.7, i * 4));
  const file = path.join(output, `${name}.ogg`);
  const result = spawnSync('ffmpeg', ['-v', 'error', '-y', '-f', 'f32le', '-ar', String(rate), '-ac', '1', '-i', 'pipe:0',
    '-c:a', 'libopus', '-b:a', '32k', '-application', 'audio', '-map_metadata', '-1', file], { input: pcm });
  if (result.status !== 0) throw new Error(String(result.stderr || result.error));
  const bytes = readFileSync(file);
  manifest[`${name}.ogg`] = { bytes: bytes.length, channels: 1, seconds: data.length / rate,
    sha256: createHash('sha256').update(bytes).digest('hex'), license: 'CC0-1.0' };
}
writeFileSync(path.join(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(`Generated ${Object.keys(manifest).length} mono Opus sounds (${Object.values(manifest).reduce((n, v) => n + v.bytes, 0)} bytes).`);
