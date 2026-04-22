// Track listen duration for two audio players in pairwise comparison mode.
// Each player reports independently with its own "side" (a/b).
(function () {
  "use strict";

  function trackAudio(audio) {
    if (!audio) return;
    var endpoint = audio.dataset.listenEndpoint;
    var side = audio.dataset.listenSide;
    var csrf = audio.dataset.csrfToken;
    if (!endpoint || !side) return;

    var totalMs = 0;
    var lastTime = 0;
    var playing = false;

    audio.addEventListener("play", function () {
      playing = true;
      lastTime = audio.currentTime;
    });

    audio.addEventListener("timeupdate", function () {
      if (!playing) return;
      var now = audio.currentTime;
      var delta = now - lastTime;
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
      if (totalMs <= 0) return;
      var payload = JSON.stringify({ duration_ms: Math.round(totalMs), side: side });
      try {
        if (navigator.sendBeacon) {
          var blob = new Blob([payload], { type: "application/json" });
          navigator.sendBeacon(endpoint, blob);
          return;
        }
      } catch (e) { /* fall through */ }
      fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: payload,
        keepalive: true,
      }).catch(function () { /* best-effort */ });
    }
  }

  trackAudio(document.getElementById("stimulus-audio-left"));
  trackAudio(document.getElementById("stimulus-audio-right"));
  trackAudio(document.getElementById("stimulus-audio-prompt"));
})();
