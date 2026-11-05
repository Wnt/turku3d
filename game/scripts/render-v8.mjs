// Offline renders of the trophy truck's synthesized V8 (src/audio/engine) to 16-bit mono WAV files, for listening
// and spectral comparison. Nothing here reads recorded audio.
//   node scripts/render-v8.mjs OUT_DIR [scenario...]        built-in scenarios (default: all)
//   node scripts/render-v8.mjs OUT_DIR --track FILE.json      keyframes {name, seed?, cockpit?, keys: [{t, rpm, load,
//                                                             throttle, limiter?, shifting?}]} at a 60 Hz control rate
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runnerImport } from 'vite';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const load = async file => (await runnerImport(path.join(root, file), { configFile: false, root })).module;
const { createV8, render } = await load('src/audio/engine/v8core.ts');
const { Drivetrain } = await load('src/audio/engine/drivetrain.ts');
const { ChassisSynth, ChassisTracker } = await load('src/audio/engine/chassis.ts');

const SR = 48000, BLOCK = 128, CONTROL = 1 / 60;

function wav(file, samples) {
  const data = Buffer.alloc(samples.length * 2);
  let peak = 0;
  for (const s of samples) peak = Math.max(peak, Math.abs(s));
  const scale = peak > 0.98 ? 0.98 / peak : 1;
  samples.forEach((s, i) => data.writeInt16LE(Math.round(Math.max(-1, Math.min(1, s * scale)) * 32767), i * 2));
  const head = Buffer.alloc(44);
  head.write('RIFF', 0); head.writeUInt32LE(36 + data.length, 4); head.write('WAVEfmt ', 8);
  head.writeUInt32LE(16, 16); head.writeUInt16LE(1, 20); head.writeUInt16LE(1, 22); head.writeUInt32LE(SR, 24);
  head.writeUInt32LE(SR * 2, 28); head.writeUInt16LE(2, 32); head.writeUInt16LE(16, 34); head.write('data', 36);
  head.writeUInt32LE(data.length, 40);
  writeFileSync(file, Buffer.concat([head, data]));
  return { file, seconds: samples.length / SR, peak, normalised: scale !== 1 };
}

/** Runs `control(t)` at 60 Hz, as the main thread posts parameters, and renders 128-sample blocks between posts. */
function run(seconds, control, { seed = 19, cockpit = false, warm = true } = {}) {
  const voice = createV8(seed, SR, { running: warm });
  const chassis = new ChassisSynth(seed + 1, SR);
  const out = new Float32Array(Math.round(seconds * SR)), block = new Float32Array(BLOCK), extra = new Float32Array(BLOCK);
  let params = null, frame = null, nextControl = 0, t = 0;
  for (let i = 0; i < out.length; i += BLOCK) {
    if (t >= nextControl) { ({ params, frame } = control(t)); nextControl += CONTROL; if (frame) chassis.frame(frame); }
    params.cockpit = cockpit;
    render(voice, params, block, SR);
    extra.fill(0); chassis.render(extra);
    for (let k = 0; k < BLOCK && i + k < out.length; k++) out[i + k] = block[k] + extra[k];
    t += BLOCK / SR;
  }
  return out;
}

const steady = (rpm, load, throttle) => () => ({ params: { rpm, load, throttle, limiter: false, shifting: false, running: true, cockpit: false, partials: 32 } });

/** A straight full-throttle run on dirt with a lift, a jump (airborne flare) and a landing, through the drivetrain. */
function driveScenario({ limiter = false } = {}) {
  const dt = CONTROL, drive = new Drivetrain(), tracker = new ChassisTracker();
  let v = 0, y = 0, vy = 0, air = false, rotation = 0;
  const wheels = Array.from({ length: 4 }, () => ({ pos: { x: 0, y: 0, z: 0 }, steer: 0, rotation: 0, contact: true, suspension: 0.705,
    tyre: { point: { x: 0, y: -0.5, z: 0 }, normal: { x: 0, y: 1, z: 0 }, material: 9, lateral: 0, longitudinal: 0 } }));
  return t => {
    const throttle = t < 9 ? 1 : t < 11 ? 0 : t < 16 ? 1 : 0.15;
    if (t > 13 && t < 13 + dt * 1.5 && !air) { air = true; vy = 4.5; }
    if (air) { vy -= 9.81 * dt; y += vy * dt; if (y <= 0) { y = 0; air = false; vy = 0; } }
    const force = throttle * Math.min(7000 * 4, 746000 / Math.max(v, 1)) * (air ? 0 : 1);
    v = Math.max(0, v + (force - 0.5 * 1.2 * 1.4 * v * v - 600) / 2350 * dt);
    if (limiter && t > 6) v = Math.max(v, 45);
    const wheelSpeed = air ? v + throttle * 18 * Math.min(1, (t - 13) * 2) : v * (1 + 0.05 * throttle);
    rotation += wheelSpeed / 0.533 * dt;
    for (const w of wheels) {
      w.contact = !air; w.rotation = rotation;
      w.suspension = air ? Math.min(1.0, w.suspension + 0.6 * dt * 10) : Math.max(0.705, w.suspension - (y === 0 && w.suspension > 0.75 ? 4.5 * dt : 0));
    }
    const d = drive.step({ dt, throttle, brake: 0, forwardSpeed: v, drivenWheelSpeed: wheelSpeed, contacts: air ? 0 : 4,
      reverse: false, rearSteerMoving: false });
    const frame = tracker.step({ dt, wheels, speed: v, rearSlew: 0, heave: y });
    return { params: { rpm: d.rpm, load: d.load, throttle: d.throttle, limiter: d.limiter, shifting: d.shifting, running: true, cockpit: false, partials: 32 },
      frame, drive: d };
  };
}

function keyed(keys) {
  return t => {
    let i = 1;
    while (i < keys.length - 1 && keys[i].t < t) i++;
    const a = keys[i - 1], b = keys[i], f = Math.max(0, Math.min(1, (t - a.t) / Math.max(1e-6, b.t - a.t)));
    const lerp = k => (a[k] ?? 0) + ((b[k] ?? 0) - (a[k] ?? 0)) * f;
    return { params: { rpm: lerp('rpm'), load: lerp('load'), throttle: lerp('throttle'), limiter: !!a.limiter, shifting: !!a.shifting,
      running: true, cockpit: false, partials: 32 } };
  };
}

const SCENARIOS = {
  // 33 s at idle, matched to the verified V2 shop idle; the rear steer cycles at 12-15 s and 24-26 s, as in the clip.
  idle: () => run(33, (() => { const d = new Drivetrain(); return t => ({ params: { ...d.step({ dt: CONTROL, throttle: 0, brake: 0, forwardSpeed: 0,
    drivenWheelSpeed: 0, contacts: 4, reverse: false, rearSteerMoving: (t > 12 && t < 15) || (t > 24 && t < 26) }), running: true, cockpit: false, partials: 32 } }); })()),
  // The verified on-load reference: V1's film pull at ≈ 6,060 rpm (ref/REFERENCE.md §5).
  pull6060: () => run(4, steady(6060, 1, 1)),
  // V2's snow driving: part load at 1,440-1,690 rpm.
  part1600: () => run(4, steady(1600, 0.4, 0.4)),
  sweep: () => run(8, t => steady(1000 + 6000 * Math.min(1, t / 8), 1, 1)()),
  drive: () => run(18, driveScenario()),
  start: () => run(4, steady(920, 0, 0), { warm: false }),
  limiter: () => run(3, () => ({ params: { rpm: 7000, load: 1, throttle: 1, limiter: true, shifting: false, running: true, cockpit: false, partials: 32 } })),
  cockpit6060: () => run(4, steady(6060, 1, 1), { cockpit: true }),
};

const [outDir, ...rest] = process.argv.slice(2);
if (!outDir) { console.error('usage: render-v8.mjs OUT_DIR [scenario...] | --track FILE'); process.exit(2); }
mkdirSync(outDir, { recursive: true });
const results = [];
if (rest[0] === '--track') {
  for (const file of rest.slice(1)) {
    const track = JSON.parse(readFileSync(file, 'utf8'));
    const seconds = track.keys.at(-1).t;
    results.push(wav(path.join(outDir, `${track.name}.wav`), run(seconds, keyed(track.keys), { seed: track.seed ?? 19, cockpit: !!track.cockpit })));
  }
} else {
  for (const name of rest.length ? rest : Object.keys(SCENARIOS)) results.push(wav(path.join(outDir, `synth_${name}.wav`), SCENARIOS[name]()));
}
for (const r of results) console.log(`${r.file} ${r.seconds.toFixed(1)} s peak ${r.peak.toFixed(3)}${r.normalised ? ' (normalised)' : ''}`);
