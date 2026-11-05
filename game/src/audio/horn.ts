import type { V3 } from '../core/origin';
import type { VehicleKind } from '../physics/constants';
import { POSITION_EPSILON, glide } from './automation';

/** Original synthesised diaphragm horns; no sample or external asset. */
export const HORNS = {
  car: { tones: [350, 440], gain: 0.28 },
  truck: { tones: [220, 275], gain: 0.42 },
  scooter: { tones: [880], gain: 0.16 },
} as const;

export function createHorn(ctx: BaseAudioContext, bus: AudioNode, kind: VehicleKind, position: V3) {
  const spec = HORNS[kind], gain = ctx.createGain(), panner = ctx.createPanner();
  panner.panningModel = 'HRTF'; panner.distanceModel = 'inverse';
  panner.refDistance = 5; panner.maxDistance = 250; panner.rolloffFactor = 0.8;
  const move = (p: V3) => {
    const t = ctx.currentTime;
    glide(panner.positionX, p.x, t, 0.025, POSITION_EPSILON); glide(panner.positionY, p.y, t, 0.025, POSITION_EPSILON);
    glide(panner.positionZ, p.z, t, 0.025, POSITION_EPSILON);
  };
  move(position);
  gain.gain.setValueAtTime(0, ctx.currentTime);
  gain.gain.linearRampToValueAtTime(spec.gain, ctx.currentTime + 0.025);
  gain.connect(panner); panner.connect(bus);
  // A diaphragm's strong fundamental and diminishing harmonics, with no DC component.
  const real = new Float32Array(9), imag = new Float32Array([0, 1, .28, .17, .08, .05, .03, .02, .01]);
  const wave = ctx.createPeriodicWave(real, imag);
  const sources = spec.tones.map(frequency => {
    const osc = ctx.createOscillator(), level = ctx.createGain();
    osc.setPeriodicWave(wave); osc.frequency.value = frequency;
    level.gain.value = 1 / spec.tones.length;
    osc.connect(level); level.connect(gain); osc.start();
    osc.onended = () => { osc.disconnect(); level.disconnect(); };
    return osc;
  });
  let stopped = false;
  return { move, stop(at = ctx.currentTime) {
    if (stopped) return; stopped = true;
    gain.gain.cancelAndHoldAtTime(at);
    gain.gain.linearRampToValueAtTime(0, at + .06);
    sources.forEach(source => source.stop(at + .065));
    sources[0].addEventListener('ended', () => { gain.disconnect(); panner.disconnect(); });
  } };
}
