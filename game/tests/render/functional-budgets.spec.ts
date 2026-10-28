import { test, expect } from '@playwright/test';

test('outside-tileset diagnostic completes within 15 seconds on an unloaded host', async ({ page }, info) => {
  const backend = info.project.name.startsWith('webgl') ? 'webgl' : 'webgpu';
  const started = Date.now();
  await page.goto(`/?scene=dev&backend=${backend}&cam=23469668,6714254,5,0,-10,60&qa=1`);
  await page.waitForFunction(() => (window as any).__qaReady || (window as any).__qaError);
  expect(await page.evaluate(() => (window as any).__qaError)).toMatch(/outside the tileset/);
  expect(Date.now() - started).toBeLessThan(15_000);
});
