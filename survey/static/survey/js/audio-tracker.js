// Track how long the participant actually listened to a stimulus and POST
// the cumulative duration back to the server. We post on `pause` / `ended`
// and again on `beforeunload` so refreshing or closing the tab doesn't
// lose the data.
(function () {
  "use strict";

  const audio = document.getElementById("stimulus-audio");
  if (!audio) {
    return;
  }
  const endpoint = audio.dataset.listenEndpoint;
  const csrf = audio.dataset.csrfToken;
  if (!endpoint) {
    return;
  }

  let totalMs = 0;
  let lastTime = 0;
  let playing = false;

  audio.addEventListener("play", function () {
    playing = true;
    lastTime = audio.currentTime;
  });

  audio.addEventListener("timeupdate", function () {
    if (!playing) {
      return;
    }
    const now = audio.currentTime;
    const delta = now - lastTime;
    // Guard against seeks producing negative or huge deltas.
    if (delta > 0 && delta < 1.5) {
      totalMs += delta * 1000;
    }
    lastTime = now;
  });

  audio.addEventListener("pause", function () {
    playing = false;
    report();
  });

  audio.addEventListener("ended", function () {
    playing = false;
    report();
  });

  window.addEventListener("beforeunload", report);

  function report() {
    if (totalMs <= 0) {
      return;
    }
    const payload = JSON.stringify({ duration_ms: Math.round(totalMs) });
    try {
      if (navigator.sendBeacon) {
        const blob = new Blob([payload], { type: "application/json" });
        navigator.sendBeacon(endpoint, blob);
        return;
      }
    } catch (e) {
      /* fall through to fetch */
    }
    fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
      },
      body: payload,
      keepalive: true,
    }).catch(function () {
      /* swallow — best-effort */
    });
  }
})();
