import { expect, test } from 'vitest';
import { Scene } from 'three/webgpu';
import { prerunnerCourse } from '../../src/dev/prerunnerCourse';

test('physical racer course flies off the kicker, lands and continues over kerbs without overturning', () => {
  const course = prerunnerCourse(new Scene());
  try {
    for (let i = 0; i < 30 * 60; i++) course.step(1 / 60);
    const result = course.snapshot();
    console.log(JSON.stringify({ longestAir: result.longestAir, landings: result.landings, maxSpeed: result.maxSpeed, maxTilt: result.maxTilt, final: result.samples.at(-1) }));
    expect(result.longestAir).toBeGreaterThan(.3);
    expect(result.landings).toBeGreaterThanOrEqual(1);
    expect(result.samples.at(-1)!.x).toBeGreaterThan(58);
    expect(result.maxTilt).toBeLessThan(65);
    expect(result.samples.at(-1)!.contact).toBeGreaterThanOrEqual(3);
  } finally { course.dispose(); }
});
