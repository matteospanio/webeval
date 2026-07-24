// Waveform audio player — progressive enhancement for <audio data-waveform>.
//
// For each tagged audio element we decode the file with the Web Audio API,
// draw a static waveform on a <canvas>, and add a play/pause + click-to-seek
// transport that drives the SAME <audio> element. The element keeps its
// data-listen-endpoint attribute, so audio-tracker.js still records listen
// time exactly as before. If anything fails (unsupported format, CORS, no
// Web Audio) the native <audio controls> is left in place — nothing breaks.
//
// Self-contained (no imports): WhiteNoise hashes filenames in production.

const cssVar = (el, name, fallback) => {
  const v = getComputedStyle(el).getPropertyValue(name).trim();
  return v || fallback;
};

// Downsample a channel to `buckets` peak amplitudes in [0, 1].
const extractPeaks = (channel, buckets) => {
  const size = Math.floor(channel.length / buckets) || 1;
  const peaks = new Float32Array(buckets);
  let max = 0.0001;
  for (let i = 0; i < buckets; i++) {
    let peak = 0;
    const start = i * size;
    for (let j = 0; j < size; j++) {
      const v = Math.abs(channel[start + j] || 0);
      if (v > peak) peak = v;
    }
    peaks[i] = peak;
    if (peak > max) max = peak;
  }
  for (let i = 0; i < buckets; i++) peaks[i] /= max; // normalise
  return peaks;
};

const draw = (canvas, peaks, progress, colors) => {
  const dpr = window.devicePixelRatio || 1;
  const { width, height } = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(width * dpr));
  canvas.height = Math.max(1, Math.floor(height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);

  const n = peaks.length;
  const gap = 1;
  const barW = Math.max(1, width / n - gap);
  const mid = height / 2;
  const playedX = progress * width;
  for (let i = 0; i < n; i++) {
    const x = i * (width / n);
    const h = Math.max(2, peaks[i] * (height - 4));
    ctx.fillStyle = x < playedX ? colors.played : colors.track;
    ctx.beginPath();
    const r = Math.min(barW, h) / 2;
    // rounded bar
    const y = mid - h / 2;
    ctx.roundRect ? ctx.roundRect(x, y, barW, h, r) : ctx.rect(x, y, barW, h);
    ctx.fill();
  }
};

const fmt = (s) => {
  if (!isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
};

const enhance = async (audio) => {
  let ctx;
  try {
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return;
    const src = audio.currentSrc || audio.src || audio.querySelector("source")?.src;
    if (!src) return;
    const resp = await fetch(src, { credentials: "same-origin" });
    const buf = await resp.arrayBuffer();
    ctx = new Ctor();
    const decoded = await ctx.decodeAudioData(buf);
    const peaks = extractPeaks(decoded.getChannelData(0), 240);

    // Build the transport UI and hide the native controls.
    const wrap = document.createElement("div");
    wrap.className = "we-waveform";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "we-waveform-play we-btn we-btn-sm";
    btn.setAttribute("aria-label", "Play");
    btn.textContent = "▶";
    const canvas = document.createElement("canvas");
    canvas.className = "we-waveform-canvas";
    const time = document.createElement("span");
    time.className = "we-waveform-time";
    time.textContent = `0:00 / ${fmt(decoded.duration)}`;
    wrap.append(btn, canvas, time);
    audio.controls = false;
    audio.after(wrap);

    const colors = {
      played: cssVar(audio, "--we-primary", "#0d9488"),
      track: cssVar(audio, "--we-border-strong", "#cfd4d4"),
    };
    const render = () =>
      draw(canvas, peaks, audio.duration ? audio.currentTime / audio.duration : 0, colors);
    render();

    btn.addEventListener("click", () => {
      if (audio.paused) audio.play();
      else audio.pause();
    });
    audio.addEventListener("play", () => {
      btn.textContent = "❚❚";
      btn.setAttribute("aria-label", "Pause");
    });
    audio.addEventListener("pause", () => {
      btn.textContent = "▶";
      btn.setAttribute("aria-label", "Play");
    });
    audio.addEventListener("timeupdate", () => {
      render();
      time.textContent = `${fmt(audio.currentTime)} / ${fmt(decoded.duration)}`;
    });
    audio.addEventListener("ended", render);
    canvas.addEventListener("click", (e) => {
      const rect = canvas.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
      if (audio.duration) audio.currentTime = ratio * audio.duration;
    });
    window.addEventListener("resize", render, { passive: true });
  } catch {
    // Decoding/UI failed — leave the native controls in place.
    audio.controls = true;
  } finally {
    // Release the decoding context; playback uses the <audio> element, not it.
    if (ctx && ctx.state !== "closed") ctx.close?.();
  }
};

document.querySelectorAll("audio[data-waveform]").forEach(enhance);
