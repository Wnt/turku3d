/** Standalone HTML; media live beside it so file:// viewing works without a report server. */
export const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
export function summarize(samples = []) {
  if (!samples.length) return null;
  const frames = samples.map(s => s.frameMs).filter(Number.isFinite).sort((a, b) => a - b);
  const heights = samples.map(s => s.feet[1]);
  return { samples: samples.length, p50: frames[Math.floor(frames.length * .5)], p95: frames[Math.floor(frames.length * .95)],
    minH: Math.min(...heights), maxH: Math.max(...heights), maxSpeed: Math.max(...samples.map(s => s.speed)),
    groundChecks: samples.filter(s => s.ground !== null).length };
}
export function renderReport(report) {
  const h = escapeHtml;
  const rows = report.rows.map(row => {
    const stats = summarize(row.evidence?.samples);
    const metrics = stats ? `<p>${stats.samples} frame samples · ${stats.groundChecks} ground checks · frame EMA p50 ${stats.p50.toFixed(1)} / p95 ${stats.p95.toFixed(1)} ms · H ${stats.minH.toFixed(2)}–${stats.maxH.toFixed(2)} m · peak speed ${stats.maxSpeed.toFixed(2)} m/s</p>` : '<p>No route measurements.</p>';
    const media = row.attachments.map(a => a.type === 'video/webm' ? `<video controls preload="metadata" src="${h(a.url)}"></video>` : a.type === 'image/png' ? `<a href="${h(a.url)}"><img alt="${h(a.name)}" loading="lazy" src="${h(a.url)}"></a>` : `<a href="${h(a.url)}">${h(a.name)}</a>`).join('\n');
    return `<article><h2><span class="${h(row.status)}">${h(row.status.toUpperCase())}</span> ${h(row.backend)} · ${h(row.title)}</h2>
      <p>${(row.duration / 1000).toFixed(2)} s · ${h(row.tags.join(' '))}</p>${metrics}
      <p>${row.evidence?.perf ? 'W1-08 __perf attached in route-metrics.' : 'W1-08 __perf unavailable; frame EMA is diagnostic, not a 1440p performance acceptance result.'}</p>
      ${row.errors.length ? `<pre>${h(row.errors.join('\n'))}</pre>` : ''}<div class="media">${media}</div></article>`;
  }).join('\n');
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Turku3D playtest</title><style>
body{margin:32px auto;padding:0 24px;max-width:1200px;background:#0f1a20;color:#e6eeee;font:16px/1.5 system-ui}h1{font-size:36px}h2{font-size:20px}.passed{color:#8de1ba}.failed,.timedOut{color:#ff9387}.notice{border-left:4px solid #edc57b;padding:16px;background:#273038}article{margin:24px 0;padding:20px;background:#1b2a32;border-radius:8px}.media{display:flex;gap:16px;flex-wrap:wrap;align-items:start}video,img{width:460px;max-width:100%;border-radius:4px}.media a{max-width:100%}a{color:#a7d7fc}pre{white-space:pre-wrap;overflow-wrap:anywhere}code{overflow-wrap:anywhere}</style>
<h1>Turku3D · Playtest</h1><p>Suite: <strong>${h(report.status.toUpperCase())}</strong> · ${(report.duration / 1000).toFixed(2)} s · ${h(report.date)}</p>
<p>Commit <code>${h(report.commit)}</code>${report.dirty ? ' + uncommitted changes' : ''} · Selection ${h(report.selection.join(', '))}</p>
${report.errors?.length ? `<pre>${h(report.errors.join('\n'))}</pre>` : ''}
<div class="notice"><strong>Phase 1 acceptance: BLOCKED</strong><p>AOI checks use the real-data coarse heightfield. Synthetic curb/stair/controller checks are tagged separately. Passing these tests does not certify the missing Wave 1 collision layers.</p><ul>${report.gaps.map(g => `<li>${h(g)}</li>`).join('')}</ul></div>
<p>Both Chrome backends render at 1280 × 720; videos are saved at 640 × 360 / 12 fps and screenshots at full viewport size. <a href="report.json">Full machine-readable report</a></p>${rows}</html>`;
}
