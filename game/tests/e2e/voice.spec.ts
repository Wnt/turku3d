import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';
import { expect, test } from './fixtures';

// Injects a fake MissionHudSource whose message is a voiced dialogue line and checks that the clip starts
// on the voice bus with ambience ducked, then that a second line cuts the first.
test('a voiced HUD message starts its clip, ducks ambience and a new line cuts it', { tag: ['@audio', '@voice'] }, async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`/?backend=${info.project.name}`);
  await page.waitForFunction(() => (window as any).__game?.ready);
  const manifest = await (await page.request.get('assets/voice/manifest.json')).json();
  const [first, second] = manifest.lines as { id: string; text: string; seconds: number }[];
  await page.getByRole('button', { name: 'Start playing' }).click(); // pointerdown unlocks the audio context
  await expect.poll(() => page.evaluate(() => (window as any).__game.audio.snapshot().state), { timeout: 30_000 }).toBe('running');
  await page.evaluate(text => {
    const game = (window as any).__game;
    const listeners: ((state: unknown) => void)[] = [];
    const state = { targets: [], activeTargetId: null, money: 0, timerSeconds: null, message: text };
    game.fakeHud = { set(message: string) { state.message = message; for (const l of listeners) l({ ...state }); } };
    game.voice.attach({ subscribe(l: (state: unknown) => void) { listeners.push(l); l({ ...state }); return () => {}; } });
    const hud = document.createElement('div');
    hud.id = 'voice-check-message';
    hud.style.cssText = 'position:fixed;left:50%;bottom:120px;transform:translateX(-50%);padding:8px 14px;background:#000a;color:#fff;font:16px sans-serif;z-index:9';
    hud.textContent = text;
    document.body.append(hud);
  }, first.text);
  await expect.poll(() => page.evaluate(() => (window as any).__game.voice.snapshot()), { timeout: 10_000 })
    .toMatchObject({ playing: first.id, played: [first.id], errors: [] });
  const audio = await page.evaluate(() => (window as any).__game.audio.snapshot());
  expect(audio).toMatchObject({ state: 'running', ducked: true, errors: [] });
  await page.screenshot({ path: path.join(reportsRoot, 'audio-voice-tts', `voice-${info.project.name}.png`) });
  await page.evaluate(text => (window as any).__game.fakeHud.set(text), second.text);
  await expect.poll(() => page.evaluate(() => (window as any).__game.voice.snapshot().played)).toEqual([first.id, second.id]);
  await expect.poll(() => page.evaluate(() => (window as any).__game.voice.snapshot().playing),
    { timeout: (second.seconds + 5) * 1000 }).toBeNull();
  expect(await page.evaluate(() => (window as any).__game.audio.snapshot().ducked)).toBe(false);
  expect(errors).toEqual([]);
});
