import type { PhysicsClient } from '../physics/client';
import type { SaveGameController } from '../gameplay/saveGame';
import { multiplayerStart } from '../gameplay/startPoints';
import type { SharedVehicles } from './sharedVehicles';
import type { MultiplayerClient, NetPlayer } from './client';

/** Reset gameplay to the new-game baseline (including its starting wallet), retaining loaded world assets. */
export async function resetRoundGame(you: Pick<NetPlayer, 'spawn'>, shared: SharedVehicles,
  physics: PhysicsClient, save: SaveGameController, leave: () => void) {
  shared.resetRound(); leave();
  for (const v of physics.latest?.vehicles ?? []) if (v.id && v.id !== 'player') physics.removeVehicle(v.id);
  await save.playAt(multiplayerStart(you));
}

export function bindRoundReset(client: MultiplayerClient, playing: () => boolean, shared: SharedVehicles,
  physics: PhysicsClient, save: SaveGameController, leave: () => void, fail: (error: unknown) => void) {
  client.addEventListener('roundReset', () => {
    if (playing() && client.state.you) void resetRoundGame(client.state.you, shared, physics, save, leave).catch(fail);
  });
}
