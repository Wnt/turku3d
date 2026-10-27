// Mirror game/src/gameplay/economy.ts without importing TypeScript into the server.
// tests/economy.test.js executes that module and checks the mission hand-over rule.
export const BASE_PAY = 6;
export const MAX_TIP = 8;
export const DOUBLE_BONUS = 4;
export const PAR_HANDLING_SECONDS = 20;
export const PAR_SPEED = Object.freeze({ none: 1.4, scooter: 7, car: 9, truck: 10.5 });
export const TIP_FULL_UNTIL = 0.7;
export const TIP_NONE_FROM = 1.3;
export const FAIL_AFTER_PAR = 2;

export function parSeconds(routeMeters, vehicle) {
  return routeMeters / PAR_SPEED[vehicle] + PAR_HANDLING_SECONDS;
}

export function tip(elapsed, par) {
  const full = TIP_FULL_UNTIL * par, none = TIP_NONE_FROM * par;
  return Math.round(MAX_TIP * Math.min(1, Math.max(0, (none - elapsed) / (none - full))));
}

export function deliveryDeadline(par) { return FAIL_AFTER_PAR * par; }

// Distance changes par time, never the euro bounds. Without elapsed time, vehicle
// ownership or server-issued orders, v1 can only validate the possible pay envelope.
export function validDelivery({ payout, tip: gratuity, routeMeters }) {
  return Number.isFinite(routeMeters) && routeMeters >= 0 && routeMeters <= 100_000
    && Number.isInteger(payout) && Number.isInteger(gratuity)
    && gratuity >= 0 && gratuity <= MAX_TIP
    && (payout === BASE_PAY || (payout === BASE_PAY + DOUBLE_BONUS && gratuity > 0));
}
