export function installNativeTransport() {
  const NativeXHR = window.XMLHttpRequest;
  const open = NativeXHR.prototype.open;
  const send = NativeXHR.prototype.send;
  const header = NativeXHR.prototype.setRequestHeader;
  const state = new WeakMap();
  window.__gmapsTemplates = {};
  NativeXHR.prototype.open = function(method, url, ...rest) {
    state.set(this, {method, url: String(url), headers: {}});
    return open.call(this, method, url, ...rest);
  };
  NativeXHR.prototype.setRequestHeader = function(name, value) {
    const request = state.get(this);
    if (request) request.headers[name] = value;
    return header.call(this, name, value);
  };
  NativeXHR.prototype.send = function(body) {
    const request = state.get(this);
    const kind = /\/(NodeData|BulkMetadata)\//.exec(request?.url ?? '')?.[1];
    if (kind) window.__gmapsTemplates[kind] = {...request, withCredentials: this.withCredentials};
    return send.call(this, body);
  };
  window.__gmapsRequest = (kind, node, epoch) => new Promise((resolve, reject) => {
    const template = window.__gmapsTemplates[kind];
    if (!template || template.method !== 'GET') return reject(new Error(`No native GET ${kind} transport observed`));
    const url = template.url.replace(/!1s[0-7]*!2u\d+/, `!1s${node}!2u${epoch}`);
    if (url === template.url && !url.includes(`!1s${node}!2u${epoch}`)) return reject(new Error('Unknown native request format'));
    const xhr = new NativeXHR();
    open.call(xhr, 'GET', url, true);
    xhr.withCredentials = template.withCredentials;
    xhr.responseType = 'arraybuffer';
    xhr.timeout = 30000;
    for (const [name, value] of Object.entries(template.headers)) header.call(xhr, name, value);
    xhr.onload = () => xhr.status === 200 ? resolve({kind, node, epoch, bytes: xhr.response.byteLength})
      : reject(new Error(`Native transport HTTP ${xhr.status} for ${kind}/${node}`));
    xhr.onerror = () => reject(new Error(`Native transport refused ${kind}/${node}`));
    xhr.ontimeout = () => reject(new Error(`Native transport timeout ${kind}/${node}`));
    send.call(xhr);
  });

}

export function requestsFromPlan(plan) {
  return plan.frontier.length
    ? plan.frontier.map(n => ({kind: 'BulkMetadata', node: n.node, epoch: n.bulk_epoch}))
    : plan.nodes.map(n => ({kind: 'NodeData', node: n.node, epoch: n.epoch}));
}
