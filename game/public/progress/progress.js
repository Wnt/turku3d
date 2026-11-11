const root = '/progress-evidence/';
const el = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
};
function asset(file) {
  const url = new URL(file, new URL(root, location.origin));
  if (url.origin !== location.origin || !url.pathname.startsWith(root)) throw Error('Evidence must be a local game capture');
  return url.href;
}
function photo(file, label, className) {
  const image = el('img', '', className);
  image.src = asset(file); image.alt = label; image.loading = 'lazy';
  return image;
}
function viewFigure(view) {
  const figure = el('figure'), frame = el('div', '', 'compare');
  const paired = Boolean(view.before && view.after);
  frame.append(photo(view.before || view.after, view.title + (paired ? ' — before' : ' — baseline')));
  if (paired) frame.append(photo(view.after, view.title + ' — after', 'after'), el('div', '', 'divider'));
  const labels = el('div', '', 'labels');
  labels.append(el('span', paired ? 'Before' : 'Baseline'));
  if (paired) labels.append(el('span', 'After'));
  frame.append(labels); figure.append(frame);
  const caption = el('figcaption'); caption.append(el('h3', view.title));
  if (view.note) caption.append(el('div', view.note, 'meta'));
  if (paired) {
    const range = el('input'); range.type = 'range'; range.min = '0'; range.max = '100'; range.value = '50';
    range.setAttribute('aria-label', 'Before and after divider: ' + view.title);
    range.addEventListener('input', () => frame.style.setProperty('--cut', range.value + '%'));
    caption.append(range);
  }
  figure.append(caption); return figure;
}
function entry(item, baseline = false) {
  const section = el('article', '', 'iteration');
  section.append(el('div', baseline ? 'STARTING POINT' : `ITERATION ${item.number}`, 'eyebrow'), el('h2', item.title));
  if (item.summary) section.append(el('p', item.summary));
  const facts = el('div', '', 'facts');
  for (const [label, value] of [['Location', item.location], ['Before', item.beforeRelease], ['After', item.afterRelease], ['Capture', item.settings], ['Hardware', item.hardware]]) {
    if (value) facts.append(el('span', `${label}: ${value}`));
  }
  section.append(facts);
  if (item.playUrl) {
    const url = new URL(item.playUrl, location.origin);
    if (url.origin === location.origin) { const link = el('a', 'Visit this location ↗'); link.href = url.href; section.append(link); }
  }
  if (item.validation) section.append(el('p', item.validation, 'notice'));
  const views = el('div', '', 'views');
  for (const view of item.views || []) views.append(viewFigure(view));
  section.append(views);
  if (item.clips?.length) {
    const clips = el('div', '', 'clips');
    for (const clip of item.clips) {
      const figure = el('figure'), video = el('video');
      video.src = asset(clip.file); video.controls = true; video.muted = true; video.playsInline = true; video.preload = 'metadata';
      if (clip.poster) video.poster = asset(clip.poster);
      figure.append(video, el('figcaption', clip.title)); clips.append(figure);
    }
    section.append(clips);
  }
  return section;
}
async function refresh() {
  const status = document.querySelector('#status');
  try {
    const response = await fetch(root + 'index.json', { cache: 'no-store' });
    if (!response.ok) throw Error('Evidence has not been published yet');
    const data = await response.json();
    const iterations = data.iterations || [];
    status.textContent = `${iterations.length} documented improvements · ${data.status || 'Work is in progress'}`;
    document.querySelector('#iterations').replaceChildren(...iterations.slice().reverse().map(item => entry(item)));
    document.querySelector('#baseline').replaceChildren(...(data.baseline ? [entry(data.baseline, true)] : []));
  } catch (error) { status.textContent = `${error.message}. The game remains available above.`; }
}
document.querySelector('#refresh').addEventListener('click', refresh);
refresh();
