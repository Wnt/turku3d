import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {installNativeTransport, requestsFromPlan} from './gmaps_synthetic_transport.mjs';
import {fileURLToPath} from 'node:url';
import {ResponseCatalogue, inventoryState} from './gmaps_capture_state.mjs';
import {chromeExecutable, chromeArgs, chromeIgnoreDefaultArgs} from '../dev/chrome-args.mjs';

const tools = process.env.GMAPS_TOOLS;
const {chromium} = createRequire(path.join(tools, 'package.json'))('playwright');
const invocation = JSON.parse(fs.readFileSync(process.argv[2]));
const root = path.join(process.env.GMAPS_ROOT || path.join(process.env.TURKU3D_WORK, 'gmaps-round3'), 'synthetic');
const codeHash = crypto.createHash('sha256').update(['gmaps_synthetic.mjs', 'gmaps_synthetic_transport.mjs', 'gmaps_synthetic_plan.py', 'gmaps_capture_state.mjs'].map(name => fs.readFileSync(fileURLToPath(new URL(name, import.meta.url)))).join('\n')).digest('hex');
const catalogue = new ResponseCatalogue(path.join(root, 'sessions', invocation.session));
const browserStarted = Date.now();
const context = await chromium.launchPersistentContext(
  path.join(process.env.TURKU3D_BROWSER_DIR, invocation.session), {
    executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs,
    headless: true, viewport: {width: 1280, height: 900}, locale: 'en-US',
    serviceWorkers: 'block', args: chromeArgs(['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist']),
  });
let stopped = false;
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, async () => {
  stopped = true;
  await context.close();
});
const page = context.pages()[0];
// Preserve the transport and request headers used by the native Maps client.
// No credentials or undocumented keys are invented, and no server-side replay occurs.
await page.addInitScript(installNativeTransport);
const cdp = await context.newCDPSession(page);
await cdp.send('Network.enable', {maxTotalBufferSize: 256*1024*1024, maxResourceBufferSize: 32*1024*1024});
const requests = new Map();
const pending = new Set();
let responseErrors = [];
cdp.on('Network.responseReceived', event => {
  const response = event.response;
  if (response.status === 200 && response.mimeType === 'application/x-protobuffer')
    requests.set(event.requestId, {url: response.url, mime: response.mimeType, type: event.type});
});
cdp.on('Network.loadingFinished', event => {
  const info = requests.get(event.requestId);
  requests.delete(event.requestId);
  if (!info) return;
  const job = (async () => {
    try {
      const body = await cdp.send('Network.getResponseBody', {requestId: event.requestId});
      const data = Buffer.from(body.body, body.base64Encoded ? 'base64' : 'utf8');
      if (data.length) catalogue.add(info, crypto.createHash('sha256').update(data).digest('hex'), data);
    } catch (error) {responseErrors.push(String(error.message));}
  })();
  pending.add(job);
  job.finally(() => pending.delete(job));
});
cdp.on('Network.loadingFailed', event => requests.delete(event.requestId));
try {
  await page.goto('https://www.google.com/?hl=en', {waitUntil: 'domcontentloaded'});
  await page.waitForTimeout(1500);
  const reject = page.getByRole('button', {name: 'Reject all', exact: true});
  if (await reject.isVisible()) await reject.click();
  const first = invocation.quarters[0];
  await page.goto(`https://www.google.com/maps/@${first.lat},${first.lon},22m/data=!3m1!1e3?hl=en`, {waitUntil:'domcontentloaded'});
  const mode = page.getByRole('switch', {name: 'Tilt the view', exact: true});
  if (!(await mode.isVisible())) {
    await page.getByRole('button', {name:'Layers', exact:true}).hover();
    await page.getByRole('button', {name:'More', exact:true}).click();
    const globe = page.getByRole('checkbox', {name:'Globe view', exact:true});
    if (!(await globe.isChecked())) await globe.click();
    await page.keyboard.press('Escape');
  }
  await mode.waitFor({timeout:30000});
  if ((await mode.innerText()).trim() !== '2D') await mode.click();
  await page.waitForFunction(() => window.__gmapsTemplates?.NodeData && window.__gmapsTemplates?.BulkMetadata, {timeout:30000});
  for (let check=0; (await mode.innerText()).trim() !== '2D' && check<40; check++) await page.waitForTimeout(250);
  if ((await mode.innerText()).trim() !== '2D') throw new Error('Native 3D mode not active');
  for (const quarter of invocation.quarters) {
    if (stopped || Date.now() >= invocation.deadline_ms) break;
    const out = path.join(root, 'captures', quarter.coverage_id);
    fs.mkdirSync(path.join(out, 'responses'), {recursive:true, mode:0o700});
    const started = Date.now();
    const record = {...quarter, responses: [], errors: [], detail_visits: [],
      native_initialization_seconds:(started-browserStarted)/1000,
      capture_code_sha256:codeHash, capture_mode:'synthetic native page XHR', started: new Date().toISOString(), status:'capturing',
      inventory_state:'planned', missing_node_policy: {authorization:'Explicit round-3 <=5% masked policy',
        max_missing_fraction:0.05, mask_missing_nodes:true}};
    const save = () => {
      catalogue.merge(record, out);
      const tmp = path.join(out, `capture-${process.pid}.tmp`);
      fs.writeFileSync(tmp, JSON.stringify(record,null,2),{mode:0o600});
      fs.renameSync(tmp,path.join(out,'capture.json'));
    };
    let lastGain = Date.now();
    let best = -1;
    let lastPlan;
    record.request_batches = [];
    while (!stopped && Date.now() < invocation.deadline_ms) {
      await Promise.allSettled([...pending]);
      save();
      let plan;
      try {
        plan = JSON.parse(execFileSync(process.env.GMAPS_PYTHON,
          [path.join(process.env.REPO, 'tools/refs/gmaps_synthetic_plan.py'), out],
          {encoding:'utf8',timeout:30000,stdio:['ignore','pipe','pipe']}));
      } catch (error) {
        if (Date.now()-started > 30000) throw error;
        await page.waitForTimeout(500);
        continue;
      }
      lastPlan = plan;
      record.detail_progress = plan;
      record.inventory_state = inventoryState(plan);
      if (plan.complete) {record.inventory_state='eligible'; record.stop_reason='masked completeness'; break;}
      const gain = plan.loaded + plan.available - plan.frontier.length;
      if (gain > best) {best=gain;lastGain=Date.now();}
      if (Date.now()-lastGain > 30000) {record.stop_reason='stalled advertised-node gain'; break;}
      // Resolve metadata before requesting its terminal payloads. Epochs come from metadata.
      const todo = requestsFromPlan(plan);
      if (!todo.length) {record.stop_reason='no advertised requests';break;}
      const batchStart=Date.now();
      const result = await page.evaluate(async tasks => {
        const results=[];
        let cursor=0;
        await Promise.all(Array.from({length:Math.min(16,tasks.length)},async()=>{
          while(cursor<tasks.length) {
            const task=tasks[cursor++];
            try {results.push(await window.__gmapsRequest(task.kind,task.node,task.epoch));}
            catch(error) {results.push({...task,error:String(error.message)});}
          }
        }));
        return results;
      }, todo);
      record.request_batches.push({seconds:(Date.now()-batchStart)/1000,requests:todo.length,
        errors:result.filter(r=>r.error),bytes:result.reduce((sum,r)=>sum+(r.bytes??0),0)});
      if (result.every(r=>r.error)) {record.stop_reason='native transport blocked';break;}
      // Ensure CDP has received loadingFinished after the page's XHR load callbacks.
      await page.waitForTimeout(100);
    }
    await Promise.allSettled([...pending]);
    record.errors=responseErrors;
    responseErrors=[];
    record.finished=new Date().toISOString();
    record.status=record.inventory_state==='eligible'?'captured':'incomplete';
    record.session_statistics=[{started:record.started,finished:record.finished,
      seconds:(Date.now()-started)/1000,visits:0,final_progress:lastPlan}];
    save();
    console.log(JSON.stringify({quarter:quarter.coverage_id,state:record.inventory_state,
      seconds:(Date.now()-started)/1000,from_browser_launch_seconds:(Date.now()-browserStarted)/1000,available:lastPlan?.available,loaded:lastPlan?.loaded,
      missing:lastPlan?.missing,reason:record.stop_reason}));
  }
} finally {
  await context.close();
}
