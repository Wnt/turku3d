// Named teleport targets of the developer tools (ui/devMenu.ts, localhost only). E/N are EPSG:3877 and the heading
// is a compass bearing, as in `?spawn=E,N,heading`; the ground under each point is found from the sky. Points stand
// on open ground (checked against the city's building footprints, GIS:Rakennukset), facing the named place.

export interface Place { id: string; label: string; E: number; N: number; headingDeg: number }

export const PLACES: readonly Place[] = [
  // The published start (manifest spawn) on the street in front of Läntinen Rantakatu 15.
  { id: 'office', label: 'Office', E: 23459673.3, N: 6704247.5, headingDeg: 331 },
  // The pizzeria's zone (content/locations.ts PIZZERIA, engine 77.1, 187.6).
  { id: 'pizzeria', label: 'Pizzeria', E: 23459577.1, N: 6704312.4, headingDeg: 0 },
  // Tuomiokirkkotori, 30 m in front of the west tower.
  { id: 'tuomiokirkko', label: 'Tuomiokirkko', E: 23460215.6, N: 6704686.0, headingDeg: 90 },
  // North-east of the castle (60.4353 N, 22.2289 E), facing it.
  { id: 'turun-linna', label: 'Turun linna', E: 23457580.0, N: 6702888.7, headingDeg: 225 },
  // The middle of the market square (OSM amenity=marketplace).
  { id: 'kauppatori', label: 'Kauppatori', E: 23459652.0, N: 6704624.0, headingDeg: 135 },
  // The pavement in front of Aurakatu 6, between the address points of Aurakatu 4 and 8, facing the door.
  { id: 'aurakatu-6a', label: 'Aurakatu 6a', E: 23459693.4, N: 6704420.2, headingDeg: 236 },
];

/** Parses the "Enter E,N" text: `E,N` or `E,N,heading` (spaces allowed), or null when it is not that. */
export function parsePlace(text: string): { E: number; N: number; headingDeg: number } | null {
  const parts = text.split(',').map(v => v.trim());
  if (parts.length < 2 || parts.length > 3 || parts.some(v => v === '')) return null;
  const [E, N, headingDeg = 0] = parts.map(Number);
  return [E, N, headingDeg].every(Number.isFinite) ? { E, N, headingDeg } : null;
}
