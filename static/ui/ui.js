// webeval shared UI behaviour: copy-to-clipboard buttons and confirm guards.
// Progressive enhancement only — nothing here is required for any flow to work.

// Copy buttons: <button class="we-copy" data-copy-text="…"> or
// data-copy-target="#selector" (reads .value for inputs, textContent else).
const copyValue = (btn) => {
  if (btn.dataset.copyText) return btn.dataset.copyText;
  const target = btn.dataset.copyTarget && document.querySelector(btn.dataset.copyTarget);
  if (!target) return "";
  return "value" in target && target.value !== undefined && target.value !== ""
    ? target.value
    : target.textContent.trim();
};

document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".we-copy");
  if (!btn) return;
  const text = copyValue(btn);
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    return; // clipboard unavailable (http, permissions) — leave the button as is
  }
  const original = btn.textContent;
  btn.textContent = "Copied ✓";
  btn.disabled = true;
  setTimeout(() => {
    btn.textContent = original;
    btn.disabled = false;
  }, 1500);
});

// Confirm guards: <form data-confirm="Are you sure?"> asks before submitting.
// Without JS the form still submits — use only for low-stakes actions; anything
// destructive and hard to reverse gets a server-rendered confirm page instead.
document.addEventListener("submit", (e) => {
  const form = e.target.closest("form[data-confirm]");
  if (form && !window.confirm(form.dataset.confirm)) e.preventDefault();
});
