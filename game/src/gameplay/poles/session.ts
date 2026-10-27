import type { VoicePlayer } from '../../audio/voice';
import type { MissionSystem } from '../missions/missions';
import type { MultiplayerClient } from '../../net/client';
import type { PhysicsClient } from '../../physics/client';
import type { PoleState } from '../../physics/protocol';
import { PoleNotice } from '../../ui/poles/notice';
import { BreakablePoles } from '../../world/poles';
import type { VisualTiles } from '../../world/tiles';

/** Binds pole physics to the local wallet, officer notice and current multiplayer hour. */
export class PoleSession {
  private notice: PoleNotice;
  private poles: BreakablePoles;
  private client: MultiplayerClient | null = null;
  constructor(tiles: VisualTiles, physics: PhysicsClient, missions: MissionSystem, voice: VoicePlayer, parent: HTMLElement) {
    this.notice = new PoleNotice(voice, parent);
    this.poles = new BreakablePoles(tiles, physics, id => {
      missions.finePole();
      this.notice.show();
      this.client?.breakPole(id);
    });
  }
  setClient(client: MultiplayerClient) {
    this.client = client;
    client.addEventListener('welcome', () => { if (this.client === client) this.poles.setBroken(client.state.brokenPoles); });
    client.addEventListener('poleBreak', event => {
      if (this.client === client) this.poles.breakRemote((event as CustomEvent<string>).detail);
    });
  }
  clearClient() { this.client = null; }
  update(states: readonly PoleState[]) { this.poles.update(states); }
  dispose() { this.poles.dispose(); this.notice.dispose(); }
}
