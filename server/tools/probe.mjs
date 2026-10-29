import { WebSocket } from 'ws';

const url = process.argv[2] ?? 'wss://turku.lab.madekivi.fi/';
const ws = new WebSocket(url, { origin: 'https://wnt.github.io', handshakeTimeout: 10_000 });
let welcomed = false;
const deadline = setTimeout(() => { console.error('Probe timed out'); process.exitCode = 1; ws.terminate(); }, 15_000);
ws.on('open', () => ws.send(JSON.stringify({ v: 2, type: 'hello' })));
ws.on('message', data => {
  const m = JSON.parse(data.toString());
  if (m.type !== 'welcome') return;
  if (m.v !== 2 || !m.you?.id || !m.you?.nick || !m.session || m.hour?.tz !== 'Europe/Helsinki'
    || !Number.isFinite(m.serverTime) || !Array.isArray(m.vehicles?.upsert) || !Array.isArray(m.vehicles?.sources)) {
    console.error('Invalid welcome'); process.exitCode = 1; ws.close(); return;
  }
  welcomed = true;
  // Never print the bearer session token.
  console.log(JSON.stringify({ ok: true, endpoint: url, v: m.v, nick: m.you.nick,
    spawn: m.you.spawn, hour: m.hour, players: m.players.length, vehicles: m.vehicles.upsert.length, sharedVehicleRegistry: true }));
  ws.close(1000, 'probe complete');
});
ws.on('error', error => { console.error(`Probe failed: ${error.message}`); process.exitCode = 1; });
ws.on('close', () => { clearTimeout(deadline); if (!welcomed) process.exitCode = 1; });
