// Straight-line Router used by main.ts until the street-network Router from gameplay-map-route is wired in.
import type { Router } from '../contract';

export const straightRouter: Router = {
  route: (from, to) => [from, to],
  distance: path => path.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - path[i].x, p.z - path[i].z), 0),
};
