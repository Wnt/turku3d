import { isShareHost, sameOriginServices } from '../core/localOnly';

/** Local and share-host drafts stay same-origin; Pages uses the build-time serving endpoint. */
export function draftsUrl(page: Pick<Location, 'hostname'> = location): string | null {
  if (sameOriginServices(page.hostname)) return '/coarse/';
  if (page.hostname !== 'wnt.github.io') return null;
  const url = new URL(import.meta.env.VITE_DRAFT_URL ?? 'https://turku.lab.madekivi.fi/coarse/');
  if (url.protocol !== 'https:') throw new Error('draft endpoint must use HTTPS');
  return url.href.endsWith('/') ? url.href : `${url.href}/`;
}

/** Multiplayer endpoint is selected at runtime so the same static build works locally and on Pages. A share host's
 *  own server proxies the socket (and the scores) to its multiplayer server, never to the published one. */
export function multiplayerUrl(page: Pick<Location, 'hostname' | 'search'> & Partial<Pick<Location, 'host' | 'protocol'>> = location): string {
  const override = new URLSearchParams(page.search).get('mp');
  if (override) {
    const url = new URL(override);
    if (url.protocol !== 'ws:' && url.protocol !== 'wss:') throw new Error('mp must be a WebSocket URL');
    return url.href;
  }
  if (page.hostname === 'localhost') return 'ws://127.0.0.1:7700/';
  if (isShareHost(page.hostname)) return `${page.protocol === 'http:' ? 'ws:' : 'wss:'}//${page.host ?? page.hostname}/`;
  return 'wss://turku.lab.madekivi.fi/';
}

export function scoresUrl(wsUrl: string): string {
  const url = new URL(wsUrl);
  url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:';
  return url.origin;
}
