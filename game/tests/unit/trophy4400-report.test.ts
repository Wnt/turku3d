import { test } from 'vitest';
import { MAT } from '../../src/shared/col/materials';
import * as S from './trophy4400-scenarios';
test('report', () => {
  const k = S.kickerTrace();
  const out = {
    D1: S.staticSettle(), D2: S.lockAngles(), D3: S.passiveTaper(), D3h: S.lockoutHysteresis(),
    D4: { auto: S.turningCircle('auto'), lock: S.turningCircle('lock') }, D5: S.crab(),
    D6drop: S.flatDrop(), D6kicker: { ...k, ys: undefined }, D13: S.cameraHeave(k.ys),
    D9: { road: S.acceleration(MAT.road), dirt: S.acceleration(MAT.soil) },
    D8: S.articulation(),
    slalom: [S.slalom(8), S.slalom(16)], D10exit: S.powerOnExit(), D11: S.autopilotCircle(),
  };
  console.log(JSON.stringify(out, (k, v) => typeof v === 'number' ? +v.toFixed(3) : v));
  const brief = (rows: ReturnType<typeof S.circle>) => rows.map(r => [r.v, +r.ay.toFixed(2), +r.roll.toFixed(1), r.contacts].join('/')).join(' ');
  console.log(JSON.stringify({ D7road: brief(S.circle(MAT.road)), D7dirt: brief(S.circle(MAT.soil)) }));
  const sp = (m: number) => { const r = S.skidpad(m); return { max: r.maxLateral, rows: r.rows.map(x => [x.v, +x.ay.toFixed(2), +x.error.toFixed(1), +x.frontSlip.toFixed(0), +x.rearSlip.toFixed(0)].join('/')).join(' ') }; };
  console.log(JSON.stringify({ D10road: sp(MAT.road), D10dirt: sp(MAT.soil) }));
});
