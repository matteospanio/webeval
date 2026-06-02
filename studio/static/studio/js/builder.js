/* Drag-&-drop question builder — vanilla, server-authoritative.
 *
 * The DOM is the source of truth: each .q-card carries its question state in
 * its inputs (+ dataset.id once saved). "Save" reads the cards in document
 * order and POSTs them as JSON to the studio save endpoint, which validates
 * and persists (create / update / delete + reorder). This is the only
 * hand-written JS outside the participant audio tracker, and it lives solely
 * in the researcher studio.
 */
(function () {
  "use strict";

  var root = document.querySelector(".builder");
  if (!root) return;
  var canEdit = root.dataset.canEdit === "1";
  var saveUrl = root.dataset.saveUrl;
  var csrf = root.dataset.csrf;

  var palette = JSON.parse(document.getElementById("builder-palette").textContent);
  var initial = JSON.parse(document.getElementById("builder-questions").textContent);
  var paletteByType = {};
  palette.forEach(function (p) { paletteByType[p.type] = p; });

  var canvas = document.getElementById("canvas");
  var emptyMsg = document.getElementById("canvas-empty");
  var statusEl = document.getElementById("save-status");
  var saveBtn = document.getElementById("save-btn");

  // --- tiny DOM helper -------------------------------------------------
  function h(tag, attrs, children) {
    var e = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      if (k === "class") e.className = attrs[k];
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] === true) e.setAttribute(k, "");
      else if (attrs[k] !== false && attrs[k] != null) e.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      if (c == null) return;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return e;
  }
  function joinLines(a) { return (a || []).join("\n"); }
  function splitLines(s) {
    return (s || "").split("\n").map(function (x) { return x.trim(); }).filter(Boolean);
  }
  function clone(o) { return JSON.parse(JSON.stringify(o || {})); }

  // --- per-type config editors (friendly for common types, JSON else) --
  function field(labelText, input) { return h("label", {}, [labelText, input]); }
  function num(value) { var i = h("input", { type: "number" }); if (value != null) i.value = value; return i; }
  function txt(value) { var i = h("input", { type: "text" }); i.value = value || ""; return i; }
  function area(value, rows) { var t = h("textarea", { rows: rows || 3 }); t.value = value || ""; return t; }
  function check(labelText, checked) {
    var i = h("input", { type: "checkbox" }); i.checked = !!checked;
    return { node: h("label", {}, [i, " " + labelText]), input: i };
  }

  var EDITORS = {
    rating: function (c) {
      var mn = num(c.min != null ? c.min : 0), mx = num(c.max != null ? c.max : 100),
          st = num(c.step != null ? c.step : 1), lo = txt(c.min_label), hi = txt(c.max_label);
      var node = h("div", { class: "q-field-row" }, [
        field("Min", mn), field("Max", mx), field("Step", st),
        field("Low label", lo), field("High label", hi)]);
      return { node: node, read: function () {
        var cfg = { min: Number(mn.value || 0), max: Number(mx.value || 0), step: Number(st.value || 1) };
        if (lo.value.trim()) cfg.min_label = lo.value.trim();
        if (hi.value.trim()) cfg.max_label = hi.value.trim();
        return cfg;
      } };
    },
    choice: function (c) {
      var opts = area(joinLines(c.choices), 4), multi = check("Allow multiple", c.multi);
      return { node: h("div", {}, [field("Options (one per line)", opts), multi.node]),
        read: function () { return { choices: splitLines(opts.value), multi: multi.input.checked }; } };
    },
    text: function (c) {
      var ml = num(c.max_length != null ? c.max_length : 500);
      return { node: field("Max length", ml), read: function () { return { max_length: Number(ml.value || 500) }; } };
    },
    likert: function (c) {
      var steps = num(c.steps != null ? c.steps : 5), labels = area(joinLines(c.labels), 5);
      return { node: h("div", {}, [field("Steps", steps), field("Labels (one per line)", labels)]),
        read: function () { return { steps: Number(steps.value || 0), labels: splitLines(labels.value) }; } };
    },
    numeric: function (c) {
      var mn = num(c.min), mx = num(c.max), integer = check("Whole numbers only", c.integer), unit = txt(c.unit);
      return { node: h("div", { class: "q-field-row" }, [field("Min (optional)", mn), field("Max (optional)", mx), integer.node, field("Unit (optional)", unit)]),
        read: function () {
          var cfg = {};
          if (mn.value !== "") cfg.min = Number(mn.value);
          if (mx.value !== "") cfg.max = Number(mx.value);
          if (integer.input.checked) cfg.integer = true;
          if (unit.value.trim()) cfg.unit = unit.value.trim();
          return cfg;
        } };
    }
  };

  function rawEditor(c) {
    var ta = area(JSON.stringify(c || {}, null, 2), 4);
    ta.setAttribute("spellcheck", "false");
    var err = h("div", { class: "q-card-error" });
    return { node: h("div", {}, [field("Config (JSON)", ta), err]), read: function () {
      err.textContent = "";
      try { return JSON.parse(ta.value || "{}"); } catch (e) { err.textContent = "Invalid JSON"; return {}; }
    } };
  }

  function makeEditor(type, config) {
    return (EDITORS[type] || rawEditor)(config || {});
  }

  // --- cards -----------------------------------------------------------
  function option(value, label, current) {
    var o = h("option", { value: value }, [label]); if (value === current) o.selected = true; return o;
  }

  function newQuestion(type) {
    var meta = paletteByType[type] || {};
    return { type: type, section: "stimulus", prompt: "", required: true,
      page_break_before: false, show_prompt: false, config: clone(meta.default_config) };
  }

  function renderCard(q) {
    var meta = paletteByType[q.type] || { label: q.type };
    var card = h("div", { class: "q-card", draggable: canEdit });
    if (q.id) card.dataset.id = q.id;
    card.dataset.type = q.type;

    var prompt = area(q.prompt, 2);
    prompt.setAttribute("placeholder", "Question prompt (Markdown)");
    var section = h("select", {}, [
      option("stimulus", "Per stimulus", q.section),
      option("demographic", "Demographic (end)", q.section),
      option("screening", "Screening (start)", q.section)]);
    var required = check("Required", q.required !== false);
    var pageBreak = check("Page break before", q.page_break_before);
    var showPrompt = check("Show stimulus prompt", q.show_prompt);
    var editor = makeEditor(q.type, q.config);
    var err = h("div", { class: "q-card-error" });

    var del = h("button", { type: "button", class: "secondary outline",
      onclick: function () { card.remove(); refreshEmpty(); } }, ["Delete"]);
    var up = h("button", { type: "button", class: "secondary outline", title: "Move up",
      onclick: function () { var p = card.previousElementSibling; if (p) canvas.insertBefore(card, p); } }, ["↑"]);
    var down = h("button", { type: "button", class: "secondary outline", title: "Move down",
      onclick: function () { var n = card.nextElementSibling; if (n) canvas.insertBefore(n, card); } }, ["↓"]);

    card.appendChild(h("div", { class: "q-card-head" }, [
      h("span", { class: "q-card-type" }, [(meta.label || q.type) + (meta.plugin ? " (plugin)" : "")]),
      h("span", { class: "q-card-actions" }, [up, down, del])]));
    card.appendChild(field("Prompt", prompt));
    card.appendChild(h("div", { class: "q-field-row" }, [field("Section", section), required.node, pageBreak.node, showPrompt.node]));
    card.appendChild(editor.node);
    card.appendChild(err);

    card._err = err;
    card._read = function () {
      var data = {
        type: card.dataset.type,
        section: section.value,
        prompt: prompt.value,
        required: required.input.checked,
        page_break_before: pageBreak.input.checked,
        show_prompt: showPrompt.input.checked,
        config: editor.read()
      };
      if (card.dataset.id) data.id = Number(card.dataset.id);
      return data;
    };

    if (canEdit) attachReorder(card); else lockCard(card);
    return card;
  }

  function lockCard(card) {
    card.setAttribute("draggable", "false");
    [].forEach.call(card.querySelectorAll("input, textarea, select, button"), function (el) { el.disabled = true; });
  }

  function addCard(q) { canvas.appendChild(renderCard(q)); refreshEmpty(); }

  function refreshEmpty() {
    emptyMsg.style.display = canvas.querySelector(".q-card") ? "none" : "";
  }

  // --- drag to reorder (cards) + drag from palette (add) ---------------
  var draggedCard = null;
  var paletteType = null;

  function attachReorder(card) {
    card.addEventListener("dragstart", function (e) {
      draggedCard = card; card.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
    });
    card.addEventListener("dragend", function () { card.classList.remove("dragging"); draggedCard = null; });
  }

  function afterElement(y) {
    var els = [].slice.call(canvas.querySelectorAll(".q-card:not(.dragging)"));
    var best = { offset: -Infinity, el: null };
    els.forEach(function (child) {
      var box = child.getBoundingClientRect();
      var offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > best.offset) best = { offset: offset, el: child };
    });
    return best.el;
  }

  canvas.addEventListener("dragover", function (e) {
    if (!canEdit) return;
    e.preventDefault();
    canvas.classList.add("dragover");
    if (draggedCard) {
      var after = afterElement(e.clientY);
      if (after == null) canvas.appendChild(draggedCard);
      else canvas.insertBefore(draggedCard, after);
    }
  });
  canvas.addEventListener("dragleave", function (e) {
    if (e.target === canvas) canvas.classList.remove("dragover");
  });
  canvas.addEventListener("drop", function (e) {
    if (!canEdit) return;
    e.preventDefault();
    canvas.classList.remove("dragover");
    if (paletteType) {
      var card = renderCard(newQuestion(paletteType));
      var after = afterElement(e.clientY);
      if (after == null) canvas.appendChild(card); else canvas.insertBefore(card, after);
      paletteType = null;
      refreshEmpty();
    }
  });

  [].forEach.call(document.querySelectorAll(".palette-item"), function (item) {
    item.addEventListener("dragstart", function (e) {
      paletteType = item.dataset.type;
      e.dataTransfer.effectAllowed = "copy";
      e.dataTransfer.setData("text/plain", item.dataset.type);
    });
    item.addEventListener("dragend", function () { paletteType = null; });
    function add() { if (canEdit) addCard(newQuestion(item.dataset.type)); }
    item.addEventListener("click", add);
    item.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); add(); }
    });
  });

  // --- save ------------------------------------------------------------
  function setStatus(msg) { statusEl.textContent = msg; }
  function clearErrors(cards) { cards.forEach(function (c) { c._err.textContent = ""; }); }
  function showErrors(cards, errors) {
    Object.keys(errors || {}).forEach(function (idx) {
      var card = cards[Number(idx)];
      if (!card) return;
      var fieldErrs = errors[idx];
      var parts = [];
      Object.keys(fieldErrs).forEach(function (f) { parts.push((f === "__all__" ? "" : f + ": ") + [].concat(fieldErrs[f]).join(" ")); });
      card._err.textContent = parts.join(" · ");
    });
  }

  if (saveBtn) {
    if (!canEdit) { saveBtn.disabled = true; }
    saveBtn.addEventListener("click", function () {
      if (!canEdit) return;
      var cards = [].slice.call(canvas.querySelectorAll(".q-card"));
      clearErrors(cards);
      var questions = cards.map(function (c) { return c._read(); });
      setStatus("Saving…");
      saveBtn.disabled = true;
      fetch(saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ questions: questions })
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          saveBtn.disabled = false;
          if (res.data.ok) {
            (res.data.ids || []).forEach(function (id, i) { if (cards[i]) cards[i].dataset.id = id; });
            setStatus("Saved " + res.data.count + " question(s).");
          } else if (res.data.errors) {
            showErrors(cards, res.data.errors);
            setStatus("Couldn't save — fix the highlighted questions.");
          } else {
            setStatus(res.data.error || "Couldn't save.");
          }
        }).catch(function () { saveBtn.disabled = false; setStatus("Network error — try again."); });
    });
  }

  // --- init ------------------------------------------------------------
  initial.forEach(function (q) { addCard(q); });
  refreshEmpty();
})();
