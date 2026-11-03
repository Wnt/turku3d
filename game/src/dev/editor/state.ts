// The editor's input state machine, free of DOM and three.js: one frame of editor actions in, commands out.
// Mapping (README.md, STATUS): touchpad / L3+R3 / F8 toggles the mode; holding it 1.5 s closes the session.
//   aiming:  ✕ pick · ○ undo · L1/R1 cycle candidates · hold △ 1 s delete · D-pad picks a reason for 2 s after a commit
//            · Create toggles the point cloud; hold Create + D-pad ↑↓ opacity, ←→ point size.
//   holding: left stick moves it on the ground (camera-relative) · L2/R2 turn smoothly · L1/R1 turn 15° · D-pad ↑↓
//            height 5 cm (held: repeats), ←→ turn 1° · □ back onto the ground · ✕ drop (commit) · ○ cancel
//            · hold △ 1 s delete.
// Camera flight stays on the sticks, □ (double tap: fly), R2/L2 (up/down) and L3 (fast) while aiming.

export interface EditorFrame {
  dt: number;
  toggle: boolean; pick: boolean; back: boolean; prev: boolean; next: boolean; delete: boolean; ground: boolean;
  /** Analog L2 / R2 (0..1). */
  turnLeft: number; turnRight: number;
  up: boolean; down: boolean; left: boolean; right: boolean; lidar: boolean;
  /** Left stick, x right and y forward (-1..1). */
  moveX: number; moveY: number;
}
export const idleFrame = (dt = 1 / 60): EditorFrame => ({ dt, toggle: false, pick: false, back: false, prev: false, next: false,
  delete: false, ground: false, turnLeft: 0, turnRight: 0, up: false, down: false, left: false, right: false, lidar: false, moveX: 0, moveY: 0 });

export type Command =
  | { type: 'enter' } | { type: 'exit' } | { type: 'closeSession' }
  | { type: 'pick' } | { type: 'drop' } | { type: 'cancel' } | { type: 'undo' } | { type: 'delete' }
  | { type: 'cycle'; step: number }
  | { type: 'move'; right: number; forward: number }
  | { type: 'turn'; radians: number } | { type: 'height'; metres: number } | { type: 'ground' }
  | { type: 'reason'; index: number }
  | { type: 'lidar'; toggle?: true; opacity?: number; size?: number };

export const EDITOR = {
  /** Hold the toggle this long (s) in the mode to close the session (previews cleared, session.json ended). */
  closeHold: 1.5,
  /** Hold △ this long (s) to delete. */
  deleteHold: 1.0,
  /** After a commit the D-pad tags a reason for this long (s). */
  reasonWindow: 2.0,
  /** Smooth turn at full trigger (rad/s), the step of L1/R1 and of D-pad ←→ (rad). */
  turnSpeed: Math.PI / 2, step: Math.PI / 12, fine: Math.PI / 180,
  /** Height step (m) and the repeat interval (s) of a held D-pad ↑↓. */
  heightStep: 0.05, repeat: 0.12,
  /** Left-stick dead zone after the stick curve. */
  deadZone: 0.05,
} as const;

/** Reason tags of the D-pad window (README.md); index = ↑ 0, → 1, ↓ 2, ← 3. */
export const REASONS = ['on_road_or_path', 'wrong_facing', 'does_not_exist', 'misaligned_with_lidar'] as const;
export type Reason = typeof REASONS[number];

export class EditorMachine {
  active = false;
  holding = false;
  /** Seconds △ has been held toward a delete (0 when released). */
  deleteHeld = 0;
  /** Seconds the toggle has been held while active. */
  toggleHeld = 0;
  /** Seconds left in the reason window (0: closed). */
  reasonLeft = 0;
  lidarHeld = false;
  private toggleDown = false;
  private toggleArmed = false;
  private deleteDone = false;
  private heightHeld = 0;
  private heightNext = 0;
  private lidarUsed = false;
  private lidarDown = false;

  /** A commit opened the reason window. */
  committed() { this.reasonLeft = EDITOR.reasonWindow; }
  /** The object under the crosshair was picked (or the pick was refused). */
  setHolding(holding: boolean) { this.holding = holding; this.deleteHeld = 0; this.deleteDone = false; }

  step(f: EditorFrame): Command[] {
    const out: Command[] = [];
    // Toggle: enter on press; while active, a short press leaves, a long hold closes the session (once).
    if (f.toggle && !this.toggleDown) {
      this.toggleDown = true;
      if (!this.active) { this.active = true; this.toggleArmed = false; out.push({ type: 'enter' }); }
      else { this.toggleArmed = true; this.toggleHeld = 0; }
    } else if (f.toggle && this.toggleArmed) {
      this.toggleHeld += f.dt;
      if (this.toggleHeld >= EDITOR.closeHold) {
        this.toggleArmed = false; this.toggleHeld = 0;
        this.reset(); out.push({ type: 'closeSession' }, { type: 'exit' });
        return out;
      }
    } else if (!f.toggle && this.toggleDown) {
      this.toggleDown = false;
      if (this.toggleArmed) {
        this.toggleArmed = false; this.toggleHeld = 0;
        if (this.holding) out.push({ type: 'cancel' });
        this.reset(); out.push({ type: 'exit' });
        return out;
      }
    }
    if (!this.active) return out;
    this.reasonLeft = Math.max(0, this.reasonLeft - f.dt);

    // Delete: hold △ (either state); fires once per hold.
    if (f.delete && !this.deleteDone) {
      this.deleteHeld += f.dt;
      if (this.deleteHeld >= EDITOR.deleteHold) { this.deleteDone = true; this.deleteHeld = 0; out.push({ type: 'delete' }); }
    } else if (!f.delete) { this.deleteHeld = 0; this.deleteDone = false; }

    // Point cloud: Create toggles on release unless the D-pad adjusted it while held.
    if (f.lidar) { if (!this.lidarDown) { this.lidarDown = true; this.lidarUsed = false; } }
    else if (this.lidarDown) { this.lidarDown = false; if (!this.lidarUsed) out.push({ type: 'lidar', toggle: true }); }
    this.lidarHeld = this.lidarDown;
    if (this.lidarDown) {
      const opacity = (f.up ? 0.1 : 0) - (f.down ? 0.1 : 0), size = (f.right ? 1 : 0) - (f.left ? 1 : 0);
      if (opacity || size) { this.lidarUsed = true; out.push({ type: 'lidar', ...(opacity ? { opacity } : {}), ...(size ? { size } : {}) }); }
      return out;
    }

    if (!this.holding) {
      if (this.reasonLeft > 0) {
        const index = f.up ? 0 : f.right ? 1 : f.down ? 2 : f.left ? 3 : -1;
        if (index >= 0) { this.reasonLeft = 0; out.push({ type: 'reason', index }); }
      }
      if (f.prev) out.push({ type: 'cycle', step: -1 });
      if (f.next) out.push({ type: 'cycle', step: 1 });
      if (f.pick) { this.reasonLeft = 0; out.push({ type: 'pick' }); }
      else if (f.back) { this.reasonLeft = 0; out.push({ type: 'undo' }); }
      return out;
    }

    // Holding.
    if (f.pick) { out.push({ type: 'drop' }); return out; }
    if (f.back) { out.push({ type: 'cancel' }); return out; }
    const mx = Math.abs(f.moveX) > EDITOR.deadZone ? f.moveX : 0, my = Math.abs(f.moveY) > EDITOR.deadZone ? f.moveY : 0;
    if (mx || my) out.push({ type: 'move', right: mx * f.dt, forward: my * f.dt });
    // three.js yaw is counter-clockwise from above: R2 (right) turns clockwise.
    const turn = (f.turnLeft - f.turnRight) * EDITOR.turnSpeed * f.dt
      + (f.prev ? EDITOR.step : 0) - (f.next ? EDITOR.step : 0) + (f.left ? EDITOR.fine : 0) - (f.right ? EDITOR.fine : 0);
    if (turn) out.push({ type: 'turn', radians: turn });
    if (f.ground) out.push({ type: 'ground' });
    return out;
  }

  /** D-pad ↑↓ height while holding: a press steps at once, a held button repeats after 0.35 s. Levels, not edges. */
  height(upHeld: boolean, downHeld: boolean, dt: number): Command | null {
    if (!this.active || !this.holding || this.lidarHeld || upHeld === downHeld) { this.heightHeld = 0; return null; }
    const command: Command = { type: 'height', metres: upHeld ? EDITOR.heightStep : -EDITOR.heightStep };
    const first = this.heightHeld === 0;
    this.heightHeld += dt;
    if (first) { this.heightNext = 0.35; return command; }
    if (this.heightHeld < this.heightNext) return null;
    this.heightNext += EDITOR.repeat;
    return command;
  }

  reset() { this.active = false; this.holding = false; this.deleteHeld = 0; this.reasonLeft = 0; this.deleteDone = false; this.lidarHeld = false; this.lidarDown = false; }
}
