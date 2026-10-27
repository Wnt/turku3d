// Where telemetry runs: on a local origin (or with ?telemetry=1), never on the published GitHub Pages site, and not
// with ?telemetry=0. Browser automation is no reason to stay off: the player may play in a Chrome window that a tool
// launched (chrome-devtools-mcp sets navigator.webdriver), and browser checks post to their own sandbox's server.
import { localFeature } from '../core/localOnly';

export const telemetryEnabled = (location: { hostname: string; search: string }) => localFeature(location, 'telemetry');
