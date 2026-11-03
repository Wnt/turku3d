// Share-host origin logic (core/localOnly.ts isShareHost): a VITE_SHARE_HOSTS page uses its own origin's tiles,
// point cloud and multiplayer socket like a local page, but none of the local-only features, like Pages.
import { afterEach, expect, test, vi } from 'vitest';
import { devToolsEnabled, isLocalHost, isShareHost, localFeature, sameOriginServices } from '../../src/core/localOnly';
import { draftsUrl, multiplayerUrl, scoresUrl } from '../../src/net/config';
import { pointCloudSources } from '../../src/render/pointCloud/sources';
import { telemetryEnabled } from '../../src/telemetry/guard';

const share = { hostname: 'city.lab.madekivi.fi', host: 'city.lab.madekivi.fi', protocol: 'https:', search: '' };
afterEach(() => vi.unstubAllEnvs());

test('a share host is exact, case-insensitive and only set at build time', () => {
  expect(isShareHost(share.hostname)).toBe(false);
  vi.stubEnv('VITE_SHARE_HOSTS', ' City.Lab.Madekivi.fi , other.example ');
  expect(isShareHost('city.lab.madekivi.fi')).toBe(true);
  expect(isShareHost('CITY.lab.madekivi.fi')).toBe(true);
  expect(isShareHost('other.example')).toBe(true);
  expect(isShareHost('evil.city.lab.madekivi.fi')).toBe(false);
  expect(isShareHost('lab.madekivi.fi')).toBe(false);
  expect(isShareHost('')).toBe(false);
  expect(isLocalHost('city.lab.madekivi.fi')).toBe(false);
  expect(sameOriginServices('city.lab.madekivi.fi')).toBe(true);
  expect(sameOriginServices('localhost')).toBe(true);
  expect(sameOriginServices('wnt.github.io')).toBe(false);
});

test('a share host streams same-origin tiles and point cloud and never the published services', () => {
  vi.stubEnv('VITE_SHARE_HOSTS', 'city.lab.madekivi.fi');
  expect(draftsUrl(share)).toBe('/coarse/');
  expect(draftsUrl({ hostname: 'wnt.github.io' })).toBe('https://turku.lab.madekivi.fi/coarse/');
  expect(draftsUrl({ hostname: 'example.com' })).toBeNull();
  expect(pointCloudSources(share.hostname)).toEqual(['./pointcloud/']);
  expect(pointCloudSources('localhost')).toEqual(['./pointcloud/', 'http://192.168.1.114:7960/']);
  expect(pointCloudSources('wnt.github.io')).toEqual(['./pointcloud/']);
});

test('a share host plays multiplayer on its own origin, scores included', () => {
  vi.stubEnv('VITE_SHARE_HOSTS', 'city.lab.madekivi.fi,127.0.0.2');
  expect(multiplayerUrl(share)).toBe('wss://city.lab.madekivi.fi/');
  expect(scoresUrl(multiplayerUrl(share))).toBe('https://city.lab.madekivi.fi');
  expect(multiplayerUrl({ hostname: '127.0.0.2', host: '127.0.0.2:8080', protocol: 'http:', search: '' })).toBe('ws://127.0.0.2:8080/');
  expect(multiplayerUrl({ ...share, search: '?mp=ws%3A%2F%2Flocalhost%3A5235' })).toBe('ws://localhost:5235/');
  expect(multiplayerUrl({ hostname: 'localhost', search: '' })).toBe('ws://127.0.0.1:7700/');
  expect(multiplayerUrl({ hostname: 'wnt.github.io', search: '' })).toBe('wss://turku.lab.madekivi.fi/');
});

test('a share host runs no local-only features unless asked, like Pages; ?dev=1 opens the dev menu', () => {
  vi.stubEnv('VITE_SHARE_HOSTS', 'city.lab.madekivi.fi');
  for (const param of ['telemetry', 'editor', 'capture', 'dev']) expect(localFeature(share, param)).toBe(false);
  expect(telemetryEnabled(share)).toBe(false);
  expect(devToolsEnabled(share)).toBe(false);
  expect(devToolsEnabled({ ...share, search: '?dev=1' })).toBe(true);
  expect(localFeature({ hostname: 'localhost', search: '' }, 'telemetry')).toBe(true);
});
