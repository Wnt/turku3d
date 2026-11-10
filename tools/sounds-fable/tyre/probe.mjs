// Diagnostic: tone-only and scrub-only levels and the tone's pitch against sliding speed and load.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const Core = new Function(readFileSync(path.join(here, 'core.js'), 'utf8') + '\nreturn TyreSquealCore;')();
const SR = 48000;
function run(params, seconds = 1.5) {
  const core = new Core(SR, 7), n = Math.round(seconds * SR), l = new Float32Array(128), r = new Float32Array(128), out = new Float32Array(n);
  core.set(params); core.cur = { ...core.target };
  for (let at = 0; at < n; at += 128) { core.process(l, r); for (let i = 0; i < 128 && at + i < n; i++) out[at + i] = (l[i] + r[i]) / 2; }
  const x = out.subarray(n >> 1);
  let sum = 0; for (const v of x) sum += v * v;
  // Pitch by autocorrelation between 250 and 2500 Hz.
  let best = 0, lag = 0;
  for (let k = Math.floor(SR / 2500); k < SR / 250; k++) { let c = 0; for (let i = 0; i + k < 8192; i++) c += x[i] * x[i + k]; if (c > best) { best = c; lag = k; } }
  let e = 0; for (let i = 0; i < 8192; i++) e += x[i] * x[i];
  return { db: 10 * Math.log10(sum / x.length + 1e-20), hz: lag ? SR / lag : 0, periodicity: best / (e + 1e-20) };
}
console.log('slide load | tone dB  pitch  periodicity | scrub dB');
for (const load of [0.6, 1, 1.7]) for (const slide of [0.5, 1, 2, 3, 5, 8, 12, 16]) {
  const tone = run({ slide, load, squeal: 1, rough: 0.35, scrub: 0, roll: 0 }), scrub = run({ slide, load, squeal: 1, rough: 0.35, tone: 0, roll: 0 });
  console.log(String(slide).padStart(5), String(load).padStart(4), '|', tone.db.toFixed(1).padStart(6), tone.hz.toFixed(0).padStart(6), tone.periodicity.toFixed(2).padStart(6), '|', scrub.db.toFixed(1).padStart(6));
}
