// Retain failures until the same URL has completed a successful retry, not merely received headers.
export class AssetTransfers {
  pending = new Map();
  failures = new Map();
  start(id, url) { this.pending.set(id, { url }); }
  response(id, status) {
    const request = this.pending.get(id);
    if (!request) return;
    request.status = status;
    if (status >= 400) this.failures.set(request.url, `${status} ${request.url}`);
  }
  finish(id, error) {
    const request = this.pending.get(id);
    if (!request) return false;
    if (error) this.failures.set(request.url, `${error} ${request.url}`);
    else if (request.status >= 200 && request.status < 400) this.failures.delete(request.url);
    this.pending.delete(id);
    return true;
  }
}
