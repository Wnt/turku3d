import { isShareHost, sameOriginServices } from '../core/localOnly';
import { CITY, isHome, type CityProfile } from '../core/city';

/** Local and share-host drafts stay same-origin; Pages uses the build-time serving endpoint. The page always asks for
 *  `/coarse/`: another city's mount on the same origin receives those requests (core/city.ts installCityFetch). Pages
 *  serves Turku only. */
export function draftsUrl(page: Pick<Location, 'hostname'> = location, city: CityProfile = CITY): string | null {
  if (sameOriginServices(page.hostname)) return '/coarse/';
  if (page.hostname !== 'wnt.github.io' || !isHome(city)) return null;
  const url = new URL(import.meta.env.VITE_DRAFT_URL ?? 'https://turku.lab.madekivi.fi/coarse/');
  if (url.protocol !== 'https:') throw new Error('draft endpoint must use HTTPS');
  return url.href.endsWith('/') ? url.href : `${url.href}/`;
}

/** Multiplayer endpoint is selected at runtime so the same static build works locally and on Pages. A share host's
 *  own server proxies the socket (and the scores) to its multiplayer server, never to the published one. */
export function multiplayerUrl(page: Pick<Location, 'hostname' | 'search'> & Partial<Pick<Location, 'host' | 'protocol'>> = location,
  city: CityProfile = CITY): string {
  const params = new URLSearchParams(page.search), override = params.get('mp');
  // Each city is its own room: the socket path names it (server/http.js), `/` is Turku. `?room=<id>` on the page
  // joins a private room of that city instead (drive tests and tools: never the players' room).
  const privateRoom = params.get('room');
  const room = (base: string) => {
    const url = new URL(city.room, base);
    if (privateRoom && /^[a-z0-9][a-z0-9-]{2,39}$/.test(privateRoom)) url.searchParams.set('room', privateRoom);
    return url.href;
  };
  if (override) {
    const url = new URL(override);
    if (url.protocol !== 'ws:' && url.protocol !== 'wss:') throw new Error('mp must be a WebSocket URL');
    return isHome(city) && !privateRoom ? url.href : room(url.href);
  }
  if (page.hostname === 'localhost') return room('ws://127.0.0.1:7700/');
  if (isShareHost(page.hostname)) return room(`${page.protocol === 'http:' ? 'ws:' : 'wss:'}//${page.host ?? page.hostname}/`);
  return room('wss://turku.lab.madekivi.fi/');
}

export function scoresUrl(wsUrl: string): string {
  const url = new URL(wsUrl);
  url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:';
  return url.origin;
}
