// Where does a relocation to the harbour put the car and the camera? BROWSER_CDP=... BASE=... node probe-harbour.mjs
import { connect, log, openGame, sleep, takeCar } from './lib.mjs';
const browser = await connect();
const { context, page } = await openGame(browser, { option: 'new', query: '&traffic=0&walkers=0' });
await takeCar(page);
for (const [x, z] of [[-2400, 1800], [-2364, 1560], [-2048, 1802], [-1383, 1516]]) {
  const r = await page.evaluate(async ([x, z]) => { const out = await Promise.resolve(window.__game.relocateVehicle(x, z, 0)).catch(e => 'error ' + e.message); return String(out); }, [x, z]);
  for (const wait of [3, 6, 12]) {
    await sleep(wait === 3 ? 3000 : wait === 6 ? 3000 : 6000);
    const s = await page.evaluate(() => { const st = window.__game.state(), g = window.__game.audio.snapshot().gulls; return { car: st.vehicle?.pos.map(Math.round), held: st.vehicle?.held, cam: st.camera.pos.map(Math.round), presence: g.presence, nextIn: g.nextIn, mode: st.mode, loading: st.loading }; });
    log(`to ${x},${z} (${r}) after ${wait} s`, JSON.stringify(s));
  }
}
await context.close(); process.exit(0);
