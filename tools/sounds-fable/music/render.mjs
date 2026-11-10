// Offline renders of the procedural music engine: node render.mjs OUT_DIR [--stems]
// Writes 48 kHz stereo float WAVs (one per song; with --stems also one per instrument group of each song).
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const { MusicCore, SONGS, PRESETS } = new Function(readFileSync(path.join(here, 'engine.js'), 'utf8') + '\nreturn { MusicCore, SONGS, PRESETS };')();
const out = process.argv[2];
if (!out) throw new Error('usage: node render.mjs OUT_DIR [--stems]');
mkdirSync(out, { recursive: true });
const SR = 48000, BLOCK = 64;

function wav(file, left, right) {
  const n = left.length, buf = Buffer.alloc(44 + n * 8);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 8, 4); buf.write('WAVEfmt ', 8); buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(3, 20); buf.writeUInt16LE(2, 22); buf.writeUInt32LE(SR, 24); buf.writeUInt32LE(SR * 8, 28);
  buf.writeUInt16LE(8, 32); buf.writeUInt16LE(32, 34); buf.write('data', 36); buf.writeUInt32LE(n * 8, 40);
  for (let i = 0; i < n; i++) { buf.writeFloatLE(left[i], 44 + i * 8); buf.writeFloatLE(right[i], 48 + i * 8); }
  writeFileSync(path.join(out, file), buf);
}

/** `intensity(t)` drives the adaptive layers; the render ends 4 s after the form does. */
function render(file, songId, intensity = () => 0, solo = null) {
  const core = new MusicCore(SR, songId, 20261110);
  core.solo = solo;
  const left = [], right = [], l = new Float32Array(BLOCK), r = new Float32Array(BLOCK);
  let frames = 0;
  while (core.tail < 4 * SR && frames < 300 * SR) {
    core.intensity = intensity(frames / SR);
    core.process(l, r);
    left.push(Float32Array.from(l)); right.push(Float32Array.from(r)); frames += BLOCK;
  }
  const L = new Float32Array(frames), R = new Float32Array(frames);
  left.forEach((b, i) => L.set(b, i * BLOCK)); right.forEach((b, i) => R.set(b, i * BLOCK));
  // Fade the reverb tail out over the last 1.5 s.
  for (let i = 0; i < 1.5 * SR; i++) { const g = i / (1.5 * SR); L[frames - 1 - i] *= g; R[frames - 1 - i] *= g; }
  wav(file, L, R);
  let peak = 0, sum = 0; for (let i = 0; i < frames; i++) { peak = Math.max(peak, Math.abs(L[i]), Math.abs(R[i])); sum += L[i] * L[i] + R[i] * R[i]; }
  console.log(file.padEnd(34), (frames / SR).toFixed(1).padStart(6) + ' s', 'peak', peak.toFixed(3), 'rms', (10 * Math.log10(sum / frames / 2 + 1e-20)).toFixed(1), 'dB');
}

const stems = process.argv.includes('--stems');
const STEMS = { 'aura-neon': ['drums', 'bass', 'pad', 'pluck', 'lead'], 'ring-road': ['drums', 'bass', 'pad', 'brass', 'seq', 'bell'],
  'river-dub': ['drums', 'subBass', 'softPad', 'stab', 'bell'], 'harbour-glass': ['subBass', 'softPad', 'epiano', 'bell'] };
// Delivery rush: cruise for 12 s, then the deadline runs out over 50 s and stays at full for the last bars.
const rush = t => Math.max(0, Math.min(1, (t - 12) / 50));
for (const id of Object.keys(SONGS)) {
  const intensity = id === 'ring-road' ? rush : () => 0;
  render(`music-own-${id}.wav`, id, intensity);
  if (stems) for (const part of STEMS[id]) render(`stem-${id}-${part}.wav`, id, intensity, part);
}
