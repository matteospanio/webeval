/* Drag-&-drop question builder — vanilla ES module, server-authoritative.
 *
 * The DOM is the source of truth: each .q-card carries its question state in
 * its inputs (+ dataset.id once saved); a WeakMap links a card element to its
 * controller (read()/setError()). "Save" reads the cards in document order and
 * POSTs them as JSON to the studio save endpoint, which validates and persists
 * (create / update / delete + reorder). This is the only hand-written JS
 * outside the participant survey modules, and it lives solely in the
 * researcher studio.
 */

// --- tiny DOM helper ---------------------------------------------------
const h = (tag, attrs = {}, children = []) => {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (typeof value === "boolean") el[key] = value; // reflected props: draggable, disabled, …
    else el.setAttribute(key, value);
  }
  el.append(...children.filter((c) => c != null));
  return el;
};

const joinLines = (lines) => (lines ?? []).join("\n");
const splitLines = (s) =>
  (s ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

// --- per-type config editors (friendly for common types, JSON else) ----
const field = (labelText, input) => h("label", {}, [labelText, input]);
const num = (value) => {
  const input = h("input", { type: "number" });
  if (value != null) input.value = value;
  return input;
};
const txt = (value) => h("input", { type: "text", value: value ?? "" });
const area = (value, rows = 3) => {
  const textarea = h("textarea", { rows });
  textarea.value = value ?? "";
  return textarea;
};
const check = (labelText, checked) => {
  const input = h("input", { type: "checkbox", checked: Boolean(checked) });
  return { node: h("label", {}, [input, ` ${labelText}`]), input };
};

const EDITORS = {
  rating(c) {
    const mn = num(c.min ?? 0);
    const mx = num(c.max ?? 100);
    const st = num(c.step ?? 1);
    const lo = txt(c.min_label);
    const hi = txt(c.max_label);
    return {
      node: h("div", { class: "q-field-row" }, [
        field("Min", mn), field("Max", mx), field("Step", st),
        field("Low label", lo), field("High label", hi),
      ]),
      read() {
        const cfg = { min: Number(mn.value || 0), max: Number(mx.value || 0), step: Number(st.value || 1) };
        if (lo.value.trim()) cfg.min_label = lo.value.trim();
        if (hi.value.trim()) cfg.max_label = hi.value.trim();
        return cfg;
      },
    };
  },
  choice(c) {
    const opts = area(joinLines(c.choices), 4);
    const multi = check("Allow multiple", c.multi);
    return {
      node: h("div", {}, [field("Options (one per line)", opts), multi.node]),
      read: () => ({ choices: splitLines(opts.value), multi: multi.input.checked }),
    };
  },
  text(c) {
    const ml = num(c.max_length ?? 500);
    return {
      node: field("Max length", ml),
      read: () => ({ max_length: Number(ml.value || 500) }),
    };
  },
  likert(c) {
    const steps = num(c.steps ?? 5);
    const labels = area(joinLines(c.labels), 5);
    return {
      node: h("div", {}, [field("Steps", steps), field("Labels (one per line)", labels)]),
      read: () => ({ steps: Number(steps.value || 0), labels: splitLines(labels.value) }),
    };
  },
  numeric(c) {
    const mn = num(c.min);
    const mx = num(c.max);
    const integer = check("Whole numbers only", c.integer);
    const unit = txt(c.unit);
    return {
      node: h("div", { class: "q-field-row" }, [
        field("Min (optional)", mn), field("Max (optional)", mx), integer.node, field("Unit (optional)", unit),
      ]),
      read() {
        const cfg = {};
        if (mn.value !== "") cfg.min = Number(mn.value);
        if (mx.value !== "") cfg.max = Number(mx.value);
        if (integer.input.checked) cfg.integer = true;
        if (unit.value.trim()) cfg.unit = unit.value.trim();
        return cfg;
      },
    };
  },
};

// Raw-JSON fallback editor (matrix / ranking / plugin types). read() returns
// null on malformed JSON so the save handler can refuse to overwrite the
// stored config with an empty object.
const rawEditor = (c) => {
  const ta = area(JSON.stringify(c ?? {}, null, 2), 4);
  ta.setAttribute("spellcheck", "false");
  const err = h("div", { class: "q-card-error" });
  return {
    node: h("div", {}, [field("Config (JSON)", ta), err]),
    read() {
      err.textContent = "";
      try {
        return JSON.parse(ta.value || "{}");
      } catch {
        err.textContent = "Invalid JSON";
        return null;
      }
    },
  };
};

const makeEditor = (type, config) => (EDITORS[type] ?? rawEditor)(config ?? {});

const option = (value, label, current) =>
  h("option", { value, selected: value === current }, [label]);

// --- builder ------------------------------------------------------------
const cookie = (name) =>
  document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`))
    ?.slice(name.length + 1);

const root = document.querySelector(".builder");
if (root) {
  const canEdit = root.dataset.canEdit === "1";
  const { saveUrl, csrf } = root.dataset;
  // Prefer the live cookie: Django rotates the CSRF secret on (re-)login, so
  // after a session expires and the researcher logs in from another tab, the
  // token embedded at render time is stale but the cookie is fresh — reading
  // it per-save makes the "log in in another tab, then save again" recovery
  // actually work.
  const csrfToken = () => cookie("csrftoken") ?? csrf;

  const palette = JSON.parse(document.getElementById("builder-palette").textContent);
  const initial = JSON.parse(document.getElementById("builder-questions").textContent);
  const paletteByType = new Map(palette.map((p) => [p.type, p]));

  const canvas = document.getElementById("canvas");
  const emptyMsg = document.getElementById("canvas-empty");
  const statusEl = document.getElementById("save-status");
  const saveBtn = document.getElementById("save-btn");

  // element → { read(), setError(msg) } for every rendered .q-card
  const controllers = new WeakMap();

  const setStatus = (msg) => { statusEl.textContent = msg; };
  const refreshEmpty = () => {
    emptyMsg.style.display = canvas.querySelector(".q-card") ? "none" : "";
  };

  const newQuestion = (type) => ({
    type,
    section: "stimulus",
    prompt: "",
    required: true,
    page_break_before: false,
    show_prompt: false,
    config: structuredClone(paletteByType.get(type)?.default_config ?? {}),
  });

  // --- drag to reorder (cards) + drag from palette (add) ----------------
  let draggedCard = null;
  let paletteType = null;

  const clearDragoverHighlight = () => canvas.classList.remove("dragover");

  // Cards drag only from their header: the card becomes draggable on
  // pointerdown over the head and reverts afterwards, so a mouse-drag inside
  // a prompt/config editor selects text instead of reordering the question.
  const attachReorder = (card, handle) => {
    handle.addEventListener("pointerdown", () => {
      card.draggable = true;
    });
    card.addEventListener("pointerup", () => {
      card.draggable = false;
    });
    card.addEventListener("dragstart", (e) => {
      draggedCard = card;
      card.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", card.dataset.type);
    });
    card.addEventListener("dragend", () => {
      card.classList.remove("dragging");
      card.draggable = false;
      draggedCard = null;
      clearDragoverHighlight();
    });
  };

  const lockCard = (card) => {
    card.draggable = false;
    card.querySelectorAll("input, textarea, select, button").forEach((el) => {
      el.disabled = true;
    });
  };

  const renderCard = (q) => {
    const meta = paletteByType.get(q.type) ?? { label: q.type };
    const card = h("div", { class: "q-card" });
    if (q.id) card.dataset.id = q.id;
    card.dataset.type = q.type;

    const prompt = area(q.prompt, 2);
    prompt.setAttribute("placeholder", "Question prompt (Markdown)");
    const section = h("select", {}, [
      option("stimulus", "Per stimulus", q.section),
      option("demographic", "Demographic (end)", q.section),
      option("screening", "Screening (start)", q.section),
    ]);
    const required = check("Required", q.required !== false);
    const pageBreak = check("Page break before", q.page_break_before);
    const showPrompt = check("Show stimulus prompt", q.show_prompt);
    const editor = makeEditor(q.type, q.config);
    const err = h("div", { class: "q-card-error" });

    const del = h(
      "button",
      { type: "button", class: "secondary outline", onclick: () => { card.remove(); refreshEmpty(); } },
      ["Delete"],
    );
    const up = h(
      "button",
      {
        type: "button", class: "secondary outline", title: "Move up", "aria-label": "Move up",
        onclick: () => card.previousElementSibling && canvas.insertBefore(card, card.previousElementSibling),
      },
      ["↑"],
    );
    const down = h(
      "button",
      {
        type: "button", class: "secondary outline", title: "Move down", "aria-label": "Move down",
        onclick: () => card.nextElementSibling && canvas.insertBefore(card.nextElementSibling, card),
      },
      ["↓"],
    );

    const head = h("div", { class: "q-card-head" }, [
      h("span", { class: "q-card-type" }, [`${meta.label ?? q.type}${meta.plugin ? " (plugin)" : ""}`]),
      h("span", { class: "q-card-actions" }, [up, down, del]),
    ]);
    card.append(
      head,
      field("Prompt", prompt),
      h("div", { class: "q-field-row" }, [field("Section", section), required.node, pageBreak.node, showPrompt.node]),
      editor.node,
      err,
    );

    controllers.set(card, {
      read() {
        const data = {
          type: card.dataset.type,
          section: section.value,
          prompt: prompt.value,
          required: required.input.checked,
          page_break_before: pageBreak.input.checked,
          show_prompt: showPrompt.input.checked,
          config: editor.read(),
        };
        if (card.dataset.id) data.id = Number(card.dataset.id);
        return data;
      },
      setError(msg) { err.textContent = msg; },
    });

    if (canEdit) attachReorder(card, head);
    else lockCard(card);
    return card;
  };

  // Safety net: a header press whose pointerup lands outside the card would
  // otherwise leave that card draggable.
  document.addEventListener("pointerup", () => {
    canvas.querySelectorAll('.q-card[draggable="true"]').forEach((el) => {
      el.draggable = false;
    });
  });

  const addCard = (q) => {
    canvas.append(renderCard(q));
    refreshEmpty();
  };

  const afterElement = (y) =>
    [...canvas.querySelectorAll(".q-card:not(.dragging)")].reduce(
      (best, el) => {
        const { top, height } = el.getBoundingClientRect();
        const offset = y - top - height / 2;
        return offset < 0 && offset > best.offset ? { offset, el } : best;
      },
      { offset: -Infinity, el: null },
    ).el;

  canvas.addEventListener("dragover", (e) => {
    if (!canEdit) return;
    e.preventDefault();
    canvas.classList.add("dragover");
    if (draggedCard) {
      const after = afterElement(e.clientY);
      if (after == null) canvas.append(draggedCard);
      else canvas.insertBefore(draggedCard, after);
    }
  });
  canvas.addEventListener("dragleave", (e) => {
    if (e.target === canvas && !canvas.contains(e.relatedTarget)) clearDragoverHighlight();
  });
  canvas.addEventListener("drop", (e) => {
    if (!canEdit) return;
    e.preventDefault();
    clearDragoverHighlight();
    if (paletteType) {
      const card = renderCard(newQuestion(paletteType));
      const after = afterElement(e.clientY);
      if (after == null) canvas.append(card);
      else canvas.insertBefore(card, after);
      paletteType = null;
      refreshEmpty();
    }
  });

  document.querySelectorAll(".palette-item").forEach((item) => {
    item.addEventListener("dragstart", (e) => {
      paletteType = item.dataset.type;
      e.dataTransfer.effectAllowed = "copy";
      e.dataTransfer.setData("text/plain", item.dataset.type);
    });
    item.addEventListener("dragend", () => {
      paletteType = null;
      clearDragoverHighlight();
    });
    const add = () => canEdit && addCard(newQuestion(item.dataset.type));
    item.addEventListener("click", add);
    item.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        add();
      }
    });
  });

  // --- save --------------------------------------------------------------
  const cardsInOrder = () =>
    [...canvas.querySelectorAll(".q-card")].map((el) => ({ el, ...controllers.get(el) }));

  const showErrors = (cards, errors) => {
    for (const [idx, fieldErrs] of Object.entries(errors ?? {})) {
      const parts = Object.entries(fieldErrs).map(
        ([f, msgs]) => `${f === "__all__" ? "" : `${f}: `}${[].concat(msgs).join(" ")}`,
      );
      cards[Number(idx)]?.setError(parts.join(" · "));
    }
  };

  const save = async () => {
    const cards = cardsInOrder();
    cards.forEach((c) => c.setError(""));
    const questions = cards.map((c) => c.read());
    if (questions.some((q) => q.config === null)) {
      setStatus("Couldn't save — fix the invalid JSON config first.");
      return;
    }
    setStatus("Saving…");
    saveBtn.disabled = true;
    try {
      const r = await fetch(saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify({ questions }),
      });
      if (r.redirected || !(r.headers.get("Content-Type") ?? "").includes("application/json")) {
        // @login_required redirect or an HTML error page (CSRF / 500) — the
        // question set only lives in this DOM, so never suggest reloading.
        setStatus(
          r.redirected || r.status === 403
            ? "Your session expired — log in to the studio in another tab, then save again."
            : `Server error (${r.status}) — your questions are still here, try saving again.`,
        );
        return;
      }
      const data = await r.json();
      if (data.ok) {
        (data.ids ?? []).forEach((id, i) => {
          if (cards[i]) cards[i].el.dataset.id = id;
        });
        setStatus(`Saved ${data.count} question(s).`);
      } else if (data.errors) {
        showErrors(cards, data.errors);
        setStatus("Couldn't save — fix the highlighted questions.");
      } else {
        setStatus(data.error ?? "Couldn't save.");
      }
    } catch {
      setStatus("Network error — try again.");
    } finally {
      saveBtn.disabled = false;
    }
  };

  if (saveBtn) {
    saveBtn.disabled = !canEdit;
    if (canEdit) saveBtn.addEventListener("click", save);
  }

  // --- init --------------------------------------------------------------
  initial.forEach(addCard);
  refreshEmpty();
}
