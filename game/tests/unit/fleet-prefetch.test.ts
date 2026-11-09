import './fleet-assets.fixture';
import { expect, test, vi } from 'vitest';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { loadAuthoredFleet, loadAuthoredFleetParts, prefetchAuthoredFleet } from '../../src/actors/fleet/authored';
import { PLAYER_MODEL } from '../../src/actors/fleet/catalog';

test('a prefetched fleet asset is fetched and baked once; the session load takes copies of it', async () => {
  const fetches = vi.spyOn(GLTFLoader.prototype, 'loadAsync');
  const before = fetches.mock.calls.length;
  await prefetchAuthoredFleet(PLAYER_MODEL, 1);
  const parts = await loadAuthoredFleetParts(PLAYER_MODEL, 1), again = await loadAuthoredFleetParts(PLAYER_MODEL, 1);
  const body = await loadAuthoredFleet(PLAYER_MODEL, 1);
  expect(fetches.mock.calls.length - before).toBe(1);
  expect(parts && again && body).toBeTruthy();
  expect(parts!.body).not.toBe(again!.body); // each load owns its geometry
  expect(parts!.body.getAttribute('position').count).toBe(again!.body.getAttribute('position').count);
});
