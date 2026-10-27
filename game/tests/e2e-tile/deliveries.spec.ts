import { cachedTest as test, expect, type Page } from '../e2e/fixtures';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot, tileDist } from '../../../tools/dev/environment.mjs';
import { DELIVERIES } from '../../src/gameplay/content/deliveries';
import { lineText } from '../../src/gameplay/content/dialogue';
import { PIZZERIA } from '../../src/gameplay/content/locations';
import { LEGACY_MISSION_KEY, SAVES_KEY } from '../../src/core/save';
import type { WorldPoint } from '../../src/gameplay/contract';
import { open, untilSim } from '../e2e/waits';

const shots = path.join(reportsRoot, 'gameplay-delivery-pool');
const manifest = JSON.parse(fs.readFileSync(path.join(tileDist, 'manifest.json'), 'utf8'));
const pool: { id: string; address: string }[] = manifest.addresses
  ? JSON.parse(fs.readFileSync(path.join(tileDist, manifest.addresses.url), 'utf8')).addresses : [];

interface Missions { phase: string; delivery: number; delivered: string[]; order: string[]; address: string | null;
  lastAddress: string | null;
  point?: WorldPoint; shop: { id: string } | null; hud: { message: string | null; targets: { id: string; point: WorldPoint }[] } }
const missions = (page: Page) => page.evaluate(() => (window.__game!.state as () => { missions: Missions })().missions);

/** Puts the player on the ground at `point` (the ground ray starts 40 m up). */
async function teleport(page: Page, point: WorldPoint) {
  await page.evaluate(async ({ x, z }) => {
    const game = window.__game as any;
    const id = `c_${Math.floor(x / 62.5)}_${Math.floor(z / 62.5)}`;
    for (let i = 0; i < 600 && !game.physics.loaded.has(id); i++) await new Promise(r => setTimeout(r, 50));
    const hit = await game.physics.raycast({ x, y: 40, z }, { x: 0, y: -1, z: 0 }, 60);
    if (!hit) throw new Error(`no ground at ${x}, ${z}`);
    game.physics.setCharacterEnabled(true, { x, y: hit.point.y + 0.1, z });
  }, point);
  await page.waitForFunction(({ x, z }) => {
    const s = (window.__game!.state as () => { feet: number[]; grounded: boolean })();
    return Math.hypot(s.feet[0] - x, s.feet[2] - z) < 1 && s.grounded;
  }, point);
}
const showsMessage = (page: Page, text: string) => untilSim(page, t =>
  (window.__game!.state as () => { missions: Missions })().missions.hud.message === t, text, 30);

test('after the scripted four, orders come from the address pool without repeats and survive a reload',
  { tag: ['@gameplay', '@walk'] }, async ({ page }, info) => {
    test.skip(info.project.name !== 'webgpu', 'order logic is backend-independent; one backend keeps the check fast');
    test.skip(pool.length < 2, 'No published address pool (synthetic tiles or an older tile build)');
    test.setTimeout(180_000);
    fs.mkdirSync(shots, { recursive: true });
    const shot = async (name: string) => fs.writeFileSync(path.join(shots, `${name}.png`), await page.screenshot());
    const ids = new Set(pool.map(a => a.id));

    const { scale } = await open(page, `/?backend=${info.project.name}&tiles=/tiles/tileset.json`);
    // A game that has just finished the scripted opening: four orders served, a fresh permit, a fixed seed. Written as
    // an unslotted mission save, which the save store adopts as slot 1 (core/save.ts).
    await page.evaluate(([legacy, saves, delivery, last]) => {
      localStorage.removeItem(saves);
      localStorage.setItem(legacy, JSON.stringify({ version: 3, phase: 'at_pizzeria', money: 150, permit: 10,
        plates: false, kidney: false, warning: false, vehicle: 'none', delivery, pizzas: delivery, seed: 2027,
        delivered: [], order: [], lastAddress: last, stop: 0, elapsed: 0, par: 0, firstPar: 0 }));
    }, [LEGACY_MISSION_KEY, SAVES_KEY, DELIVERIES.length, DELIVERIES[DELIVERIES.length - 1].address] as const);
    await page.reload();
    const resume = page.getByRole('option', { name: 'Continue' });
    await expect(resume).toBeEnabled({ timeout: 60_000 * scale });
    await resume.click();
    await page.waitForFunction(() => {
      const s = (window.__game!.state as () => { loading: boolean; tilesSettled: boolean })();
      return !s.loading && s.tilesSettled;
    });

    const served: string[] = [];
    for (const order of [5, 6]) {
      await teleport(page, { x: PIZZERIA.point.x + 1.5, z: PIZZERIA.point.z });
      await page.keyboard.press('KeyE');
      await expect(page.locator('#shop-menu .shop-option.selected')).toContainText(lineText('option_take_order'));
      const quoted = await missions(page);
      expect(quoted.order).toHaveLength(1);
      expect(ids.has(quoted.order[0])).toBe(true);
      expect(served).not.toContain(quoted.order[0]);
      await expect(page.locator('#shop-menu')).toContainText(quoted.address!);
      await expect(page.locator('#shop-menu .shop-status')).toContainText(`Delivered ${DELIVERIES.length + served.length}`);
      await shot(`0${order}-1-quote`);
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => (window.__game!.state as () => { missions: Missions })().missions.phase === 'carrying');
      await untilSim(page, address =>
        (window.__game!.state as () => { missions: Missions })().missions.hud.message?.includes(address) ?? false,
      quoted.address!, 30);
      // Out of the pizzeria's prompt range the hint names the address.
      await teleport(page, { x: PIZZERIA.point.x + 8, z: PIZZERIA.point.z });
      await untilSim(page, address =>
        (window.__game!.state as () => { missions: Missions })().missions.hud.message?.includes(address) ?? false,
      quoted.address!, 30);
      const carrying = await missions(page);
      const target = carrying.hud.targets.find(t => t.id === quoted.order[0])!;
      if (order === 5) {
        await page.keyboard.press('KeyM'); // the big map labels the drop-off with its house number
        await page.waitForFunction(() => (window.__game as any).minimap.snapshot().expanded);
        await shot('05-2-big-map');
        await page.keyboard.press('KeyM');
      }
      await teleport(page, target.point);
      await showsMessage(page, lineText('prompt_hand_over_key'));
      await shot(`0${order}-3-dropoff`);
      await page.keyboard.press('KeyE');
      await page.waitForFunction(() => (window.__game!.state as () => { missions: Missions })().missions.phase === 'delivered');
      const done = await missions(page);
      expect(done.hud.message).toMatch(/./);
      await shot(`0${order}-4-customer`);
      served.push(quoted.order[0]);
      expect(done).toMatchObject({ delivery: order, delivered: served });
    }
    expect(served[1]).not.toBe(served[0]);

    // A reload autosaves on page hide; Continue (labelled with the last pool address) keeps the delivered set.
    const last = pool.find(a => a.id === served[1])!.address;
    await page.reload();
    await expect(resume).toBeEnabled({ timeout: 60_000 * scale });
    await expect(resume).toContainText(last);
    await resume.click();
    await page.waitForFunction(() => !(window.__game!.state as () => { loading: boolean })().loading);
    expect(await missions(page)).toMatchObject({ delivery: DELIVERIES.length + 2, delivered: served, lastAddress: last });
    await page.evaluate(keys => keys.forEach(key => localStorage.removeItem(key)), [SAVES_KEY, LEGACY_MISSION_KEY]);
  });
