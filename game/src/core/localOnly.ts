// Features for the local game only (play telemetry, developer tools, draft tiles): on a local origin, or with
// ?<param>=1 on another host, but never on the published GitHub Pages site; ?<param>=0 turns one off locally.

export function isLocalHost(hostname: string): boolean {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]' || hostname === '::1';
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

/** Developer tools in the pause menu (ui/devMenu.ts): money, fly mode, teleport and the time of day. */
export const devToolsEnabled = (location: { hostname: string; search: string }) => localFeature(location, 'dev');
