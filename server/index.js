import { fileURLToPath } from 'node:url';
import { ScoreStore } from './store.js';
import { createServer } from './http.js';

const store = new ScoreStore(process.env.TURKU_MP_SCORES ?? fileURLToPath(new URL('./data/scores/', import.meta.url)));
const app = createServer({ store });
const port = Number(process.env.TURKU_MP_PORT ?? 7700);
app.server.listen(port, '127.0.0.1', () => {
  console.log(`Turku multiplayer v1 listening on 127.0.0.1:${app.server.address().port}`);
  // Used by the load test to measure the server in a separate process.
  process.send?.({ port: app.server.address().port });
});
process.on('message', message => {
  if (message === 'metrics') process.send?.({ cpu: process.cpuUsage(), stats: app.room.stats });
});
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, async () => {
  await app.close();
  process.exit(0);
});
