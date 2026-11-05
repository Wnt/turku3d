import { afterEach, expect, test, vi } from 'vitest';
import { ControlsHelp } from '../../src/ui/controlsHelp';
import { DEFAULT_BINDINGS, promptLabel, type Action, type PromptDevice } from '../../src/input/bindings';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function setup() {
  const storage = { getItem: () => null, setItem: vi.fn() };
  vi.stubGlobal('localStorage', storage);
  vi.stubGlobal('location', { hostname: 'localhost', search: '' });
  const element = { hidden: false, textContent: '' };
  const input = { source: 'keyboard', device: 'keyboard' as PromptDevice, promptRevision: 0, activity: new Set<string>(),
    prompt: vi.fn((action: Action) => promptLabel(action, DEFAULT_BINDINGS, input.device)) };
  return { help: new ControlsHelp(element as unknown as HTMLElement), element, input: input as any, storage };
}

test('hidden and unchanged help skips formatting while device and mode changes update its text', () => {
  const { help, input, element } = setup();
  help.render(input, 'foot', false, false, false, false, 0);
  expect(input.prompt).not.toHaveBeenCalled();
  help.render(input, 'foot', false, false, false, true, 10);
  expect(element.textContent).toContain('mouse look');
  input.prompt.mockClear();
  help.render(input, 'foot', false, false, false, true, 20);
  expect(input.prompt).not.toHaveBeenCalled();
  input.source = 'gamepad'; input.device = 'xbox'; input.promptRevision++;
  help.render(input, 'foot', false, false, false, true, 30);
  expect(element.textContent).toContain('Ⓡ look');
  expect(element.textContent).toContain('Ⓐ sprint');
  input.device = 'playstation'; input.promptRevision++;
  help.render(input, 'foot', false, false, false, true, 35);
  expect(element.textContent, 'a DualSense redraws the help at once').toContain('R stick look');
  expect(element.textContent).toContain('✕ sprint · □ jump · △ enter vehicle');
  expect(element.textContent).not.toMatch(/[ⒶⒷⓧⓎⓁⓇ]/);
  help.render(input, 'car', false, false, false, true, 40);
  expect(element.textContent).toContain('throttle');
  help.render(input, 'car', false, true, false, true, 50);
  expect(element.textContent).toContain('Resume');
  help.render(input, 'car', false, false, true, true, 60);
  expect(element.textContent).toContain('return to solid ground');
  help.render(input, 'foot', true, false, false, true, 70);
  expect(element.textContent).toContain('Flying');
});

test('help only serializes changed saved seconds or presses, excludes paused time, and stops when learned', () => {
  const { help, input, element, storage } = setup();
  const stringify = vi.spyOn(JSON, 'stringify');
  for (const now of [0, 16, 500]) help.update(input, 'foot', false, true, now);
  expect(stringify).not.toHaveBeenCalled();
  help.update(input, 'foot', false, true, 1000);
  expect(stringify).toHaveBeenCalledOnce();
  input.activity.add('sprint');
  help.update(input, 'foot', false, true, 1016);
  expect(stringify).toHaveBeenCalledTimes(2);
  expect(JSON.parse(storage.setItem.mock.calls.at(-1)![1]).foot.counts.sprint).toBe(1);
  input.activity.clear();
  help.update(input, 'foot', false, false, 2000);
  help.update(input, 'foot', false, true, 5000);
  expect(stringify).toHaveBeenCalledTimes(2);
  help.update(input, 'foot', false, true, 63984);
  expect(help.hidden('foot', false)).toBe(true);
  expect(help.hidden('car', false)).toBe(false);
  expect(stringify).toHaveBeenCalledTimes(3);
  help.update(input, 'foot', false, true, 64000);
  help.render(input, 'foot', false, false, false, true, 64000);
  expect(stringify).toHaveBeenCalledTimes(3);
  expect(element.hidden).toBe(true);
  expect(input.prompt).not.toHaveBeenCalled();
});

test('unchanged help periodically picks up in-place binding changes', () => {
  const { help, input, element } = setup();
  help.render(input, 'foot', false, false, false, true, 0);
  input.prompt.mockImplementation(() => 'new binding');
  help.render(input, 'foot', false, false, false, true, 1000);
  expect(element.textContent).toContain('new binding');
});
