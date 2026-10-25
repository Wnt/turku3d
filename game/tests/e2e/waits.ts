import type { Page } from '@playwright/test';

// Observe browser frames, including while physics is paused. No wall-clock sleeps.
export async function frames(page: Page, count = 3) {
  await page.evaluate(n => new Promise<void>(resolve => {
    const step = () => { if (--n <= 0) resolve(); else requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }), count);
}
export async function settled(page: Page) {
  await page.waitForFunction(() => {
    const s = (window as any).__game.state();
    return s.grounded && Math.hypot(...s.vel) < 0.05 &&
      Math.abs(s.visualFeet[1] - s.feet[1]) < 0.04 && s.contacts.every((c: any) => c.lift < 0.05);
  }, null, { timeout: 60_000 });
}
