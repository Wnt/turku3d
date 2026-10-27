// Fullscreen policy for touch play: the start screen works in the normal browser view, the 3D scene runs only in
// fullscreen. Closing the start screen requests fullscreen inside the tap; leaving fullscreen while the scene runs
// pauses the game behind a "Tap to continue in fullscreen" overlay. Keyboard and gamepad players are never affected.

export interface FullscreenFacts {
  /** The player is on touch input (`Input.source === 'touch'`). */
  touch: boolean;
  /** The start screen is closed and the scene runs. */
  inScene: boolean;
  fullscreen: boolean;
  /** Launched from the Home Screen (display-mode standalone or fullscreen): already fullscreen. */
  standalone: boolean;
  /** The browser can fullscreen an element (iPhone Safari cannot). */
  supported: boolean;
}

/** Whether the scene must wait behind the fullscreen overlay. */
export function mustBlock(f: FullscreenFacts): boolean {
  return f.touch && f.inScene && f.supported && !f.standalone && !f.fullscreen;
}

/**
 * One frame of the pause interplay: blocking pauses the game, and unblocking resumes it only when the policy made that
 * pause (`own`), so a pause the player chose survives a trip out of fullscreen.
 */
export function guardPause(paused: boolean, own: boolean, blocking: boolean): { paused: boolean; own: boolean } {
  if (blocking) return paused ? { paused, own } : { paused: true, own: true };
  return own ? { paused: false, own: false } : { paused, own: false };
}

/** The start-screen hint for a touch device that cannot fullscreen the page, or null. */
export function fullscreenHint(touchDevice: boolean, supported: boolean, standalone: boolean): string | null {
  return touchDevice && !supported && !standalone ? 'For fullscreen on iPhone add the page to the Home Screen.' : null;
}

export class FullscreenPolicy {
  readonly overlay = document.createElement('div');
  readonly supported: boolean;
  private inScene = false;
  private own = false;
  private wakeLock: WakeLockSentinel | null = null;

  constructor(private readonly root: HTMLElement = document.documentElement) {
    this.supported = typeof root.requestFullscreen === 'function' && document.fullscreenEnabled !== false;
    this.overlay.id = 'fullscreen-overlay';
    this.overlay.hidden = true;
    this.overlay.setAttribute('role', 'dialog');
    this.overlay.append(Object.assign(document.createElement('button'), { type: 'button', textContent: 'Tap to continue in fullscreen' }));
    // Tapping anywhere on the overlay is the user gesture the request needs.
    this.overlay.addEventListener('click', () => this.request());
    document.body.append(this.overlay);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && this.inScene) this.lockWake(); });
  }

  get standalone() {
    return matchMedia('(display-mode: standalone)').matches || matchMedia('(display-mode: fullscreen)').matches
      || (navigator as { standalone?: boolean }).standalone === true;
  }

  /** Call synchronously from the gesture that closes the start screen. */
  enterScene(touch: boolean) {
    this.inScene = true;
    if (touch) this.request();
  }

  /** Per frame: shows or hides the overlay and returns the paused state after the policy's pause or resume. */
  update(touch: boolean, paused: boolean): boolean {
    const blocking = mustBlock({ touch, inScene: this.inScene, fullscreen: document.fullscreenElement !== null,
      standalone: this.standalone, supported: this.supported });
    this.overlay.hidden = !blocking;
    const next = guardPause(paused, this.own, blocking);
    this.own = next.own;
    return next.paused;
  }

  get blocking() { return !this.overlay.hidden; }

  private request() {
    if (!this.supported || this.standalone) return;
    // Rejections (no gesture, a browser policy, an unsupported lock) leave the overlay to retry on the next tap.
    if (!document.fullscreenElement) {
      this.root.requestFullscreen({ navigationUI: 'hide' })
        .then(() => (screen.orientation as { lock?: (o: string) => Promise<void> } | undefined)?.lock?.('landscape'))
        .catch(() => {});
    }
    this.lockWake();
  }

  private lockWake() {
    if (!('wakeLock' in navigator) || this.wakeLock) return;
    navigator.wakeLock.request('screen').then(lock => {
      this.wakeLock = lock;
      // The browser releases the lock when the page is hidden; visibilitychange takes a new one.
      lock.addEventListener('release', () => { this.wakeLock = null; });
    }).catch(() => {});
  }
}
