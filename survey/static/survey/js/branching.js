// Progressive enhancement for conditional questions (skip logic).
//
// The server is authoritative: it decides which questions render and never
// requires/stores a hidden question on submit. This script only improves the
// SAME-PAGE case — revealing or hiding a dependent live as its controlling
// answer changes — so studies work without JS (cross-page) and feel
// responsive with it (same-page).
//
// A conditional question's <fieldset> carries data-visible-if='<rule JSON>'.
// We only manage a fieldset whose controlling questions are present ON THIS
// page; if a controller lives on an earlier page (not in this form), we leave
// the field visible because the server already decided to render it.
//
// The operator semantics below MUST mirror experiments/branching.py
// (_eval_clause) — answers read from the DOM are strings, so both engines
// compare "loosely": numbers numerically when both sides parse, strings
// otherwise. Change the two files together.

const fieldName = (qid) => `q_${qid}`;

// Read the current answer for a question from this form's inputs. Inputs
// disabled by this script (hidden dependents) don't submit, so they read as
// unanswered — keeping chained rules in sync with what the server will see.
// Returns undefined when the control is not on this page at all.
const readAnswer = (form, qid) => {
  const radios = [...form.querySelectorAll(`input[type=radio][name="${fieldName(qid)}"]`)];
  if (radios.length) {
    return radios.find((r) => r.checked && !r.matches(":disabled"))?.value ?? null;
  }
  const checks = [...form.querySelectorAll(`input[type=checkbox][name="${fieldName(qid)}"]`)];
  if (checks.length) {
    return checks.filter((c) => c.checked && !c.matches(":disabled")).map((c) => c.value);
  }
  const el = form.querySelector(`[name="${fieldName(qid)}"]`);
  if (el) return el.matches(":disabled") || el.value === "" ? null : el.value;
  return undefined;
};

// The numeric-literal grammar BOTH engines share: plain decimal / scientific
// notation only (Python's float() additionally accepts "inf"/"1_0", JS's
// Number() accepts "0x10" — the shared regex removes the drift).
const NUMERIC_RE = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;

const toNumber = (x) => {
  if (typeof x === "number") return x;
  if (typeof x === "string" && NUMERIC_RE.test(x.trim())) return Number(x);
  return null;
};

// Type-tolerant equality: a rating answered "3" in the DOM matches a rule
// value authored as the number 3; lists compare element-wise loose (mirrors
// _loose_eq in branching.py — change the two together).
const looseEq = (a, b) => {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((item, i) => looseEq(item, b[i]));
  }
  if (Array.isArray(a) || Array.isArray(b)) return false;
  if (a == null || b == null) return a === b;
  const na = toNumber(a);
  const nb = toNumber(b);
  if (na !== null && nb !== null) return na === nb;
  return String(a) === String(b);
};

// "in"/"nin" membership: list values test loose element equality; a string
// value tests substring membership of a SCALAR answer (an array answer is
// never "in" a string — String([]) === "" would match everything); anything
// else is an empty collection.
const membership = (ans, value) => {
  if (Array.isArray(value)) return value.some((item) => looseEq(ans, item));
  if (typeof value === "string") return !Array.isArray(ans) && value.includes(String(ans));
  return false;
};

const isAnswered = (ans) =>
  ans != null && ans !== "" && !(Array.isArray(ans) && ans.length === 0);

const clauses = (rule) => rule.all ?? rule.any ?? [rule];

const refsOnPage = (rule, form) =>
  clauses(rule).every((c) => readAnswer(form, c.question) !== undefined);

const clauseTrue = (c, form) => {
  const ans = readAnswer(form, c.question);
  if (c.op === "answered") return isAnswered(ans);
  if (c.op === "not_answered") return !isAnswered(ans);
  if (ans == null) return false;
  const v = c.value;
  switch (c.op) {
    case "eq":
      return looseEq(ans, v);
    case "ne":
      return !looseEq(ans, v);
    case "in":
      return membership(ans, v);
    case "nin":
      return !membership(ans, v);
    case "contains":
      return Array.isArray(ans)
        ? ans.some((item) => looseEq(item, v))
        : String(ans).includes(String(v));
    case "gt":
    case "lt":
    case "gte":
    case "lte": {
      const a = toNumber(ans);
      const b = toNumber(v);
      if (a === null || b === null) return false;
      return { gt: a > b, lt: a < b, gte: a >= b, lte: a <= b }[c.op];
    }
  }
  return false;
};

const visible = (rule, form) => {
  if (rule.all) return rule.all.every((c) => clauseTrue(c, form));
  if (rule.any) return rule.any.some((c) => clauseTrue(c, form));
  return clauseTrue(rule, form);
};

document.querySelectorAll("form").forEach((form) => {
  const conditional = form.querySelectorAll("[data-visible-if]");
  if (!conditional.length) return;

  const setShown = (el, shown) => {
    el.hidden = !shown;
    // Latent dependents are server-rendered as <fieldset hidden disabled>;
    // clear the fieldset-level disabled too or the inner inputs stay dead.
    if ("disabled" in el) el.disabled = !shown;
    el.querySelectorAll("input, select, textarea").forEach((input) => {
      input.disabled = !shown;
    });
  };

  const apply = () => {
    conditional.forEach((el) => {
      let rule;
      try {
        rule = JSON.parse(el.dataset.visibleIf);
      } catch {
        return;
      }
      if (refsOnPage(rule, form)) {
        setShown(el, visible(rule, form));
        return;
      }
      // Controllers we can't read. For a normally-rendered fieldset that
      // means an earlier page — trust the server's render (visible). For a
      // server-latent fieldset (data-latent) the controller is on THIS page
      // but unreadable (matrix/ranking/plugin inputs): keep it hidden — the
      // server reveals it on the POST re-render when its rule passes.
      setShown(el, el.dataset.latent !== "1");
    });
  };

  form.addEventListener("change", apply);
  form.addEventListener("input", apply);
  apply();
});
