import { Room } from '../room.js';

export function fixture(start = '2026-10-27T12:00:00Z') {
  let time = Date.parse(start);
  const records = [];
  const store = { write: (hour, players) => records.push({ hour, players }), prune() {} };
  const room = new Room({ store, now: () => time });
  function connect(session) {
    const messages = [], closes = [];
    const c = room.connect(data => messages.push(JSON.parse(data)), (...args) => closes.push(args));
    const send = m => room.receive(c, JSON.stringify({ v: 1, ...m }));
    send({ type: 'hello', ...(session ? { session } : {}) });
    return { c, messages, closes, send, get welcome() { return messages.find(m => m.type === 'welcome'); } };
  }
  return { room, records, connect, advance: ms => { time += ms; room.tick(); },
    set: date => { time = Date.parse(date); room.tick(); } };
}

export const pose = { type: 'pose', t: 1, E: 173, N: -252, H: 4, yaw: 0,
  vE: 1, vN: 0, vH: 0, mode: 'foot', vehicle: null, carrying: false };
export const delivery = { type: 'delivery', orderId: 'order-1:stop-0', addressId: 'test-address',
  payout: 6, tip: 8, routeMeters: 120 };
