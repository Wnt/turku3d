// The active city (docs/cities.md): one page plays one city, chosen by ?city=NAME (default Turku). Mirror of the
// generator's city profiles (pipeline/turku3d/cities): the world origin O, the centre spawn, the projection of the
// city's grid and where its tile service is mounted. Changing city is a page load (gameplay/travel.ts), so every
// module reads its constants once at load and nothing re-projects at runtime.

export type CityName = 'turku' | 'espoo' | 'amsterdam' | 'rotterdam';

/** Inverse projection of the city grid: Finnish GK zones (ETRS89 TM, k0 = 1, false easting zone * 1e6 + 500 000) or
 *  the Dutch RD New (EPSG:28992). */
export type CityProjection = { kind: 'gk'; meridian: number } | { kind: 'rd' };

export interface CityProfile {
  name: CityName;
  title: string;
  /** Horizontal CRS of E/N, shown in the HUD. */
  crs: string;
  /** Height datum of H. */
  heightDatum: string;
  projection: CityProjection;
  /** World origin O (NW corner of a 500 m block): engine x = E - E0, z = -(N - N0). */
  origin: { E: number; N: number };
  /** Grid bearing (deg) of true north at O: engine/grid azimuth = true azimuth + this (minus pyproj's
   *  meridian_convergence at O). */
  trueNorthGridBearingDeg: number;
  /** Centre spawn: where a new game and an arrival through a portal start. */
  spawn: { E: number; N: number; headingDeg: number; label: string };
  /** WGS84 latitude/longitude of the spawn (the sun's position; pyproj from the spawn E/N). */
  latLon: { lat: number; lon: number };
  /** Same-origin path of the city's coarse tile service; manifests inside it name `/coarse/...` paths. */
  tiles: string;
  /** Multiplayer socket path: each city has its own room (server/http.js). */
  room: string;
}

export const HOME_CITY: CityName = 'turku';

export const CITIES: Record<CityName, CityProfile> = {
  turku: {
    name: 'turku', title: 'Turku', crs: 'EPSG:3877', heightDatum: 'N2000', projection: { kind: 'gk', meridian: 23 },
    origin: { E: 23459500, N: 6704500 },
    // EPSG:3877 meridian convergence at O (pyproj: 0.640 deg; 0.56 deg 7 km south-east).
    trueNorthGridBearingDeg: 0.64,
    // Office, Läntinen Rantakatu 15 (ARCHITECTURE.md D3).
    spawn: { E: 23459668, N: 6704254, headingDeg: 0, label: 'Läntinen Rantakatu 15' },
    latLon: { lat: 60.448, lon: 22.267 }, tiles: '/coarse/', room: '/',
  },
  espoo: {
    name: 'espoo', title: 'Espoo', crs: 'EPSG:3879', heightDatum: 'N2000', projection: { kind: 'gk', meridian: 25 },
    origin: { E: 25489000, N: 6674000 }, trueNorthGridBearingDeg: 0.172,
    spawn: { E: 25489150, N: 6673640, headingDeg: 0, label: 'Tapiontori, Tapiola' },
    latLon: { lat: 60.17548, lon: 24.80452 }, tiles: '/cities/espoo/coarse/', room: '/city/espoo',
  },
  amsterdam: {
    name: 'amsterdam', title: 'Amsterdam', crs: 'EPSG:28992', heightDatum: 'NAP', projection: { kind: 'rd' },
    origin: { E: 121000, N: 487500 }, trueNorthGridBearingDeg: 0.395,
    spawn: { E: 121318, N: 487362, headingDeg: 0, label: 'Dam' },
    latLon: { lat: 52.3731, lon: 4.8926 }, tiles: '/cities/amsterdam/coarse/', room: '/city/amsterdam',
  },
  rotterdam: {
    name: 'rotterdam', title: 'Rotterdam', crs: 'EPSG:28992', heightDatum: 'NAP', projection: { kind: 'rd' },
    origin: { E: 93000, N: 437500 }, trueNorthGridBearingDeg: 0.711,
    spawn: { E: 93058, N: 437219, headingDeg: 0, label: 'Markthal' },
    latLon: { lat: 51.92, lon: 4.4868 }, tiles: '/cities/rotterdam/coarse/', room: '/city/rotterdam',
  },
};

export const isCityName = (name: unknown): name is CityName => typeof name === 'string' && Object.hasOwn(CITIES, name);

/** The city a page plays: `?city=NAME`, Turku without it. An unknown name is an error, not a silent Turku. */
export function cityOf(search: string): CityProfile {
  const name = new URLSearchParams(search).get('city');
  if (name === null || name === '') return CITIES[HOME_CITY];
  if (!isCityName(name)) throw new Error(`bad ?city= (${Object.keys(CITIES).join('|')}): ${name}`);
  return CITIES[name];
}

/** The active city of this page (worker and test contexts without a location play Turku). */
export const CITY: CityProfile = cityOf(globalThis.location?.search ?? '');

/** Whether `name` is the home city, the only one with the story content (missions, shops, landmarks, point cloud). */
export const isHome = (city: CityProfile = CITY) => city.name === HOME_CITY;

/**
 * The page URL that plays `name`: the current page's own query without the city-specific keys (`city`, `spawn`), plus
 * `city` for a city other than Turku and `spawn` when given (E,N,heading in that city's grid).
 */
export function cityPageUrl(name: CityName, page: Pick<Location, 'href'>, spawn?: { E: number; N: number; headingDeg: number }): string {
  const url = new URL(page.href);
  url.searchParams.delete('city');
  url.searchParams.delete('spawn');
  if (name !== HOME_CITY) url.searchParams.set('city', name);
  if (spawn) url.searchParams.set('spawn', `${spawn.E.toFixed(1)},${spawn.N.toFixed(1)},${spawn.headingDeg.toFixed(0)}`);
  url.hash = '';
  return url.href;
}

/**
 * Same-origin `/coarse/...` request paths onto the active city's tile mount. The tile service names absolute
 * `/coarse/...` URLs in its manifests (collision, scenery, models); a city mounted at `/cities/<name>/coarse/` on the
 * game's origin receives them unchanged. Returns null when nothing changes.
 */
export function cityTileUrl(input: string, pageOrigin: string, city: CityProfile = CITY): string | null {
  if (city.tiles === '/coarse/') return null;
  let url: URL;
  try { url = new URL(input, pageOrigin); } catch { return null; }
  if (url.origin !== new URL(pageOrigin).origin) return null;
  if (url.pathname !== '/coarse' && !url.pathname.startsWith('/coarse/')) return null;
  url.pathname = city.tiles + url.pathname.slice('/coarse/'.length);
  return url.href;
}

/** Routes this page's `/coarse/...` fetches (tiles, models, collision, scenery) to the active city's mount. */
export function installCityFetch(target: { fetch: typeof fetch; location?: Location } = globalThis as never, city: CityProfile = CITY) {
  if (city.tiles === '/coarse/' || !target.location) return;
  const origin = target.location.origin;
  const original = target.fetch.bind(target);
  target.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const href = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const mapped = cityTileUrl(href, origin, city);
    if (!mapped) return original(input, init);
    return original(typeof input === 'string' || input instanceof URL ? mapped : new Request(mapped, input), init);
  }) as typeof fetch;
}

/** Storage key of per-city browser state: Turku keeps the historic key, other cities add `.<name>`. */
export const cityKey = (key: string, city: CityProfile = CITY) => city.name === HOME_CITY ? key : `${key}.${city.name}`;
