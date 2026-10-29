import http from 'node:http';
import { fileURLToPath } from 'node:url';
import { ScoreStore } from './store.js';
import { createServer } from './http.js';

const store = new ScoreStore(process.env.TURKU_MP_SCORES ?? fileURLToPath(new URL('./data/scores/', import.meta.url)));
const app = createServer({ store, onConnection() {
  // Notify both persistent producer slots, including standby during cutover.
  for (const port of [7765, 7766]) {
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
