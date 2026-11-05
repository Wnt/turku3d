import { MultiplayerClient } from './client';
import { multiplayerStart } from '../gameplay/startPoints';

/** Resolve the assigned/retained position before enabling Multiplayer in the menu. */
/** `socket`: the previous city's open connection, moved to this city's room instead of a new one (an in-place switch).
 *  `arrival`: this page arrived from another city (a travel ticket): the room frees seats left behind here. */
export function prepareMultiplayer(start?: { x: number; y: number; z: number; yaw: number }, socket?: WebSocket | null, arrival = false) {
  const client = new MultiplayerClient();
  client.arrival = arrival;
  let position: ReturnType<typeof multiplayerStart> | null | undefined;
  const ready = new Promise<ReturnType<typeof multiplayerStart> | null>(resolve => {
    const timer = setTimeout(() => { client.disconnect(); position = null; resolve(null); }, 10000);
    client.addEventListener('welcome', () => {
      clearTimeout(timer);
      const self = client.state.you!.id;
      position = multiplayerStart(client.state.you!, client.state.players.find(p => p.id === self),
        [...client.state.vehicles.values()].find(v => v.occupant === self && v.owner === self), start);
      resolve(position);
    }, { once: true });
    if (socket) client.adopt(socket); else client.connect();
  });
  return { client, ready, get position() { return position; } };
}
