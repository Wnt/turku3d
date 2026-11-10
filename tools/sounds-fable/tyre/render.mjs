// Offline renders of the stick-slip tyre synthesiser: node render.mjs OUT_DIR
// Writes 48 kHz stereo float WAVs; make_board.py encodes them. Loops are rendered long and cut by loops.py.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const TyreSquealCore = new Function(readFileSync(path.join(here, 'core.js'), 'utf8') + '\nreturn TyreSquealCore;')();
const out = process.argv[2];
if (!out) throw new Error('usage: node render.mjs OUT_DIR');
mkdirSync(out, { recursive: true });
const SR = 48000, BLOCK = 128;

function wav(file, left, right) {
  const n = left.length, buf = Buffer.alloc(44 + n * 8);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 8, 4); buf.write('WAVEfmt ', 8); buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(3, 20); buf.writeUInt16LE(2, 22); buf.writeUInt32LE(SR, 24); buf.writeUInt32LE(SR * 8, 28);
  buf.writeUInt16LE(8, 32); buf.writeUInt16LE(32, 34); buf.write('data', 36); buf.writeUInt32LE(n * 8, 40);
  for (let i = 0; i < n; i++) { buf.writeFloatLE(left[i], 44 + i * 8); buf.writeFloatLE(right[i], 48 + i * 8); }
  writeFileSync(path.join(out, file), buf);
}

/** `control(t)` returns the parameter object at time t (seconds). */
function render(file, seconds, seed, control) {
  const core = new TyreSquealCore(SR, seed), n = Math.round(seconds * SR);
  const left = new Float32Array(n), right = new Float32Array(n), l = new Float32Array(BLOCK), r = new Float32Array(BLOCK);
  for (let at = 0; at < n; at += BLOCK) {
    core.set(control(at / SR));
    core.process(l, r);
    left.set(l.subarray(0, Math.min(BLOCK, n - at)), at); right.set(r.subarray(0, Math.min(BLOCK, n - at)), at);
  }
  wav(file, left, right);
  let peak = 0; for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(left[i]), Math.abs(right[i]));
  console.log(file, seconds + ' s', 'peak', peak.toFixed(3));
}

const lerp = (a, b, x) => a + (b - a) * Math.max(0, Math.min(1, x));
const ASPHALT = { squeal: 1, rough: 0.35, grit: 0 };
const base = { ...ASPHALT, load: 1, speed: 0, roll: 0 };

// Chirps: the tyre breaks away for a moment (gear change, kerb, quick steering input) and grips again.
render('chirp-short.wav', 1.2, 11, t => ({ ...base, slide: t > 0.1 && t < 0.28 ? 3.2 * Math.sin(Math.PI * (t - 0.1) / 0.18) : 0, load: 1.15 }));
render('chirp-double.wav', 1.6, 12, t => ({ ...base,
  slide: (t > 0.1 && t < 0.3 ? 4.5 * Math.sin(Math.PI * (t - 0.1) / 0.2) : 0) + (t > 0.48 && t < 0.62 ? 2.4 * Math.sin(Math.PI * (t - 0.48) / 0.14) : 0), load: 1.3 }));
// Emergency stop from 60 km/h with locked wheels: sliding speed falls with the car, pitch and level follow.
render('skid-stop.wav', 4.2, 13, t => { const v = Math.max(0, 16.7 - 7.5 * Math.max(0, t - 0.3)); return { ...base, slide: t < 0.3 ? 0 : v, load: 1.35, speed: v, roll: 0.4 }; });
// Sustained drift at three intensities (rendered long for seamless loops): light understeer scrub, steady slide, full drift.
for (const [name, slide, load, seed] of [['drift-light', 2.0, 1.0, 21], ['drift-mid', 4.2, 1.25, 22], ['drift-hard', 8.5, 1.6, 23]])
  render(`${name}-long.wav`, 14, seed, t => ({ ...base, slide: slide * (1 + 0.1 * Math.sin(t * 2.1) + 0.06 * Math.sin(t * 5.3 + 1)), load: load * (1 + 0.08 * Math.sin(t * 1.3 + 2)), speed: 14, roll: 0.25 }));
// Sweep: sliding speed 0 -> 10 -> 0 m/s at light, normal and heavy load (three passes).
render('sweep-slip-load.wav', 18, 31, t => { const pass = Math.floor(t / 6), u = (t % 6) / 6; return { ...base, slide: 10 * Math.sin(Math.PI * u) ** 1.5, load: [0.6, 1, 1.7][Math.min(2, pass)], speed: 15, roll: 0.2 }; });
// Surfaces at the same slide: dry asphalt, smooth painted concrete (car park), wet asphalt, gravel.
render('surfaces.wav', 16, 41, t => { const pass = Math.min(3, Math.floor(t / 4)), u = (t % 4) / 4;
  const surface = [{ squeal: 1, rough: 0.35, grit: 0 }, { squeal: 1.25, rough: 0.08, grit: 0 }, { squeal: 0.12, rough: 0.3, grit: 0 }, { squeal: 0, rough: 0.9, grit: 1 }][pass];
  return { ...base, ...surface, slide: 5 * Math.sin(Math.PI * u) ** 0.7, load: 1.2, speed: 12, roll: 0.3 }; });
// A drift through a bend, as the game would drive it: entry chirp, build-up, hold with corrections, catch.
render('drift-corner.wav', 7, 51, t => {
  const slide = t < 0.4 ? 0 : t < 1.2 ? lerp(0, 6.5, (t - 0.4) / 0.8) : t < 4.6 ? 6.5 + 1.6 * Math.sin((t - 1.2) * 3.1) + 0.8 * Math.sin((t - 1.2) * 7.7) : lerp(6, 0, (t - 4.6) / 0.7);
  return { ...base, slide, load: 1.25 + 0.25 * Math.sin(t * 2.2), speed: lerp(22, 14, t / 7), roll: 0.35 };
});
