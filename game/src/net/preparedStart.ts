import { MultiplayerClient } from './client';
import { multiplayerStart } from '../gameplay/startPoints';

/** Resolve the assigned/retained position before enabling Multiplayer in the menu. */
export function prepareMultiplayer() {
  const client = new MultiplayerClient();
  const ready = new Promise<ReturnType<typeof multiplayerStart> | null>(resolve => {
    const timer = setTimeout(() => { client.disconnect(); resolve(null); }, 10000);
    client.addEventListener('welcome', () => {
      clearTimeout(timer);
      const self = client.state.you!.id;
      resolve(multiplayerStart(client.state.you!, client.state.players.find(p => p.id === self),
        [...client.state.vehicles.values()].find(v => v.occupant === self && v.owner === self)));
    }, { once: true });
    client.connect();
  });
  return { client, ready };
}
