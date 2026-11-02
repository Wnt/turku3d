// Features for the local game only (play telemetry, developer tools, draft tiles): on a local origin, or with
// ?<param>=1 on another host, but never on the published GitHub Pages site; ?<param>=0 turns one off locally.

// A private test share of a local build (e.g. a Cloudflare quick tunnel to the Mac) counts as local: build it with
// VITE_TEST_HOSTS=trycloudflare.com (comma-separated hostname suffixes). Pages builds leave it unset.
const TEST_HOSTS = String(import.meta.env?.VITE_TEST_HOSTS ?? '').split(',').map(h => h.trim().toLowerCase()).filter(Boolean);

export function isLocalHost(hostname: string): boolean {
  const host = hostname.toLowerCase();
  return host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1' ||
    TEST_HOSTS.some(suffix => host === suffix || host.endsWith(`.${suffix}`));
}

/** Whether the local-only feature behind the query parameter `param` runs at `location`. */
export function localFeature(location: { hostname: string; search: string }, param: string): boolean {
  const host = location.hostname.toLowerCase();
  if (host === 'github.io' || host.endsWith('.github.io')) return false;
  const value = new URLSearchParams(location.search).get(param);
  if (value === '0') return false;
  if (value === '1') return true;
  return isLocalHost(host);
}

/** Developer tools in the pause menu (ui/devMenu.ts): money, fly mode, teleport and the time of day. Unlike the
 *  other local features, ?dev=1 also opens them on GitHub Pages; such a session sends no scoreboard deliveries. */
export const devToolsEnabled = (location: { hostname: string; search: string }) =>
  new URLSearchParams(location.search).get('dev') === '1' || localFeature(location, 'dev');
