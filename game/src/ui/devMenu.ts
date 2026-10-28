// Developer pages of the pause menu (localhost, or ?dev=1 anywhere including Pages, core/localOnly.ts devToolsEnabled): money, fly mode,
// spawning the trophy truck, teleport and the time of day. The pages are MenuPages like the save pages (menu.ts); what a pick does to the
// running game is in DevHooks (play.ts).
import { DAY_PHASES, type DayPhase } from '../core/sun';
import { PLACES, parsePlace, type Place } from '../gameplay/content/places';
import type { MenuHost, MenuItem, MenuPage } from './menu';

const DEV_MONEY_STEPS = [1000, 10_000] as const;

/** What the developer pages do to the running game. */
export interface DevHooks {
  money(): number;
  /** Adds euros to the local wallet only (the save marks them as `devMoney`). */
  addMoney(euros: number): void;
  flying(): boolean;
  setFlying(on: boolean): void;
  /** Teleports to E/N (EPSG:3877) facing `headingDeg`, like `?spawn=`; closes the menu. */
  teleport(target: { E: number; N: number; headingDeg: number }): void;
  /** Whether the collision under E/N is built; elsewhere a teleport arrives flying. */
  built(E: number, N: number): boolean;
  /** Asks for the "E,N[,heading]" text (window.prompt); null when cancelled. */
  ask(question: string): string | null;
  setTime(phase: DayPhase): void;
  nextTrack(): void;
  /** Puts the trophy truck on the ground in front of the camera, without buying it. */
  spawnTruck(): void;
}

const euros = (n: number) => `${n.toLocaleString('en-US').replace(/,/g, ' ')} €`;
const PHASE_LABELS: Record<DayPhase, string> = { dawn: 'Dawn', noon: 'Noon', dusk: 'Dusk', night: 'Night' };

export class DevMenuFlow {
  /** `back` returns the pause menu root. */
  constructor(private readonly host: MenuHost, private readonly dev: DevHooks, private readonly back: () => MenuPage) {}

  /** The Developer page; `text` is a status line such as "Fly mode on.". */
  root(text: string | null = null): MenuPage {
    const back = () => this.host.show(this.back());
    const money = (step: number): MenuItem => ({ id: `money-${step}`, label: `Money +${euros(step)}`, detail: null,
      enabled: true, run: () => { this.dev.addMoney(step); this.host.show(this.root(`Money ${euros(this.dev.money())}.`)); } });
    const flying = this.dev.flying();
    return { id: 'dev', title: 'Developer', text: text ?? `Money ${euros(this.dev.money())}.`, back, items: [
      ...DEV_MONEY_STEPS.map(money),
      { id: 'fly', label: `Fly mode ${flying ? 'off' : 'on'}`, detail: 'Double-tap jump in the game', enabled: true,
        run: () => { this.dev.setFlying(!flying); this.host.show(this.root(`Fly mode ${flying ? 'off' : 'on'}.`)); } },
      { id: 'truck', label: 'Spawn trophy truck here', detail: 'In front of you; not bought', enabled: true,
        run: () => { this.dev.spawnTruck(); this.host.show(this.root('Trophy truck spawned in front of you.')); } },
      { id: 'teleport', label: 'Teleport to...', detail: null, enabled: true, run: () => this.host.show(this.teleport()) },
      { id: 'time', label: 'Time of day', detail: null, enabled: true, run: () => this.host.show(this.time()) },
      { id: 'music-next', label: 'Next music track', detail: null, enabled: true,
        run: () => { this.dev.nextTrack(); this.host.show(this.root('Next track selected.')); } },
      { id: 'back', label: 'Back', detail: null, enabled: true, run: back },
    ] };
  }

  /** The named places and "Enter E,N". */
  teleport(text: string | null = null): MenuPage {
    const back = () => this.host.show(this.root());
    const place = (p: Place): MenuItem => ({ id: `place-${p.id}`, label: p.label,
      detail: this.dev.built(p.E, p.N) ? null : 'Not built: arrives flying', enabled: true, run: () => this.dev.teleport(p) });
    return { id: 'dev-teleport', title: 'Teleport to', text, back, items: [
      ...PLACES.map(place),
      { id: 'enter', label: 'Enter E,N', detail: 'EPSG:3877, optional heading', enabled: true, run: () => {
        const answer = this.dev.ask('Teleport to E,N[,heading] (EPSG:3877, as ?spawn=)');
        if (answer === null) return;
        const target = parsePlace(answer);
        if (target) this.dev.teleport(target);
        else this.host.show(this.teleport(`Not E,N: ${answer}`));
      } },
      { id: 'back', label: 'Back', detail: null, enabled: true, run: back },
    ] };
  }

  /** Dawn, noon, dusk and night on the game clock's day. */
  time(): MenuPage {
    const back = () => this.host.show(this.root());
    return { id: 'dev-time', title: 'Time of day', text: null, back, items: [
      ...DAY_PHASES.map((phase): MenuItem => ({ id: `time-${phase}`, label: PHASE_LABELS[phase], detail: null, enabled: true,
        run: () => { this.dev.setTime(phase); this.host.show(this.root(`${PHASE_LABELS[phase]}.`)); } })),
      { id: 'back', label: 'Back', detail: null, enabled: true, run: back },
    ] };
  }
}
