import { test } from 'vitest';
import { course, cruise, idle, pitch, ramp, travel } from './trophy4400-course';
test('kicker trace', () => {
  const speed = 60 / 3.6;
  const c = course({ x: -60, build: w => ramp(w, 0, 1.5, 6) });
  c.run(12, s => cruise(s, speed), s => s.pos.x > -20 && Math.abs(s.forwardSpeed - speed) < 0.3);
  const rows: string[] = [];
  c.run(2.2, s => s.wheels.some(w => w.contact) ? cruise(s, speed) : idle(), (s, t) => {
    if (s.pos.x > -9 && rows.length < 200 && (rows.length % 2 === 0 || true)) rows.push([t.toFixed(2), s.pos.x.toFixed(1), s.pos.y.toFixed(2), pitch(s.rot).toFixed(1), c.vehicle.body.angvel().z.toFixed(2), s.wheels.map(w => w.contact ? 1 : 0).join(''), [0, 2].map(i => travel(s, i).toFixed(2)).join(',')].join(' '));
  });
  console.log(rows.filter((_, i) => i % 3 === 0).join('\n'));
});
