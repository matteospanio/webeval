// Detect musical keys in the prompt text (e.g. "C minor", "F# major", "Bb minor")
// and render one reference button per detected key that plays the matching root
// triad via the Web Audio API. Gives evaluators an auditory reference so they
// can check whether the sample is in the requested key.
(function () {
  "use strict";

  var promptEl = document.querySelector(".stimulus-prompt p");
  if (!promptEl) {
    return;
  }

  var text = promptEl.textContent || "";
  var re = /\b([A-G])([#♯b♭]?)\s+(major|minor|maj|min)\b/gi;
  var semitoneByLetter = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

  var keys = [];
  var seen = {};
  var m;
  while ((m = re.exec(text)) !== null) {
    var rootLetter = m[1].toUpperCase();
    var accidentalRaw = (m[2] || "").toLowerCase();
    var accidental = "";
    if (accidentalRaw === "#" || accidentalRaw === "♯") accidental = "#";
    else if (accidentalRaw === "b" || accidentalRaw === "♭") accidental = "b";

    var qualityRaw = m[3].toLowerCase();
    var quality = (qualityRaw === "min" || qualityRaw === "minor") ? "minor" : "major";

    var rootSemitone = semitoneByLetter[rootLetter];
    if (accidental === "#") rootSemitone += 1;
    else if (accidental === "b") rootSemitone -= 1;

    var rootLabel = rootLetter + accidental;
    var key = rootLabel + " " + quality;
    if (seen[key]) continue;
    seen[key] = true;
    keys.push({ rootLabel: rootLabel, rootMidi: 60 + rootSemitone, quality: quality });
  }

  if (keys.length === 0) {
    return;
  }

  var audioCtx = null;
  function getCtx() {
    if (audioCtx) return audioCtx;
    try {
      var Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) return null;
      audioCtx = new Ctor();
    } catch (e) {
      return null;
    }
    return audioCtx;
  }

  function midiToFreq(midi) {
    return 440 * Math.pow(2, (midi - 69) / 12);
  }

  function playChord(rootMidi, quality) {
    var ctx = getCtx();
    if (!ctx) return;
    if (ctx.state === "suspended" && typeof ctx.resume === "function") {
      ctx.resume();
    }

    var thirdInterval = quality === "minor" ? 3 : 4;
    var intervals = [0, thirdInterval, 7];
    var now = ctx.currentTime;
    var attack = 0.015;
    var release = 1.2;
    var peak = 1 / 3;

    intervals.forEach(function (semi) {
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = "triangle";
      osc.frequency.value = midiToFreq(rootMidi + semi);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.linearRampToValueAtTime(peak, now + attack);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + attack + release);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + attack + release + 0.05);
    });
  }

  function makeButton(key) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "♪ Reference " + key.rootLabel + " " + key.quality;
    btn.style.marginRight = "0.4rem";
    btn.style.padding = "0.1rem 0.5rem";
    btn.style.fontSize = "0.85rem";
    btn.addEventListener("click", function () {
      playChord(key.rootMidi, key.quality);
    });
    return btn;
  }

  var wrap = document.createElement("div");
  wrap.className = "chord-buttons";
  wrap.style.marginTop = "0.4rem";
  keys.forEach(function (key) {
    wrap.appendChild(makeButton(key));
  });
  promptEl.parentNode.insertBefore(wrap, promptEl.nextSibling);
})();
