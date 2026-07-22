// Detect musical keys in the prompt text (e.g. "C minor", "F# major", "Bb minor")
// and render one reference button per detected key that plays the matching root
// triad via the Web Audio API. Gives evaluators an auditory reference so they
// can check whether the sample is in the requested key.

const SEMITONE_BY_LETTER = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

// The root letter must be uppercase in the source text — a case-insensitive
// match would turn the English article in "a minor adjustment" into the key
// of A minor. The quality word stays case-tolerant.
const KEY_RE = /\b([A-G])([#♯b♭]?)\s+([Mm]ajor|[Mm]inor|[Mm]aj|[Mm]in)\b/g;

const detectKeys = (text) => {
  const keys = new Map();
  for (const [, letter, accidentalRaw, qualityRaw] of text.matchAll(KEY_RE)) {
    const accidental =
      accidentalRaw === "#" || accidentalRaw === "♯" ? "#"
      : accidentalRaw === "b" || accidentalRaw === "♭" ? "b"
      : "";
    const quality = qualityRaw.toLowerCase().startsWith("min") ? "minor" : "major";
    const offset = accidental === "#" ? 1 : accidental === "b" ? -1 : 0;
    const rootLabel = letter + accidental;
    const label = `${rootLabel} ${quality}`;
    if (!keys.has(label)) {
      keys.set(label, { rootLabel, rootMidi: 60 + SEMITONE_BY_LETTER[letter] + offset, quality });
    }
  }
  return [...keys.values()];
};

let audioCtx = null;
const getCtx = () => {
  if (!audioCtx) {
    const Ctor = window.AudioContext ?? window.webkitAudioContext;
    if (!Ctor) return null;
    try {
      audioCtx = new Ctor();
    } catch {
      return null;
    }
  }
  return audioCtx;
};

const midiToFreq = (midi) => 440 * 2 ** ((midi - 69) / 12);

const playChord = async (rootMidi, quality) => {
  const ctx = getCtx();
  if (!ctx) return;
  if (ctx.state !== "running") {
    try {
      await ctx.resume();
    } catch {
      return;
    }
  }

  const intervals = [0, quality === "minor" ? 3 : 4, 7];
  const now = ctx.currentTime;
  const attack = 0.015;
  const release = 1.2;
  const peak = 1 / intervals.length;

  for (const semi of intervals) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "triangle";
    osc.frequency.value = midiToFreq(rootMidi + semi);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.linearRampToValueAtTime(peak, now + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + attack + release);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now);
    osc.stop(now + attack + release + 0.05);
  }
};

const makeButton = ({ rootLabel, rootMidi, quality }) => {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = `♪ Reference ${rootLabel} ${quality}`;
  Object.assign(btn.style, { marginRight: "0.4rem", padding: "0.1rem 0.5rem", fontSize: "0.85rem" });
  btn.addEventListener("click", () => playChord(rootMidi, quality));
  return btn;
};

const promptEl = document.querySelector(".stimulus-prompt p");
if (promptEl) {
  const keys = detectKeys(promptEl.textContent ?? "");
  if (keys.length) {
    const wrap = document.createElement("div");
    wrap.className = "chord-buttons";
    wrap.style.marginTop = "0.4rem";
    wrap.append(...keys.map(makeButton));
    promptEl.after(wrap);
  }
}
