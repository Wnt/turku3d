import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { ResponseCatalogue, parseMapCoordinate, inventoryState } from "./gmaps_capture_state.mjs";
const privateTools = process.env.GMAPS_TOOLS;
if (!privateTools) throw new Error("GMAPS_TOOLS must name the installed private decoder tools");
const { chromium } = createRequire(path.join(privateTools, "package.json"))("playwright");

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
      .update(fs.readFileSync(fileURLToPath(import.meta.url)))
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
  let anchor = shared.anchor;
  let panCalibration = shared.panCalibration;
  const saveRecord = () => {
    const temporary = path.join(out, `.capture-${process.pid}.tmp`);
    fs.writeFileSync(temporary, JSON.stringify(record, null, 2), {
      mode: 0o600,
    });
    fs.renameSync(temporary, path.join(out, "capture.json"));
  };
  saveRecord();
  try {
    const { chromeExecutable, chromeArgs, chromeIgnoreDefaultArgs } = await import(
      path.join(process.env.REPO, "tools/dev/chrome-args.mjs")
    );
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
    const pose = () => {
      const match = /@(-?[0-9.]+),(-?[0-9.]+),([0-9.]+)a,/.exec(page.url());
      return match
        ? {
            lat: Number(match[1]),
            lon: Number(match[2]),
            altitude: Number(match[3]),
            heading: Number(/,([0-9.]+)h/.exec(page.url())?.[1] ?? 0),
            tilt: Number(/,([0-9.]+)t/.exec(page.url())?.[1] ?? 0),
          }
        : null;
    };
    const distance = (a, b) => Math.hypot(...metres(a, b));
    const nativeMapPoint = async () => {
      await page.keyboard.press("Escape");
      await page.waitForTimeout(150);
      await page.mouse.click(720, 450, { button: "right" });
      const coordinate = page.getByText(/^-?\d{1,2}\.\d{4,},\s*-?\d{1,3}\.\d{4,}$/).first();
      await coordinate.waitFor({ state: "visible", timeout: 5000 });
      const point = parseMapCoordinate(await coordinate.innerText());
      if (!point) throw new Error("Native visible coordinate unavailable");
      record.target_probes ??= [];
      record.target_probes.push({ point, url: page.url(), at: new Date().toISOString() });
      if (record.target_probes.length <= 2)
        await page.screenshot({path: path.join(out, `target-menu-${record.target_probes.length}.jpg`)});
      await page.keyboard.press("Escape");
      try { await coordinate.waitFor({state: "hidden", timeout: 1000}); }
      catch {
        await page.mouse.click(680, 420);
        await coordinate.waitFor({state: "hidden", timeout: 3000});
      }
      saveRecord();
      return point;
    };
    const metres = (a, b) => [
      (b.lon - a.lon) * 111320 * Math.cos((a.lat * Math.PI) / 180),
      (b.lat - a.lat) * 111320,
    ];
    // Wait for the native URL to settle after a gesture, rather than sleeping for
    // every camera reload. Data loading has its own independent settle check.
    const cameraSettled = async (before) => {
      const started = Date.now();
      let last = before;
      let changed = started;
      while (Date.now() - started < 2200) {
        await page.waitForTimeout(150);
        const current = page.url();
        if (current !== last) {
          last = current;
          changed = Date.now();
        }
        if (current !== before && Date.now() - changed >= 300) return;
      }
    };
    const drag = async (dx, dy, rotate = true) => {
      await page.keyboard.press("Escape");
      const x = 720;
      const y = 450;
      const before = page.url();
      await page.mouse.move(x, y);
      if (rotate) await page.keyboard.down("Shift");
      await page.mouse.down();
      await page.mouse.move(x + dx, y + dy, { steps: 8 });
      await page.mouse.up();
      if (rotate) await page.keyboard.up("Shift");
      await cameraSettled(before);
    };
    let navigationCount = 0;
    const orient = async (view) => {
      for (let turn = 0; turn < 3; turn++) {
        const delta = pose().tilt - view.tilt;
        if (Math.abs(delta) < 4) break;
        await drag(0, Math.max(-180, Math.min(180, delta * 6)));
      }
      let pixelsPerDegree = 3.8;
      for (let turn = 0; turn < 5; turn++) {
        const previous = pose().heading;
        const delta = ((view.heading - previous + 540) % 360) - 180;
        if (Math.abs(delta) < 8) break;
        const dx = Math.max(-300, Math.min(300, delta * pixelsPerDegree));
        await drag(dx, 0);
        const changed = ((pose().heading - previous + 540) % 360) - 180;
        if (Math.abs(changed) > 1)
          pixelsPerDegree = Math.max(1, Math.min(12, Math.abs(dx / changed)));
      }
    };
    const pan = async (dx, dy) => {
      const before = anchor;
      await drag(dx, dy, false);
      anchor = await nativeMapPoint();
      const movement = metres(before, anchor);
      return movement;
    };
    const panTo = async (target) => {
      if (distance(anchor, target) < 4) return 0;
      // Reuse calibration while heading, tilt and zoom remain comparable.
      // Camera changes invalidate it; ordinary nearby pans refine it below.
      const current = pose();
      const reusable =
        panCalibration &&
        Math.abs(
          ((current.heading - panCalibration.pose.heading + 540) % 360) - 180,
        ) < 8 &&
        Math.abs(current.tilt - panCalibration.pose.tilt) < 4 &&
        current.altitude / panCalibration.pose.altitude > 0.8 &&
        current.altitude / panCalibration.pose.altitude < 1.25;
      let a, b, c, d;
      let calibrationGestures = 0;
      if (reusable) {
        ({ a, b, c, d } = panCalibration);
      } else {
        // Two normal map drags measure the local screen/world Jacobian.
        const east = await pan(45, 0);
        const north = await pan(0, 45);
        a = east[0] / 45;
        b = north[0] / 45;
        c = east[1] / 45;
        d = north[1] / 45;
        calibrationGestures = 2;
      }
      for (let turn = 0; turn < 40; turn++) {
        const [e, n] = metres(anchor, target);
        if (Math.hypot(e, n) < 4) {
          panCalibration = { a, b, c, d, pose: pose() };
          return turn + calibrationGestures;
        }
        const det = a * d - b * c;
        if (Math.abs(det) < 1e-5) {
          panCalibration = undefined;
          return turn + calibrationGestures;
        }
        let dx = (d * e - b * n) / det;
        let dy = (-c * e + a * n) / det;
        const scale = Math.min(0.65, 200 / Math.max(Math.abs(dx), Math.abs(dy)));
        dx *= scale;
        dy *= scale;
        const [movedE, movedN] = await pan(dx, dy);
        // Rank-one feedback adapts the Jacobian as perspective changes locally.
        const norm = dx * dx + dy * dy;
        const errorE = movedE - a * dx - b * dy;
        const errorN = movedN - c * dx - d * dy;
        a += (errorE * dx) / norm;
        b += (errorE * dy) / norm;
        c += (errorN * dx) / norm;
        d += (errorN * dy) / norm;
      }
      // A nearby view still captures useful nodes. Record the residual and let
      // metadata decide what remains; do not lose a session over a camera target.
      panCalibration = undefined;
      return 6 + calibrationGestures;
    };
    const zoomTo = async (view) => {
      for (let turn = 0; turn < 4; turn++) {
        const height = pose().altitude;
        if (height >= view.altitude * 0.6 && height <= view.altitude * 1.5)
          break;
        const before = page.url();
        const zoom = page.getByRole("button", {
          name: height > view.altitude ? "Zoom in" : "Zoom out",
          exact: true,
        });
        // Native collision/terrain limits can disable zoom above a roof. That
        // is a real UI limit, not a reason to wait fifteen seconds and fail.
        if (!(await zoom.isEnabled())) {
          return true;
        }
        await zoom.click();
        await cameraSettled(before);
      }
      return false;
    };
    const visit = async (view) => {
      const started = Date.now();
      if (anchor) {
        await ensureGlobe();
        anchor = await nativeMapPoint();
      }
      const responseStart = record.responses.length;
      let gestures = 0;
      let zoomLimited = false;
      let method = "same-tab pan";
      if (!anchor) {
        // Only the first detail view navigates. Subsequent views retain the SPA,
        // WebGL scene and decoded client cache instead of starting them again.
        await page.goto(
          `https://www.google.com/maps/@${view.lat},${view.lon},${view.altitude}m/data=!3m1!1e3?hl=en`,
          { waitUntil: "domcontentloaded", timeout: 30000 },
        );
        navigationCount++;
        await page.locator("canvas").first().waitFor({ timeout: 15000 });
        await ensureGlobe();
        await orient(view);
        zoomLimited = await zoomTo(view);
        anchor = await nativeMapPoint();
        gestures = await panTo(view);
        method = "session initialization";
      } else {
        // Keep the URL position at the requested coordinates after every native
        // camera transform; metadata independently certifies actual coverage.
        await orient(view);
        zoomLimited = await zoomTo(view);
        // Rotation and zoom also move the native URL position; discard integrated drift.
        anchor = await nativeMapPoint();
        if (distance(anchor, view) > 200) {
          await zoomTo({ altitude: 180 });
          anchor = await nativeMapPoint();
          gestures = await panTo(view);
          zoomLimited = await zoomTo(view);
          anchor = await nativeMapPoint();
          gestures += await panTo(view);
        } else gestures = await panTo(view);
        for (let retry = 0; distance(anchor, view) > 30 && retry < 3; retry++) {
          panCalibration = undefined;
          await zoomTo({ altitude: 180 });
          anchor = await nativeMapPoint();
          gestures += await panTo(view);
          zoomLimited = await zoomTo(view);
          anchor = await nativeMapPoint();
          gestures += await panTo(view);
        }
        if (distance(anchor, view) > 40) {
          throw new Error(
            `Pan residual ${distance(anchor, view).toFixed(1)} m; navigation recovery disabled for batch capture`,
          );
        }
      }
      anchor = await nativeMapPoint();
      for (let retry = 0; distance(anchor, view) >= 5 && retry < 2; retry++) {
        panCalibration = undefined;
        await orient({...view, heading: (view.heading + 90 * (retry + 1)) % 360});
        anchor = await nativeMapPoint();
        gestures += await panTo(view);
      }
      if (distance(anchor, view) >= 5)
        throw new Error(`Visible target error ${distance(anchor, view).toFixed(2)} m exceeds 5 m`);
      const loading = await settle();
      const mapMode = page.getByRole("switch", {
        name: "Tilt the view",
        exact: true,
      });
      if (!(await mapMode.isVisible())) {
        record.ui = await page.locator("body").innerText();
        throw new Error(
          "Native client left the 3D map; refusing photo/blank overlay as capture",
        );
      }
      const actual = pose();
      if (!actual || actual.altitude <= 0 || actual.altitude >= 200)
        throw new Error("Close camera fell back to a distant or flat view");
      record.display = await page.evaluate(() => ({
        devicePixelRatio: window.devicePixelRatio,
        canvases: [...document.querySelectorAll("canvas")].map((c) => ({
          width: c.width,
          height: c.height,
          cssWidth: c.clientWidth,
          cssHeight: c.clientHeight,
        })),
      }));
      record.navigation = {
        method: "persistent SPA with calibrated map drags",
        navigations: navigationCount,
      };
      return {
        actual,
        timing: {
          method,
          loading,
          seconds: (Date.now() - started) / 1000,
          pan_gestures: gestures,
          native_zoom_limited: zoomLimited,
          tracked_target_residual_m: distance(anchor, view),
          verified_visible_point: anchor,
          target_error_m: distance(anchor, view),
          new_response_records: record.responses.length - responseStart,
        },
      };
    };
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
    shared.anchor = anchor;
    shared.panCalibration = panCalibration;
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
    shared.anchor = anchor;
    shared.panCalibration = panCalibration;
    if (!invocation.batch) await context?.close();
    await cdp?.detach().catch(() => {});
    await Promise.allSettled([...pending]);
    record.finished = new Date().toISOString();
    Object.assign(session, {
      finished: record.finished,
      seconds: (Date.now() - sessionStarted) / 1000,
      visits: record.detail_visits.length - session.initial_visits,
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
