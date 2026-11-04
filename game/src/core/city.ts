// The city this page plays (mirror of pipeline/turku3d/cities). `?city=espoo` selects another city's frame; its tiles
// come from that city's own tile service (the page's /coarse/ is proxied there). Turku is the default.
export interface CityFrame {
  name: string;
  title: string;
  /** World origin O (NW corner of a 500 m block) in the city's GK zone. */
  E0: number;
  N0: number;
  /** GK zone: central meridian (deg) of the city's EPSG code. */
  gkMeridian: number;
  epsg: string;
  /** Grid bearing (deg) of true north at O (meridian convergence). */
  trueNorthGridBearingDeg: number;
  /** Default spawn, E/N in the city's CRS. */
  spawn: { E: number; N: number };
  spawnLabel: string;
}

export const CITIES: Record<string, CityFrame> = {
  turku: {
    name: 'turku', title: 'Turku', E0: 23459500, N0: 6704500, gkMeridian: 23, epsg: 'EPSG:3877',
    trueNorthGridBearingDeg: 0.64, spawn: { E: 23459668, N: 6704254 }, spawnLabel: 'Läntinen Rantakatu 15',
  },
  espoo: {
    // pyproj: convergence +0.17 deg at Tapiola (24.80 E, GK25).
    name: 'espoo', title: 'Espoo', E0: 25489000, N0: 6674000, gkMeridian: 25, epsg: 'EPSG:3879',
    trueNorthGridBearingDeg: 0.17, spawn: { E: 25489150, N: 6673640 }, spawnLabel: 'Tapiontori, Tapiola',
  },
};

export function cityFromSearch(search: string): CityFrame {
  const name = new URLSearchParams(search).get('city') ?? 'turku';
  const city = CITIES[name];
  if (!city) throw new Error(`bad ?city= (${Object.keys(CITIES).join('|')}): ${name}`);
  return city;
}

export const CITY: CityFrame = cityFromSearch(typeof location === 'undefined' ? '' : location.search);
