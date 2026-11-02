// CPU orthographic rasterizer for impostor baking: alpha-tested, textured, z-buffered, supersampled.
// Writes albedo (texture x vertex colour), coverage alpha and a view-space normal per texel.
import { Img } from './image.mjs';

/** Bilinear sample with wrap in both axes (bark V repeats; leaf cards stay inside their cell). */
function tex(img, u, v, out) {
  const x = (u - Math.floor(u)) * img.w - 0.5, y = (v - Math.floor(v)) * img.h - 0.5;
  const x0 = Math.floor(x), y0 = Math.floor(y), fx = x - x0, fy = y - y0;
  out[0] = out[1] = out[2] = out[3] = 0;
  for (let j = 0; j < 2; j++) for (let i = 0; i < 2; i++) {
    const px = ((x0 + i) % img.w + img.w) % img.w, py = ((y0 + j) % img.h + img.h) % img.h;
    const w = (i ? fx : 1 - fx) * (j ? fy : 1 - fy), k = (py * img.w + px) * 4;
    out[0] += img.data[k] * w; out[1] += img.data[k + 1] * w; out[2] += img.data[k + 2] * w; out[3] += img.data[k + 3] * w;
  }
  return out;
}

/**
 * view: { right, up, toward (unit, from the object towards the camera), centre [x,y,z] of the image, width, height (m) }.
 * parts: [{ mesh: MeshBuilder, texture: Img, cutout: bool }]. Returns { albedo: Img (rgb + coverage), normal: Img }.
 */
export function bake(parts, view, W, H, ss = 3) {
  const SW = W * ss, SH = H * ss, depth = new Float32Array(SW * SH).fill(-Infinity);
  const col = new Float32Array(SW * SH * 3), nrm = new Float32Array(SW * SH * 3), cov = new Uint8Array(SW * SH);
  const s = [0, 0, 0, 0];
  for (const { mesh, texture, cutout } of parts) {
    const P = mesh.positions, N = mesh.normals, UV = mesh.uvs, C = mesh.colors, I = mesh.indices;
    const proj = new Float32Array(P.length);
    for (let i = 0; i < P.length / 3; i++) {
      const x = P[i * 3] - view.centre[0], y = P[i * 3 + 1] - view.centre[1], z = P[i * 3 + 2] - view.centre[2];
      proj[i * 3] = ((x * view.right[0] + y * view.right[1] + z * view.right[2]) / view.width + 0.5) * SW;
      proj[i * 3 + 1] = (0.5 - (x * view.up[0] + y * view.up[1] + z * view.up[2]) / view.height) * SH;
      proj[i * 3 + 2] = x * view.toward[0] + y * view.toward[1] + z * view.toward[2];
    }
    for (let t = 0; t < I.length; t += 3) {
      const a = I[t], b = I[t + 1], c = I[t + 2];
      const ax = proj[a * 3], ay = proj[a * 3 + 1], bx = proj[b * 3], by = proj[b * 3 + 1], cx = proj[c * 3], cy = proj[c * 3 + 1];
      const area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
      if (Math.abs(area) < 1e-9) continue;
      const minX = Math.max(0, Math.floor(Math.min(ax, bx, cx))), maxX = Math.min(SW - 1, Math.ceil(Math.max(ax, bx, cx)));
      const minY = Math.max(0, Math.floor(Math.min(ay, by, cy))), maxY = Math.min(SH - 1, Math.ceil(Math.max(ay, by, cy)));
      for (let y = minY; y <= maxY; y++) for (let x = minX; x <= maxX; x++) {
        const px = x + 0.5, py = y + 0.5;
        const w0 = ((bx - px) * (cy - py) - (by - py) * (cx - px)) / area;
        const w1 = ((cx - px) * (ay - py) - (cy - py) * (ax - px)) / area;
        const w2 = 1 - w0 - w1;
        if (w0 < 0 || w1 < 0 || w2 < 0) continue;
        const z = w0 * proj[a * 3 + 2] + w1 * proj[b * 3 + 2] + w2 * proj[c * 3 + 2], k = y * SW + x;
        if (z <= depth[k]) continue;
        const u = w0 * UV[a * 2] + w1 * UV[b * 2] + w2 * UV[c * 2], v = w0 * UV[a * 2 + 1] + w1 * UV[b * 2 + 1] + w2 * UV[c * 2 + 1];
        tex(texture, u, v, s);
        if (cutout && s[3] < 0.5) continue;
        depth[k] = z; cov[k] = 1;
        for (let ch = 0; ch < 3; ch++) {
          col[k * 3 + ch] = s[ch] * (w0 * C[a * 3 + ch] + w1 * C[b * 3 + ch] + w2 * C[c * 3 + ch]);
        }
        let nx = w0 * N[a * 3] + w1 * N[b * 3] + w2 * N[c * 3], ny = w0 * N[a * 3 + 1] + w1 * N[b * 3 + 1] + w2 * N[c * 3 + 1], nz = w0 * N[a * 3 + 2] + w1 * N[b * 3 + 2] + w2 * N[c * 3 + 2];
        const l = Math.hypot(nx, ny, nz) || 1; nx /= l; ny /= l; nz /= l;
        // Express in the impostor plane's tangent frame (x right, y up, z towards the viewer).
        let tx = nx * view.right[0] + ny * view.right[1] + nz * view.right[2];
        let ty = nx * view.up[0] + ny * view.up[1] + nz * view.up[2];
        let tz = nx * view.toward[0] + ny * view.toward[1] + nz * view.toward[2];
        if (tz < 0.15) { tz = 0.15; const m = Math.hypot(tx, ty) / Math.sqrt(1 - 0.15 * 0.15) || 1; tx /= m; ty /= m; }
        nrm[k * 3] = tx; nrm[k * 3 + 1] = ty; nrm[k * 3 + 2] = tz;
      }
    }
  }
  const albedo = new Img(W, H), normal = new Img(W, H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let r = 0, g = 0, b = 0, nx = 0, ny = 0, nz = 0, n = 0;
    for (let j = 0; j < ss; j++) for (let i = 0; i < ss; i++) {
      const k = (y * ss + j) * SW + x * ss + i;
      if (!cov[k]) continue;
      r += col[k * 3]; g += col[k * 3 + 1]; b += col[k * 3 + 2]; nx += nrm[k * 3]; ny += nrm[k * 3 + 1]; nz += nrm[k * 3 + 2]; n++;
    }
    const o = (y * W + x) * 4;
    if (n) {
      albedo.data[o] = r / n; albedo.data[o + 1] = g / n; albedo.data[o + 2] = b / n;
      const l = Math.hypot(nx, ny, nz) || 1;
      normal.data[o] = nx / l * 0.5 + 0.5; normal.data[o + 1] = ny / l * 0.5 + 0.5; normal.data[o + 2] = nz / l * 0.5 + 0.5;
    } else { normal.data[o] = 0.5; normal.data[o + 1] = 0.5; normal.data[o + 2] = 1; }
    albedo.data[o + 3] = n / (ss * ss); normal.data[o + 3] = 1;
  }
  return { albedo, normal };
}
