import { isolatedTest as test, expect } from './fixtures';
import { lineText } from '../../src/gameplay/content/dialogue';
import { reportsRoot } from '../../../tools/dev/environment.mjs';
import fs from 'node:fs';
import path from 'node:path';

test('New game plays the three-line intro before handing control to the player', { tag: ['@gameplay', '@input', '@synthetic'] }, async ({ page }, info) => {
  await page.goto(`/?backend=${info.project.name}`);
  await page.getByRole('option', { name: 'New game' }).click();
  const dialog = page.locator('#intro-dialog');
  await expect(dialog).toBeVisible();
  const reports = path.join(reportsRoot, 'gameplay-intro-dialog');
  fs.mkdirSync(reports, { recursive: true });
  await expect(dialog.locator('.intro-line')).toHaveText(lineText('intro_1'));
  fs.writeFileSync(path.join(reports, `${info.project.name}-intro.png`), await page.screenshot());
  await dialog.getByRole('button', { name: 'Continue' }).click();
  await expect(dialog.locator('.intro-line')).toHaveText(lineText('intro_2'));
  await dialog.getByRole('button', { name: 'Continue' }).click();
  await expect(dialog.locator('.intro-line')).toHaveText(lineText('intro_3'));
  await dialog.getByRole('button', { name: 'Continue' }).click();
  await expect(dialog).toBeHidden();
  await page.waitForFunction(() => {
    const state = (window.__game!.state as () => { loading: boolean; startScreen: boolean; grounded: boolean })();
    return !state.loading && !state.startScreen && state.grounded;
  });
  await expect(page.locator('#hud')).toBeVisible();
  fs.writeFileSync(path.join(reports, `${info.project.name}-playing.png`), await page.screenshot());
});
