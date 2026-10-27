// Where telemetry runs: only on a local origin (or with ?telemetry=1), never on the published GitHub Pages site, and
// not under browser automation unless the page asks for it, so test suites do not write play sessions.
export function telemetryEnabled(location: { hostname: string; search: string }, webdriver: boolean): boolean {
  const host = location.hostname.toLowerCase();
  if (host === 'github.io' || host.endsWith('.github.io')) return false;
  if (new URLSearchParams(location.search).get('telemetry') === '1') return true;
  if (webdriver) return false;
  return host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1';
}
