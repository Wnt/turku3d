import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { ResponseCatalogue, inventoryState } from "./gmaps_capture_state.mjs";
const privateTools = process.env.GMAPS_TOOLS;
if (!privateTools) throw new Error("GMAPS_TOOLS must name the installed private decoder tools");
const { chromium } = createRequire(path.join(privateTools, "package.json"))("playwright");

import {createNavigation} from './gmaps_capture_navigation.mjs';
import {chromeExecutable, chromeArgs, chromeIgnoreDefaultArgs} from '../dev/chrome-args.mjs';

const invocation = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const shared = {};
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, async () => {
    shared.stop = true;
    await shared.context?.close();
  });
}
async function capture(config) {
  const root = path.resolve(process.env.TURKU3D_WORK, "gmaps-round3");
  const out = path.resolve(config.output);
  if (!out.startsWith(`${root}/`) || out.includes(".."))
    throw new Error("Private data root required");
  if (
    !process.env.TURKU3D_BROWSER_DIR ||
    process.env.TURKU3D_SANDBOX === "main"
  )
    throw new Error("Sandbox required");
  fs.mkdirSync(path.join(out, "responses"), { recursive: true, mode: 0o700 });
  const record = config.resume
    ? JSON.parse(fs.readFileSync(path.join(out, "capture.json"), "utf8"))
    : {
        tile: config.tile,
        extent_en: config.extent_en,
        coverage_id: config.coverage_id ?? config.tile,
        started: new Date().toISOString(),
        status: "capturing",
        responses: [],
        errors: [],
      };
  shared.catalogue ??= new ResponseCatalogue(path.join(root, "sessions", config.profile));
  shared.catalogue.merge(record, out);
  record.missing_node_policy = {
    authorization: "gmaps-capture-round3 brief: <=5% masked required nodes",
    max_missing_fraction: 0.05, mask_missing_nodes: true,
  };
  record.inventory_state = "planned";
  record.status = "capturing";
  delete record.error;
  delete record.finished;
  record.detail_visits ??= [];
  record.capture_sessions ??= [];
  record.capture_sessions.push(new Date().toISOString());
  const sessionStarted = Date.now();
  const session = {
    started: record.capture_sessions.at(-1),
    capture_code_sha256: crypto
      .createHash("sha256")
      .update(["gmaps_capture.mjs", "gmaps_capture_navigation.mjs", "gmaps_capture_state.mjs", "gmaps_capture_plan.py"]
        .map(name => fs.readFileSync(fileURLToPath(new URL(name, import.meta.url)))).join("\n"))
      .digest("hex"),
    initial_visits: record.detail_visits.length,
    initial_progress: record.detail_progress,
  };
  record.session_statistics ??= [];
  record.session_statistics.push(session);
  const pending = new Set();
  const deadline = Math.min(
    Infinity,
    invocation.deadline_ms ?? Infinity,
  );
  const initialVisits = record.detail_visits.length;
  let context;
  let cdp;
  let navigation;
  const saveRecord = () => {
    const temporary = path.join(out, `.capture-${process.pid}.tmp`);
    fs.writeFileSync(temporary, JSON.stringify(record, null, 2), {
      mode: 0o600,
    });
    fs.renameSync(temporary, path.join(out, "capture.json"));
  };
  saveRecord();
  try {
    context =
      shared.context ??
      (await chromium.launchPersistentContext(
        path.join(
          process.env.TURKU3D_BROWSER_DIR,
          config.profile ?? "gmaps-extract",
        ),
        {
          executablePath: chromeExecutable(),
          ignoreDefaultArgs: chromeIgnoreDefaultArgs,
          headless: config.headless ?? false,
          viewport: { width: 1280, height: 900 },
          deviceScaleFactor: 2,
          locale: "en-US",
          serviceWorkers: "block",
          args: chromeArgs([
            "--use-angle=metal",
            "--enable-gpu",
            "--ignore-gpu-blocklist",
            "--disk-cache-size=104857600",
            "--window-position=-3000,-3000",
          ]),
        },
      ));
    shared.context = context;
    const page = context.pages()[0];
    page.setDefaultTimeout(15000);
    const ensureGlobe = async () => {
      const mode = page.getByRole("switch", {
        name: "Tilt the view",
        exact: true,
      });
      if (!(await mode.isVisible())) {
        await page.keyboard.press("Escape");
        const close = page.getByRole("button", { name: "Close", exact: true });
        if (await close.isVisible()) {
          await close.click();
          await page.waitForTimeout(1000);
          record.photo_overlay_exits = (record.photo_overlay_exits ?? 0) + 1;
        }
      }
      if (!(await mode.isVisible())) {
        await page.getByRole("button", { name: "Layers", exact: true }).hover();
        await page.getByRole("button", { name: "More", exact: true }).click();
        const globe = page.getByRole("checkbox", {
          name: "Globe view",
          exact: true,
        });
        if (!(await globe.isChecked())) await globe.click();
        await page.keyboard.press("Escape");
        await page.waitForTimeout(4000);
      }
      await mode.waitFor({ timeout: 15000 });
      if ((await mode.innerText()).trim() !== "2D") {
        await mode.click();
        await page.waitForTimeout(6000);
      }
      if (
        (await mode.innerText()).trim() !== "2D" ||
        !/,\d+(?:\.\d+)?a,/.test(page.url())
      )
        throw new Error(
          "Close view fell back to flat satellite; refusing metadata completeness claim",
        );
    };
    cdp = await context.newCDPSession(page);
    await cdp.send("Network.enable", {
      maxTotalBufferSize: 256 * 1024 * 1024,
      maxResourceBufferSize: 32 * 1024 * 1024,
    });
    await cdp.send("Network.setCacheDisabled", { cacheDisabled: false });
    // Limit bandwidth for this single targeted camera. Never replay provider requests.
    await cdp.send("Network.emulateNetworkConditions", {
      offline: false,
      latency: 60,
      downloadThroughput: 2 * 1024 * 1024,
      uploadThroughput: 256 * 1024,
    });
    const responses = new Map();
    const dataRequests = new Set();
    let lastBody = Date.now();
    const settle = async () => {
      const started = Date.now();
      const stop = Math.min(started + 45000, deadline - 5000);
      const state = (complete) => ({
        complete,
        seconds: (Date.now() - started) / 1000,
        active_requests: dataRequests.size,
        response_bodies: responses.size,
        body_jobs: pending.size,
        quiet_ms: Date.now() - lastBody,
      });
      while (Date.now() < stop) {
        await page.waitForTimeout(250);
        if (
          Date.now() - started >= 3000 &&
          Date.now() - lastBody >= 1500 &&
          dataRequests.size === 0 &&
          responses.size === 0 &&
          pending.size === 0
        )
          return state(true);
      }
      // A bounded wait is not evidence that loading finished. Keep this target
      // for resumption instead of silently advancing to the next region.
      return state(false);
    };
    cdp.on("Network.requestWillBeSent", (event) => {
      if (
        /\/(?:NodeData|BulkMetadata|PlanetoidMetadata)(?:\/|\?|$)/.test(
          event.request.url,
        )
      ) {
        dataRequests.add(event.requestId);
        lastBody = Date.now();
      }
    });
    cdp.on("Network.loadingFailed", (event) => {
      responses.delete(event.requestId);
      dataRequests.delete(event.requestId);
    });
    cdp.on("Network.responseReceived", (event) => {
      const { response: r } = event;
      if (r.status === 200 && r.mimeType === "application/x-protobuffer") {
        dataRequests.add(event.requestId);
        lastBody = Date.now();
        responses.set(event.requestId, {
          url: r.url,
          mime: r.mimeType,
          type: event.type,
        });
      }
    });
    const responseKeys = new Set(
      record.responses.map((r) => `${r.url}|${r.sha256}`),
    );
    let written = [
      ...new Map(record.responses.map((r) => [r.file, r.bytes])).values(),
    ].reduce((a, b) => a + b, 0);
    const initialBytes = written;
    cdp.on("Network.loadingFinished", (event) => {
      dataRequests.delete(event.requestId);
      const info = responses.get(event.requestId);
      responses.delete(event.requestId);
      if (
        !info ||
        written - initialBytes >= 512 * 1024 * 1024 ||
        event.encodedDataLength > 32 * 1024 * 1024
      )
        return;
      const job = (async () => {
        try {
          lastBody = Date.now();
          const body = await cdp.send("Network.getResponseBody", {
            requestId: event.requestId,
          });
          const data = Buffer.from(
            body.body,
            body.base64Encoded ? "base64" : "utf8",
          );
          if (
            !data.length ||
            written - initialBytes + data.length > 512 * 1024 * 1024
          )
            return;
          const sha = crypto.createHash("sha256").update(data).digest("hex");
          shared.catalogue.add(info, sha, data);
          const file = `responses/${sha}.bin`;
          if (!fs.existsSync(path.join(out, file))) {
            fs.linkSync(path.join(shared.catalogue.root, file), path.join(out, file));
            written += data.length;
          }
          const key = `${info.url}|${sha}`;
          if (!responseKeys.has(key)) {
            responseKeys.add(key);
            record.responses.push({
              ...info,
              file,
              bytes: data.length,
              sha256: sha,
            });
          }
        } catch (error) {
          record.errors.push({
            kind: "response_body",
            message: String(error.message).slice(0, 200),
          });
        }
      })();
      pending.add(job);
      job.finally(() => {
        pending.delete(job);
        lastBody = Date.now();
      });
    });
    navigation = createNavigation({page, record, out, settle, ensureGlobe, saveRecord,
      initialAnchor: shared.anchor, initialCalibration: shared.panCalibration});
    const {visit} = navigation;
    if (!shared.initialized) {
      await page.goto("https://www.google.com/?hl=en", {
        waitUntil: "domcontentloaded",
        timeout: 60000,
      });
      const reject = page.getByRole("button", {
        name: "Reject all",
        exact: true,
      });
      await page.waitForTimeout(2000);
      if (await reject.isVisible()) {
        await reject.click();
        record.consent = "Reject all";
      } else record.consent = "existing preference";
      record.consent_cookie_present = (await context.cookies()).some(
        (c) => c.name === "SOCS",
      );
      if (!record.consent_cookie_present)
        throw new Error("Consent preference not established");
      shared.initialized = true;
    }
    record.status = "captured";
    if (record.status === "captured") {
      if (invocation.batch) {
        const view = {
          E: (config.extent_en?.[0] + config.extent_en?.[2]) / 2,
          N: (config.extent_en?.[1] + config.extent_en?.[3]) / 2,
          lat: config.lat,
          lon: config.lon,
          altitude: 22,
          tilt: 45,
          heading: 0,
        };
        const { actual, timing } = await visit(view);
        record.detail_visits.push({
          ...view,
          effective_url: page.url(),
          actual_camera: actual,
          mode: timing.loading.complete
            ? "verified close 3D"
            : "close 3D loading incomplete",
          timing,
        });
        await page.screenshot({
          path: path.join(out, "initial.jpg"),
          type: "jpeg",
          quality: 85,
        });
        await Promise.allSettled([...pending]);
        saveRecord();
      }
      // Inspect only metadata delivered to this normal browser session. No request replay.
      const detailScript = path.join(process.env.REPO, "tools/refs/gmaps_capture_plan.py");
      const inspect = () =>
        JSON.parse(
          execFileSync(process.env.GMAPS_PYTHON, [detailScript, "plan", out], {
            encoding: "utf8",
            timeout: 30000,
          }),
        );
      const save = () => {
        shared.catalogue.merge(record, out);
        record.bytes = written;
        session.response_bytes_added = written - initialBytes;
        saveRecord();
      };
      let bestLoaded = -1;
      let lastGain = Date.now();
      rounds: for (let round = 0; Date.now() < deadline; round++) {
        await Promise.allSettled([...pending]);
        save();
        const plan = inspect();
        record.detail_visits = plan.observed_history;
        record.detail_progress = plan.summary;
        record.inventory_state = inventoryState(record, plan);
        if (plan.summary.terminal_loaded > bestLoaded) {
          bestLoaded = plan.summary.terminal_loaded;
          lastGain = Date.now();
        }
        if (!plan.complete && Date.now() - lastGain > 120000) {
          record.stop_reason = "stalled terminal-node gain for 120 seconds";
          break;
        }
        if (plan.complete) {
          record.detail_complete = true;
          break;
        }
        const views = record.pending_detail_view
          ? [record.pending_detail_view, ...plan.views]
          : plan.views;
        for (const view of views) {
          if (written - initialBytes >= 500 * 1024 * 1024)
            throw new Error(
              "Session response cap reached before deepest-node completeness",
            );
          if (Date.now() >= deadline) break rounds;
          const { actual, timing } = await visit(view);
          record.detail_visits.push({
            ...view,
            effective_url: page.url(),
            mode: timing.loading.complete
              ? "verified close 3D"
              : "close 3D loading incomplete",
            actual_camera: actual,
            timing,
          });
          if (
            record.detail_visits.length - initialVisits <= 3 ||
            record.detail_visits.length % 6 === 0
          )
            await page.screenshot({
              path: path.join(out, `detail-${record.detail_visits.length}.jpg`),
              type: "jpeg",
              quality: 85,
            });
          await Promise.allSettled([...pending]);
          if (timing.loading.complete) delete record.pending_detail_view;
          else record.pending_detail_view = view;
          save();
          // A loading timeout is not a quarter deadline. Re-audit node gain.
          break;

        }
      }
      await Promise.allSettled([...pending]);
      save();
      const final = inspect();
      record.detail_visits = final.observed_history;
      record.detail_complete = final.complete;
      record.inventory_state = inventoryState(record, final);
      record.stop_reason ??= final.complete ? "masked completeness" : "shared browser budget";
      record.detail_progress = final.summary;
      if (final.complete) delete record.pending_detail_view;
    }
    await Promise.allSettled([...pending]);
    record.bytes = written;
    if (navigation) Object.assign(shared, navigation.state());
  } catch (error) {
    record.status = "failed";
    record.error = String(error.message);
    if (context?.pages()[0]) {
      record.ui = await context
        .pages()[0]
        .locator("body")
        .innerText()
        .catch(() => "unavailable");
      await context
        .pages()[0]
        .screenshot({ path: path.join(out, "failure.png") })
        .catch(() => {});
    }
    process.exitCode = 1;
  } finally {
    if (navigation) Object.assign(shared, navigation.state());
    if (!invocation.batch) await context?.close();
    await cdp?.detach().catch(() => {});
    await Promise.allSettled([...pending]);
    record.finished = new Date().toISOString();
    Object.assign(session, {
      finished: record.finished,
      seconds: (Date.now() - sessionStarted) / 1000,
      visits: record.detail_visits.length - session.initial_visits,
      error: record.error,
      stop_reason: record.stop_reason,
      final_progress: record.detail_progress,
    });
    saveRecord();
    console.log(
      JSON.stringify({
        status: record.status,
        responses: record.responses.length,
        bytes: record.bytes,
        error: record.error,
        output: out,
      }),
    );
  }
}
try {
  for (const request of invocation.batch ?? [invocation]) {
    if (shared.stop || Date.now() >= (invocation.deadline_ms ?? Infinity))
      break;
    await capture(request);
    // Keep incomplete results private, and continue other independent extents.
    process.exitCode = 0;
  }
} finally {
  await shared.context?.close();
}
