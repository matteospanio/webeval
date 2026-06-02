// Progressive enhancement for conditional questions (skip logic).
//
// The server is authoritative: it hides questions whose `visible_if` fails
// against answers from EARLIER pages, and never requires/stores a hidden
// question on submit. This script only improves the SAME-PAGE case — revealing
// or hiding a dependent live as its controlling answer changes — so studies
// work without JS (cross-page) and feel responsive with it (same-page).
//
// A conditional question's <fieldset> carries data-visible-if='<rule JSON>'.
// We only manage a fieldset whose controlling questions are present ON THIS
// page; if a controller lives on an earlier page (not in this form), we leave
// the field visible because the server already decided to render it.
(function () {
  "use strict";

  function fieldName(qid) {
    return 'q_' + qid;
  }

  function readAnswer(form, qid) {
    var radios = form.querySelectorAll(
      'input[type=radio][name="' + fieldName(qid) + '"]'
    );
    if (radios.length) {
      for (var i = 0; i < radios.length; i++) {
        if (radios[i].checked) return radios[i].value;
      }
      return null;
    }
    var checks = form.querySelectorAll(
      'input[type=checkbox][name="' + fieldName(qid) + '"]'
    );
    if (checks.length) {
      var vals = [];
      checks.forEach(function (c) {
        if (c.checked) vals.push(c.value);
      });
      return vals;
    }
    var el = form.querySelector('[name="' + fieldName(qid) + '"]');
    if (el) return el.value === "" ? null : el.value;
    return undefined; // control not on this page
  }

  function clauses(rule) {
    if (rule.all) return rule.all;
    if (rule.any) return rule.any;
    return [rule];
  }

  function refsOnPage(rule, form) {
    return clauses(rule).every(function (c) {
      return readAnswer(form, c.question) !== undefined;
    });
  }

  function clauseTrue(c, form) {
    var ans = readAnswer(form, c.question);
    if (c.op === "answered") {
      return ans !== null && ans !== undefined && ans !== "" &&
        !(Array.isArray(ans) && ans.length === 0);
    }
    if (c.op === "not_answered") {
      return ans === null || ans === undefined || ans === "" ||
        (Array.isArray(ans) && ans.length === 0);
    }
    if (ans === null || ans === undefined) return false;
    var v = c.value;
    switch (c.op) {
      case "eq":
        return String(ans) === String(v);
      case "ne":
        return String(ans) !== String(v);
      case "in":
        return Array.isArray(v) && v.map(String).indexOf(String(ans)) !== -1;
      case "nin":
        return Array.isArray(v) && v.map(String).indexOf(String(ans)) === -1;
      case "contains":
        return Array.isArray(ans)
          ? ans.map(String).indexOf(String(v)) !== -1
          : String(ans).indexOf(String(v)) !== -1;
      case "gt":
        return parseFloat(ans) > parseFloat(v);
      case "lt":
        return parseFloat(ans) < parseFloat(v);
      case "gte":
        return parseFloat(ans) >= parseFloat(v);
      case "lte":
        return parseFloat(ans) <= parseFloat(v);
    }
    return false;
  }

  function visible(rule, form) {
    if (rule.all) return rule.all.every(function (c) { return clauseTrue(c, form); });
    if (rule.any) return rule.any.some(function (c) { return clauseTrue(c, form); });
    return clauseTrue(rule, form);
  }

  document.querySelectorAll("form").forEach(function (form) {
    var conditional = form.querySelectorAll("[data-visible-if]");
    if (!conditional.length) return;

    function setShown(el, shown) {
      el.hidden = !shown;
      el.querySelectorAll("input, select, textarea").forEach(function (i) {
        i.disabled = !shown;
      });
    }

    function apply() {
      conditional.forEach(function (el) {
        var rule;
        try {
          rule = JSON.parse(el.getAttribute("data-visible-if"));
        } catch (e) {
          return;
        }
        if (!refsOnPage(rule, form)) {
          // Controller is on an earlier page — trust the server's render.
          setShown(el, true);
          return;
        }
        setShown(el, visible(rule, form));
      });
    }

    form.addEventListener("change", apply);
    form.addEventListener("input", apply);
    apply();
  });
})();
