// Injected into every page of the apply browser (Playwright MCP --init-script).
// Belt and braces for "never submit": a click on a final-action control (Submit / Send /
// Bewerbung abschicken …) or a programmatic form.submit() first needs a native confirm() dialog.
// The headless run is told never to accept dialogs (and has no dialog tool), so only a person
// at the visible browser can let the submission through; clicking OK re-sends the click once.
(() => {
  if (window.__applyGuard) return;
  window.__applyGuard = true;
  const FINAL = [
    /^submit( (my )?application)?$/,
    /^send( (my )?application)?$/,
    /^(confirm|review) and submit$/,
    /^apply now$/,
    /^finish( application)?$/,
    /^(jetzt )?bewerbung (abschicken|absenden|senden|einreichen)$/,
    /^(abschicken|absenden|einreichen)$/,
    /^jetzt bewerben$/,
    /^verzenden$/,
    /^sollicitatie (versturen|verzenden)$/,
  ];
  const norm = (s) => String(s || "").replace(/\s+/g, " ").trim().toLowerCase();
  const label = (el) => norm(el.innerText || el.value || el.getAttribute("aria-label") || "");
  const isFinal = (el) => FINAL.some((re) => re.test(label(el)));
  const ask = (what) =>
    window.confirm(`Job-apply guard: "${what}" would submit this application.\n\nOK only if YOU (not the assistant) want to submit now.`);

  let pass = null;
  document.addEventListener(
    "click",
    (ev) => {
      const el = ev.target instanceof Element ? ev.target.closest("button, input[type=submit], input[type=button], a, [role=button]") : null;
      if (!el || !isFinal(el)) return;
      if (pass === el) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      if (ask(label(el))) {
        pass = el;
        try {
          el.click();
        } finally {
          pass = null;
        }
      }
    },
    true,
  );

  // Programmatic submits are gated only for forms that carry a final-action control (the application
  // form). Sites submit other hidden forms on load (consent/tracking bridges); a dialog there would
  // freeze the page for the automation.
  const finalButtonsOf = (form) =>
    [...form.querySelectorAll("button, input[type=submit], [role=button]")].concat(
      form.id ? [...document.querySelectorAll(`[form="${CSS.escape(form.id)}"]`)] : [],
    );
  const isApplicationForm = (form) => finalButtonsOf(form).some(isFinal);
  for (const name of ["submit", "requestSubmit"]) {
    const orig = HTMLFormElement.prototype[name];
    if (typeof orig !== "function") continue;
    HTMLFormElement.prototype[name] = function (...args) {
      if (pass === null && isApplicationForm(this) && !ask(`form.${name}()`)) return undefined;
      return orig.apply(this, args);
    };
  }

  document.addEventListener(
    "submit",
    (ev) => {
      const by = ev.submitter;
      if (pass !== null || !by || !isFinal(by)) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
    },
    true,
  );
})();
