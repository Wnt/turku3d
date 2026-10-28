import type { Input } from '../input';

/** Device-specific prompts for the current control mode. */
export function controlsHelp(input: Input, mode: 'foot' | 'car', flying: boolean, paused: boolean, lost: boolean) {
  return paused ? `${input.prompt('pause')} Resume`
      : lost ? `${input.prompt('respawn')} return to solid ground · ${input.prompt('map')} map · ${input.prompt('pause')} pause`
      : mode === 'foot' && flying
      ? `Flying · ${input.prompt('moveY')} move · ${input.prompt('flyUp')} up · ${input.prompt('flyDown')} down · ${input.prompt('sprint')} fast · ${input.prompt('jump')} ${input.prompt('jump')} land · ${input.prompt('pause')} pause`
      : mode === 'foot'
      ? `${input.prompt('moveY')} move · ${input.source === 'gamepad' ? 'Ⓡ' : 'mouse'} look · ${input.prompt('sprint')} sprint · ${input.prompt('jump')} jump · ${input.prompt('interact')} enter vehicle · ${input.prompt('cameraReset')} camera · ${input.prompt('map')} map · ${input.prompt('pause')} pause`
      : `${input.prompt('throttle')} throttle · ${input.prompt('brake')} brake/reverse · ${input.prompt('steer')} steer · ${input.prompt('handbrake')} handbrake · ${input.prompt('interact')} exit · ${input.prompt('cameraReset')} camera · ${input.prompt('map')} map · ${input.prompt('pause')} pause`;
}
