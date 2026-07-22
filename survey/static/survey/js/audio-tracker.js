// Track how long the participant actually played each tracked media element
// and report the cumulative total back to the server. One module covers both
// flows: the single-stimulus page (one <audio> or <video> tagged with
// data-listen-endpoint) and the pairwise page (left/right/prompt players,
// each additionally tagged with data-listen-side). The HTMLMediaElement API
// is identical for <audio> and <video>, so the same accumulation works for
// both. The server stores max(stored, reported), so repeatedly flushing the
// running total is idempotent.

const trackMedia = (media) => {
  const { listenEndpoint: endpoint, listenSide: side, csrfToken: csrf } = media.dataset;
  if (!endpoint) return;

  let totalMs = 0;
  let lastTime = 0;
  let playing = false;

  media.addEventListener("play", () => {
    playing = true;
    lastTime = media.currentTime;
  });

  media.addEventListener("timeupdate", () => {
    if (!playing) return;
    const delta = media.currentTime - lastTime;
    // Guard against seeks producing negative or huge deltas.
    if (delta > 0 && delta < 1.5) totalMs += delta * 1000;
    lastTime = media.currentTime;
  });

  const report = () => {
    if (totalMs <= 0) return;
    const body = { duration_ms: Math.round(totalMs) };
    if (side) body.side = side;
    const payload = JSON.stringify(body);
    // sendBeacon is the transport that survives page unload (the endpoint is
    // csrf-exempt: it is scoped to the participant's session cookie and only
    // ever raises the stored maximum). Fall back to a keepalive fetch when
    // the beacon is unavailable or refuses to queue.
    try {
      if (navigator.sendBeacon?.(endpoint, new Blob([payload], { type: "application/json" }))) {
        return;
      }
    } catch {
      /* fall through to fetch */
    }
    fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: payload,
      keepalive: true,
    }).catch(() => {
      /* swallow — best-effort */
    });
  };

  const stop = () => {
    playing = false;
    report();
  };
  media.addEventListener("pause", stop);
  media.addEventListener("ended", stop);

  // beforeunload alone is unreliable on mobile (iOS Safari may never fire it,
  // and backgrounded tabs are killed without it); visibilitychange → hidden
  // and pagehide are the dependable page-lifecycle signals.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") report();
  });
  window.addEventListener("pagehide", report);
  window.addEventListener("beforeunload", report);
};

document.querySelectorAll("[data-listen-endpoint]").forEach(trackMedia);
