// Where does a relocation to the harbour put the car and the camera, and how long does the world load there?
//   BROWSER_CDP=... BASE=... OPTION=new|multiplayer node probe-harbour.mjs
import { connect, log, openGame, sleep, takeCar } from './lib.mjs';
const browser = await connect();
const option = process.env.OPTION ?? 'new';
const { context, page } = await openGame(browser, { option, query: `&traffic=0&walkers=0${option === 'multiplayer' ? `&room=sf-probe-${Date.now() % 100000}` : ''}` });
await takeCar(page);
const r = await page.evaluate(async () => String(await Promise.resolve(window.__game.relocateVehicle(-2364, 1560, 0)).catch(e => 'error ' + e.message)));
for (let t = 0; t <= 90; t += 6) {
  const s = await page.evaluate(() => { const st = window.__game.state(), g = window.__game.audio.snapshot().gulls; return { car: st.vehicle?.pos.map(Math.round), cam: st.camera.pos.map(Math.round), presence: g.presence, nextIn: g.nextIn, calls: g.calls, mode: st.mode, loading: st.loading, settled: st.tilesSettled }; });
  log(`harbour (${r}) after ${t} s`, JSON.stringify(s));
  if (s.calls > 1) break;
  await sleep(6000);
}
await context.close(); process.exit(0);
