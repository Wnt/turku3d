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

export const basePay = (metres, job = 'short') => job === 'long' ? 24 + Math.ceil(metres * 0.012) : BASE_PAY;

export function parSeconds(routeMeters, vehicle, job = 'short') {
  const boost = job === 'long' ? (vehicle === 'scooter' ? 1.2 : 1.4) : 1;
  return routeMeters / (PAR_SPEED[vehicle] * boost) + PAR_HANDLING_SECONDS;
}

export function tip(elapsed, par) {
  const full = TIP_FULL_UNTIL * par, none = TIP_NONE_FROM * par;
  return Math.round(MAX_TIP * Math.min(1, Math.max(0, (none - elapsed) / (none - full))));
}

export function deliveryDeadline(par) { return FAIL_AFTER_PAR * par; }

// Without server-issued orders, validate the exact distance-pay envelope for the declared job.
export function validDelivery({ payout, tip: gratuity, routeMeters, job = 'short' }) {
  return Number.isFinite(routeMeters) && routeMeters >= 0 && routeMeters <= 100_000
    && Number.isInteger(payout) && Number.isInteger(gratuity)
    && gratuity >= 0 && gratuity <= MAX_TIP
    && (job === 'long' ? routeMeters > 60 && routeMeters <= 7000 && payout === basePay(routeMeters, 'long')
      : job === 'short' && (payout === BASE_PAY || (payout === BASE_PAY + DOUBLE_BONUS && gratuity > 0)));
}
