import { isLocalHost } from '../core/localOnly';

/** Local drafts stay same-origin; Pages uses the build-time serving endpoint. */
export function draftsUrl(page: Pick<Location, 'hostname'> = location): string | null {
  if (isLocalHost(page.hostname)) return '/coarse/';
  if (page.hostname !== 'wnt.github.io') return null;
  const url = new URL(import.meta.env.VITE_DRAFT_URL ?? 'https://turku.lab.madekivi.fi/coarse/');
  if (url.protocol !== 'https:') throw new Error('draft endpoint must use HTTPS');
  return url.href.endsWith('/') ? url.href : `${url.href}/`;
}

/** Multiplayer endpoint is selected at runtime so the same static build works locally and on Pages. */
export function multiplayerUrl(page: Pick<Location, 'hostname' | 'search'> = location): string {
  const override = new URLSearchParams(page.search).get('mp');
  if (override) {
    const url = new URL(override);
    if (url.protocol !== 'ws:' && url.protocol !== 'wss:') throw new Error('mp must be a WebSocket URL');
    return url.href;
  }
  return page.hostname === 'localhost' ? 'ws://127.0.0.1:7700/' : 'wss://turku.lab.madekivi.fi/';
}

export function scoresUrl(wsUrl: string): string {
  const url = new URL(wsUrl);
  url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:';
  return url.origin;
}
