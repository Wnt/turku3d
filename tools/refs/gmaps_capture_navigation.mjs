import path from 'node:path';
import {parseMapCoordinate} from './gmaps_capture_state.mjs';

// All steering acceptance uses the native point; URL pose is only orientation/zoom.
export function createNavigation({page, record, out, settle, ensureGlobe, saveRecord,
  initialAnchor, initialCalibration}) {
  let anchor = initialAnchor;
  let panCalibration = initialCalibration;
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
return {visit, state: () => ({anchor, panCalibration})};
}
