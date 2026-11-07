// Listening for the share's long-running servers (city-web, city-tiles) so a restart answers nobody with an error:
//  - systemd socket activation: with LISTEN_FDS=1 (and LISTEN_PID unset or this process) the server accepts on the
//    inherited fd 3, the unit's .socket that systemd holds across restarts. Connections that arrive while the
//    service restarts wait in that socket's backlog instead of being refused (the tunnel answered them 502).
//  - SIGTERM/SIGINT drain: stop accepting, close idle keep-alive connections, let the requests in flight finish (at
//    most `drainMs`), then exit. A response is never cut by a restart unless it outlives the drain. `onStop()` ends
//    what would hold the drain open for nothing (upgraded WebSocket pipes: their clients reconnect).
// Without LISTEN_FDS the server listens on `port`/`host` itself, as before.
export function listen(server, { port, host = '127.0.0.1', drainMs = 4000, onListening, onStop } = {}) {
  const inherited = process.env.LISTEN_FDS === '1' && (!process.env.LISTEN_PID || Number(process.env.LISTEN_PID) === process.pid);
  server.listen(inherited ? { fd: 3 } : { port, host }, () => onListening?.(inherited));
  let stopping = false;
  const stop = () => {
    if (stopping) return;
    stopping = true;
    server.close(() => process.exit(0)); // resolves once every request in flight has been answered
    // A keep-alive connection turns idle when its answer is out: close those as they appear, not only the idle ones now.
    server.closeIdleConnections?.();
    setInterval(() => server.closeIdleConnections?.(), 50).unref();
    onStop?.();
    setTimeout(() => process.exit(0), drainMs).unref();
  };
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
  return server;
}
