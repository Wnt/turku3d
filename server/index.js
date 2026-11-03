import http from 'node:http';
import { fileURLToPath } from 'node:url';
import { ScoreStore } from './store.js';
import { createServer } from './http.js';

const store = new ScoreStore(process.env.TURKU_MP_SCORES ?? fileURLToPath(new URL('./data/scores/', import.meta.url)));
// A share host's server adds its page origin (TURKU_MP_ORIGINS=https://city.lab.madekivi.fi, exact, comma-separated) and
// has no tile producer to notify (TURKU_MP_PRODUCER_PORTS='').
const origins = (process.env.TURKU_MP_ORIGINS ?? '').split(',').map(o => o.trim()).filter(Boolean);
const producers = (process.env.TURKU_MP_PRODUCER_PORTS ?? '7765,7766').split(',').map(Number).filter(p => p > 0);
const app = createServer({ store, origins, onConnection() {
  // Notify both persistent producer slots, including standby during cutover.
  for (const port of producers) {
    const request = http.request({ host: '127.0.0.1', port, method: 'POST',
      path: '/internal/player-connected', timeout: 500 }, response => response.resume());
    request.on('error', () => {});
    request.on('timeout', () => request.destroy());
    request.end();
  }
} });
const port = Number(process.env.TURKU_MP_PORT ?? 7700);
app.server.listen(port, '127.0.0.1', () => {
  console.log(`Turku multiplayer v2 listening on 127.0.0.1:${app.server.address().port}`);
  // Used by the load test to measure the server in a separate process.
  process.send?.({ port: app.server.address().port });
});
process.on('message', message => {
  if (message === 'metrics') process.send?.({ cpu: process.cpuUsage(), stats: app.room.stats, vehicles: app.room.vehicles.entries.size });
});
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, async () => {
  await app.close();
  process.exit(0);
});
