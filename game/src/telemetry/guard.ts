// Where telemetry runs: on a local origin (or with ?telemetry=1), never on the published GitHub Pages site, and not
// with ?telemetry=0. Browser automation is no reason to stay off: the player may play in a Chrome window that a tool
// launched (chrome-devtools-mcp sets navigator.webdriver), and browser checks post to their own sandbox's server.
export function telemetryEnabled(location: { hostname: string; search: string }): boolean {
  const host = location.hostname.toLowerCase();
  if (host === 'github.io' || host.endsWith('.github.io')) return false;
  const param = new URLSearchParams(location.search).get('telemetry');
  if (param === '0') return false;
  if (param === '1') return true;
  return host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1';
}
