import { expect, test } from 'vitest';
import { multiplayerUrl, scoresUrl } from '../../src/net/config';
import { shouldSendPose, type NetPose } from '../../src/net/client';

const pose: NetPose = { t: 0, E: 168, N: -246, H: 3, yaw: 0, vE: 0, vN: 0, vH: 0,
  mode: 'foot', carrying: false };

test('endpoint selection uses localhost for local pages and permits a WebSocket override', () => {
  expect(multiplayerUrl({ hostname: 'localhost', search: '' })).toBe('ws://127.0.0.1:7700/');
  expect(multiplayerUrl({ hostname: 'wnt.github.io', search: '' })).toBe('wss://turku.lab.madekivi.fi/');
  expect(multiplayerUrl({ hostname: 'localhost', search: '?mp=ws%3A%2F%2Flocalhost%3A5235' })).toBe('ws://localhost:5235/');
  expect(() => multiplayerUrl({ hostname: 'localhost', search: '?mp=https%3A%2F%2Fexample.com' })).toThrow();
  expect(scoresUrl('wss://turku.lab.madekivi.fi/')).toBe('https://turku.lab.madekivi.fi');
});

test('pose rate keeps within 20 messages per second and sends a stationary heartbeat', () => {
  expect(shouldSendPose(0, null, -Infinity, pose)).toBe(true);
  expect(shouldSendPose(50, pose, 0, { ...pose, E: 169 })).toBe(false);
  expect(shouldSendPose(55, pose, 0, { ...pose, E: 169 })).toBe(true);
  expect(shouldSendPose(999, pose, 0, pose)).toBe(false);
  expect(shouldSendPose(1000, pose, 0, pose)).toBe(true);
});
