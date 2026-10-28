import assert from 'node:assert/strict';
import { WebSocket } from 'ws';

const url = process.argv[2] ?? 'wss://turku.lab.madekivi.fi/';
const clients = [];
const deadline = setTimeout(() => {
  console.error('Presence probe timed out');
  for (const ws of clients) ws.terminate();
  process.exitCode = 1;
}, 20_000);

async function join() {
  const ws = new WebSocket(url, { origin: 'https://wnt.github.io', handshakeTimeout: 10_000 });
  clients.push(ws);
  return new Promise((resolve, reject) => {
    ws.on('open', () => ws.send(JSON.stringify({ v: 1, type: 'hello' })));
    ws.on('error', reject);
    ws.on('close', () => reject(new Error('Closed before welcome')));
    ws.on('message', data => {
      const message = JSON.parse(data.toString());
      if (message.type === 'welcome') resolve(message);
    });
  });
}

try {
  const first = await join();
  const second = await join();
  assert.equal(second.v, 1);
  assert.equal(second.hour.tz, 'Europe/Helsinki');
  assert.notEqual(first.you.id, second.you.id);
  assert(second.players.some(p => p.id === first.you.id));
  assert(second.players.some(p => p.id === second.you.id));
  console.log(JSON.stringify({ ok: true, endpoint: url, players: second.players.length, presence: true }));
} catch (error) {
  console.error(`Presence probe failed: ${error.message}`);
  process.exitCode = 1;
} finally {
  clearTimeout(deadline);
  for (const ws of clients) ws.close(1000, 'probe complete');
}
