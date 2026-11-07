import { describe, expect, it } from 'vitest';
import { CITIES, cityKey, cityOf, cityPageUrl, cityTileUrl, installCityFetch } from '../../src/core/city';
import { enToLatLon, gkToLatLon, rdToLatLon } from '../../src/core/geographic';
import { draftsUrl, multiplayerUrl } from '../../src/net/config';
import {
  EXIT_DISTANCE_M, PORTAL_HEIGHT, PortalTriggers, TURKU_PORTALS, exitPose, insidePortal, portalLocal, portalsIn,
  returnPortal, returnTarget, type PortalDef,
} from '../../src/gameplay/portals';
import {
  CAME_THROUGH_KEY, CityAvailability, TICKET_KEY, TICKET_TTL_MS, cameThrough, cityServed, takeTicket, walletOnly,
  writeTicket, type TravelTicket,
} from '../../src/gameplay/travel';
import { SaveMenuFlow, type GameHooks, type MenuPage } from '../../src/ui/menu';
import { SAVES_KEY, SaveStore, type KeyValueStore } from '../../src/core/save';
import { missionSummary } from '../../src/gameplay/missions/missions';
import { EMPTY_HUD_STATE, type MissionHudState } from '../../src/gameplay/contract';
import { enToEngine } from '../../src/core/origin';

class MemoryStore implements KeyValueStore {
  data = new Map<string, string>();
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
  removeItem(key: string) { this.data.delete(key); }
}

const portal = (id: string) => TURKU_PORTALS.find(p => p.id === id)!;
/** A point `along` the portal's travel direction and `across` to its right, as engine x/z. */
const at = (p: PortalDef, along: number, across = 0) => {
  const h = p.headingDeg * Math.PI / 180;
  return { x: p.x + along * Math.sin(h) + across * Math.cos(h), z: p.z - along * Math.cos(h) + across * Math.sin(h) };
};

describe('portal trigger', () => {
  const p = portal('kupittaa-southeast');
  const ground = 18.6;

  it('measures along the lanes and across them', () => {
    const q = at(p, 2, -3);
    const local = portalLocal(p, q.x, q.z);
    expect(local.along).toBeCloseTo(2, 6);
    expect(local.across).toBeCloseTo(-3, 6);
  });

  it('fires once when a car drives through, never for traffic on the deck above', () => {
    const triggers = new PortalTriggers([p]);
    const fired: string[] = [];
    for (let along = -30; along <= 30; along += 0.5) {
      const q = at(p, along, 2);
      const hit = triggers.update(q.x, ground + 0.4, q.z, () => ground);
      if (hit) fired.push(`${hit.id}@${along}`);
    }
    expect(fired).toEqual([`${p.id}@-3`]);
    const deck = new PortalTriggers([p]);
    for (let along = -30; along <= 30; along += 0.5) {
      const q = at(p, along);
      expect(deck.update(q.x, ground + 7, q.z, () => ground)).toBeNull();
    }
    expect(insidePortal(p, ground, p.x, ground + PORTAL_HEIGHT + 0.1, p.z)).toBe(false);
  });

  it('ignores a pass beside the lanes and a portal whose ground is not known yet', () => {
    const triggers = new PortalTriggers([p]);
    for (let along = -30; along <= 30; along += 0.5) {
      const beside = at(p, along, p.width / 2 + 1);
      expect(triggers.update(beside.x, ground, beside.z, () => ground)).toBeNull();
      const q = at(p, along);
      expect(triggers.update(q.x, ground, q.z, () => null)).toBeNull();
    }
  });

  it('arms only after the player has been outside: an arrival or a load inside a portal never travels at once', () => {
    const triggers = new PortalTriggers([p]);
    expect(triggers.update(p.x, ground, p.z, () => ground)).toBeNull();
    const out = at(p, 12);
    expect(triggers.update(out.x, ground, out.z, () => ground)).toBeNull();
    expect(triggers.update(p.x, ground, p.z, () => ground)?.id).toBe(p.id);
    expect(triggers.update(p.x, ground, p.z, () => ground)).toBeNull();
  });

  it('places the four Turku portals under their bridges with their targets', () => {
    expect(TURKU_PORTALS.map(p => `${p.id}->${p.to}`)).toEqual(['kupittaa-southeast->espoo', 'kupittaa-northwest->espoo',
      'aninkainen-rail->amsterdam', 'aninkainen-path->rotterdam']);
    // The OSM centreline crossings of each carriageway with the bridge deck (EPSG:3877), within a few metres.
    const crossings: Record<string, [number, number]> = { 'kupittaa-southeast': [23461274, 6704564],
      'kupittaa-northwest': [23461288.9, 6704572.5], 'aninkainen-rail': [23459719.2, 6705411.8], 'aninkainen-path': [23459727.8, 6705398.6] };
    for (const p of TURKU_PORTALS) {
      const c = enToEngine(...crossings[p.id]);
      expect(Math.abs(portalLocal(p, c.x, c.z).across), p.id).toBeLessThan(1.5);
      expect(Math.abs(portalLocal(p, c.x, c.z).along), p.id).toBeLessThan(10);
    }
    // Under Aninkaistensilta the railway is north-west of Ratapihankatu: the Amsterdam lanes are the railway's side.
    const rail = portal('aninkainen-rail'), path = portal('aninkainen-path');
    expect(rail.x - path.x).toBeLessThan(0);
    expect(rail.z - path.z).toBeLessThan(0);
  });

  it('returns past the portal it came through, facing on along its lanes', () => {
    const p = portal('aninkainen-path');
    const exit = exitPose(p);
    const e = enToEngine(exit.E, exit.N);
    expect(portalLocal(p, e.x, e.z).along).toBeCloseTo(EXIT_DISTANCE_M, 6);
    expect(exit.headingDeg).toBe(p.headingDeg);
    // In Turku's grid whatever city the page plays (the return URL is built on the other city's page).
    expect(exit.E).toBeGreaterThan(23459000); expect(exit.E).toBeLessThan(23462000);
    expect(exit.N).toBeGreaterThan(6704000); expect(exit.N).toBeLessThan(6706000);
    expect(returnTarget('espoo', 'kupittaa-northwest').id).toBe('kupittaa-northwest');
    expect(returnTarget('espoo', 'aninkainen-rail').id).toBe('kupittaa-southeast');
    expect(returnTarget('rotterdam', null).id).toBe('aninkainen-path');
  });

  it('stands a return arch ahead of every other city\'s centre spawn and none in Turku', () => {
    expect(returnPortal(CITIES.turku)).toBeNull();
    expect(portalsIn(CITIES.turku)).toBe(TURKU_PORTALS);
    for (const name of ['espoo', 'amsterdam', 'rotterdam'] as const) {
      const back = returnPortal(CITIES[name])!;
      expect(back.to).toBe('turku');
      expect(portalsIn(CITIES[name])).toEqual([back]);
      const arch = CITIES[name].arch;
      if (arch) {
        // Placed by the profile (off the street the spawn faces), in the city's own engine metres.
        expect([back.x, back.z, back.headingDeg]).toEqual([arch.E - CITIES[name].origin.E, CITIES[name].origin.N - arch.N, arch.headingDeg]);
        continue;
      }
      const s = { x: CITIES[name].spawn.E - CITIES[name].origin.E, z: CITIES[name].origin.N - CITIES[name].spawn.N };
      expect(Math.hypot(back.x - s.x, back.z - s.z)).toBeCloseTo(12, 6);
      // Ahead of the spawn heading: the arriving player faces it.
      expect(portalLocal({ ...back, x: s.x, z: s.z, headingDeg: CITIES[name].spawn.headingDeg }, back.x, back.z).along).toBeCloseTo(12, 6);
    }
  });
});

describe('city switch state', () => {
  it('picks the city from ?city= and refuses an unknown one', () => {
    expect(cityOf('').name).toBe('turku');
    expect(cityOf('?city=espoo').name).toBe('espoo');
    expect(cityOf('?city=').name).toBe('turku');
    expect(() => cityOf('?city=narnia')).toThrow(/bad \?city=/);
  });

  it('builds the target page URL, keeping the other parameters', () => {
    const page = { href: 'https://city.lab.madekivi.fi/?quality=high&spawn=1,2,3&city=espoo#x' };
    expect(cityPageUrl('amsterdam', page)).toBe('https://city.lab.madekivi.fi/?quality=high&city=amsterdam');
    expect(cityPageUrl('turku', page, { E: 23461295.5, N: 6704550.04, headingDeg: 148 }))
      .toBe('https://city.lab.madekivi.fi/?quality=high&spawn=23461295.5%2C6704550.0%2C148');
  });

  it('maps tile requests onto the city mount on the same origin only', () => {
    const origin = 'https://city.lab.madekivi.fi';
    expect(cityTileUrl('/coarse/t0_0_0/manifest.json?d=3', origin, CITIES.espoo))
      .toBe('https://city.lab.madekivi.fi/cities/espoo/coarse/t0_0_0/manifest.json?d=3');
    expect(cityTileUrl('https://city.lab.madekivi.fi/coarse/_v/ab/t0_0_0/col/c_0_0.col', origin, CITIES.espoo))
      .toBe('https://city.lab.madekivi.fi/cities/espoo/coarse/_v/ab/t0_0_0/col/c_0_0.col');
    expect(cityTileUrl('/coarse/health', origin, CITIES.turku)).toBeNull();
    expect(cityTileUrl('/assets/car/sedan.glb', origin, CITIES.espoo)).toBeNull();
    expect(cityTileUrl('/coarsely/x', origin, CITIES.espoo)).toBeNull();
    expect(cityTileUrl('https://turku.lab.madekivi.fi/coarse/health', origin, CITIES.espoo)).toBeNull();
  });

  it('routes the page\'s tile fetches through the mount', async () => {
    const seen: string[] = [];
    const target = { fetch: (async (input: RequestInfo | URL) => { seen.push(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url); return new Response('{}'); }) as typeof fetch,
      location: { origin: 'https://city.lab.madekivi.fi' } as Location };
    installCityFetch(target, CITIES.rotterdam);
    await target.fetch('/coarse/health');
    await target.fetch(new Request('https://city.lab.madekivi.fi/coarse/t0_1_1/roads.json'));
    await target.fetch('/assets/walkers/walkers.bin');
    expect(seen).toEqual(['https://city.lab.madekivi.fi/cities/rotterdam/coarse/health',
      'https://city.lab.madekivi.fi/cities/rotterdam/coarse/t0_1_1/roads.json', '/assets/walkers/walkers.bin']);
  });

  it('joins each city\'s own multiplayer room and serves other cities on same-origin hosts only', () => {
    const share = { hostname: 'localhost', search: '', host: 'localhost:5220', protocol: 'http:' };
    expect(multiplayerUrl(share, CITIES.turku)).toBe('ws://127.0.0.1:7700/');
    expect(multiplayerUrl(share, CITIES.espoo)).toBe('ws://127.0.0.1:7700/city/espoo');
    expect(multiplayerUrl({ ...share, search: '?mp=ws://10.0.0.2:7700/' }, CITIES.amsterdam)).toBe('ws://10.0.0.2:7700/city/amsterdam');
    expect(multiplayerUrl({ hostname: 'wnt.github.io', search: '' }, CITIES.rotterdam)).toBe('wss://turku.lab.madekivi.fi/city/rotterdam');
    expect(draftsUrl({ hostname: 'localhost' }, CITIES.espoo)).toBe('/coarse/');
    expect(draftsUrl({ hostname: 'wnt.github.io' }, CITIES.espoo)).toBeNull();
  });

  it('keeps saves and sessions per city', () => {
    expect(cityKey('turku3d.saves', CITIES.turku)).toBe('turku3d.saves');
    expect(cityKey('turku3d.saves', CITIES.espoo)).toBe('turku3d.saves.espoo');
    const storage = new MemoryStore();
    storage.setItem('turku3d.missions', '{}');
    const espoo = new SaveStore(storage, missionSummary, cityKey(SAVES_KEY, CITIES.espoo));
    expect(espoo.any).toBe(false);
    expect(storage.getItem('turku3d.missions')).toBe('{}'); // only Turku's slots migrate the legacy save
  });

  it('projects every city grid to WGS84', () => {
    const near = (a: { lat: number; lon: number }, lat: number, lon: number, digits = 4) => {
      expect(a.lat).toBeCloseTo(lat, digits); expect(a.lon).toBeCloseTo(lon, digits);
    };
    // The profiles' sun positions are the spawns' coordinates (Turku's rounded to 0.001 deg).
    for (const city of Object.values(CITIES)) near(enToLatLon(city.spawn.E, city.spawn.N, city.projection), city.latLon.lat, city.latLon.lon, 3);
    near(enToLatLon(25489150, 6673640, CITIES.espoo.projection), 60.175482, 24.804519); // pyproj EPSG:3879
    near(enToLatLon(121318, 487362, CITIES.amsterdam.projection), 52.373099, 4.892604); // pyproj EPSG:28992
    near(enToLatLon(93075, 437390, CITIES.rotterdam.projection), 51.921541, 4.487011); // pyproj EPSG:28992
    near(gkToLatLon(23459668, 6704254, 23), 60.44839, 22.26726);
    near(rdToLatLon(155000, 463000), 52.15517, 5.38721); // Amersfoort, the RD origin
  });

  it('carries a ticket to its city only, fresh, once; the stay remembers its Turku portal', () => {
    const store = new MemoryStore();
    const ticket: TravelTicket = { v: 1, from: 'turku', to: 'espoo', portal: 'kupittaa-northwest', ride: { id: 'player', kind: 'car' },
      mission: '{"money":120}', multiplayer: true, at: 1000 };
    writeTicket(store, ticket);
    expect(takeTicket(store, 'amsterdam', 2000)).toBeNull();
    expect(store.getItem(TICKET_KEY)).toBeNull(); // a ticket for another city is dropped, not kept for later
    writeTicket(store, ticket);
    expect(takeTicket(store, 'espoo', 1000 + TICKET_TTL_MS + 1)).toBeNull();
    writeTicket(store, ticket);
    expect(takeTicket(store, 'espoo', 5000)).toEqual(ticket);
    expect(takeTicket(store, 'espoo', 5000)).toBeNull();
    expect(cameThrough(store)).toBe('kupittaa-northwest');
    writeTicket(store, { ...ticket, from: 'espoo', to: 'turku', at: 9000 });
    expect(takeTicket(store, 'turku', 9500)?.portal).toBe('kupittaa-northwest');
    expect(store.getItem(CAME_THROUGH_KEY)).toBeNull();
    store.setItem(TICKET_KEY, '{"v":1,"to":"espoo"}');
    expect(takeTicket(store, 'espoo', 0)).toBeNull();
  });

  it('opens a city only while its tile mount answers ready; Turku always', async () => {
    expect(await cityServed(CITIES.turku, async () => { throw new Error('not asked'); })).toBe(true);
    const asked: string[] = [];
    expect(await cityServed(CITIES.espoo, async url => { asked.push(url); return { state: 'ready' }; })).toBe(true);
    expect(asked).toEqual(['/cities/espoo/coarse/health']);
    expect(await cityServed(CITIES.espoo, async () => ({ state: 'offline' }))).toBe(false);
    expect(await cityServed(CITIES.espoo, async () => null)).toBe(false);
    expect(await cityServed(CITIES.espoo, async () => { throw new Error('timeout'); })).toBeNull();
    let answer: boolean | null = false;
    const availability = new CityAvailability(async () => answer, 1000);
    expect(availability.served('amsterdam', 0)).toBeNull();
    await new Promise(r => setTimeout(r, 0));
    expect(availability.served('amsterdam', 500)).toBe(false); // coming soon
    answer = true;
    expect(await availability.confirm('amsterdam', 600)).toBe(true);
    expect(availability.served('amsterdam', 700)).toBe(true);
    answer = null; // a busy host that does not answer keeps the last answer
    expect(await availability.confirm('amsterdam', 800)).toBe(true);
    expect(availability.served('amsterdam', 5000)).toBe(true);
  });

  it('shows only the wallet outside Turku', () => {
    const states: MissionHudState[] = [];
    const source = { subscribe: (l: (s: MissionHudState) => void) => { l({ ...EMPTY_HUD_STATE, money: 120, message: 'Auransilta',
      targets: [{ id: 'x', kind: 'dropoff', label: 'x', point: { x: 0, z: 0 } }] as MissionHudState['targets'] }); return () => {}; } };
    walletOnly(source).subscribe(s => states.push(s));
    expect(states).toEqual([{ ...EMPTY_HUD_STATE, money: 120 }]);
  });
});

describe('pause menu: Return to Turku', () => {
  const store = new SaveStore(new MemoryStore(), missionSummary);
  const hooks = (extra: Partial<GameHooks> = {}): GameHooks => ({ capture: () => { throw new Error('no capture'); }, play: () => {},
    resume: () => {}, quit: () => {}, respawn: () => {}, ...extra });
  const host = { show: (_page: MenuPage) => {} };

  it('lists the entry right after Resume in another city and runs the trip', () => {
    let trips = 0;
    const page = new SaveMenuFlow(host, store, hooks({ returnHome: { detail: 'To the Kupittaansilta (Kalevan silta) portal', run: () => { trips++; } } })).pause();
    expect(page.items.map(i => i.id).slice(0, 3)).toEqual(['resume', 'return-home', 'respawn']);
    const item = page.items.find(i => i.id === 'return-home')!;
    expect(item).toMatchObject({ label: 'Return to Turku', enabled: true, detail: 'To the Kupittaansilta (Kalevan silta) portal' });
    item.run();
    expect(trips).toBe(1);
  });

  it('has no such entry in Turku', () => {
    expect(new SaveMenuFlow(host, store, hooks()).pause().items.some(i => i.id === 'return-home')).toBe(false);
  });
});
