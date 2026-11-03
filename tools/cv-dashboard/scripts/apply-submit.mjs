#!/usr/bin/env node
// "Confirm & submit": the only automated code path that submits an application (the Live browser
// view in server/live.ts relays the candidate's own taps and guard-dialog answer). Started by the dashboard's
// POST /api/applications/:jd/apply/submit after the candidate confirmed twice. No LLM involved.
//
//   node scripts/apply-submit.mjs <data/apply/<jd> dir>
//
// Reads status.json written by the fill run (state filled_awaiting_confirm, url, cdpEndpoint,
// submitControl.text), reattaches to the SAME persistent browser over CDP, finds the filled tab,
// clicks the recorded final-action control with a real mouse event (the page guard's confirm()
// dialog is accepted here, and only here), waits for a confirmation, screenshots it and updates
// status.json: submitted | submit_uncertain | submit_failed. If the browser is gone it is restarted
// on the same profile (logins survive) but the half-filled form is not, so it reports page_lost.
import { spawnSync } from "node:child_process";
import { readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { CONFIRM_RE, GUARD_PREFIX, REJECT_RE } from "./apply-confirm.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.resolve(process.argv[2] ?? "");
const statusFile = path.join(outDir, "status.json");
const WAIT_MS = Number(process.env.APPLY_SUBMIT_WAIT_MS ?? 20000);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => new Date().toISOString();

function readStatus() {
  return JSON.parse(readFileSync(statusFile, "utf-8"));
}
function writeStatus(patch) {
  const next = { ...readStatus(), ...patch, updatedAt: now() };
  writeFileSync(`${statusFile}.tmp`, JSON.stringify(next, null, 2));
  renameSync(`${statusFile}.tmp`, statusFile);
  return next;
}
function fail(state, error, extra = {}) {
  writeStatus({ state, submitted: false, submitError: error, submitFinishedAt: now(), ...extra });
  console.log(JSON.stringify({ state, error }));
  process.exit(0);
}

const httpBase = (ep) => {
  const u = new URL(ep);
  return `http://${u.host}`;
};
async function getJson(url) {
  const r = await fetch(url, { signal: AbortSignal.timeout(3000) });
  return r.json();
}
const sameDoc = (a, b) => {
  try {
    const x = new URL(a), y = new URL(b);
    return x.origin === y.origin && x.pathname.replace(/\/$/, "") === y.pathname.replace(/\/$/, "");
  } catch {
    return false;
  }
};

/** Minimal CDP session on one page target. */
function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0;
    const pending = new Map();
    const listeners = new Set();
    ws.onopen = () =>
      resolve({
        send(method, params = {}) {
          return new Promise((res, rej) => {
            const i = ++id;
            pending.set(i, { res, rej });
            ws.send(JSON.stringify({ id: i, method, params }));
          });
        },
        on(fn) {
          listeners.add(fn);
        },
        close() {
          ws.close();
        },
      });
    ws.onerror = (e) => reject(new Error(`CDP connect failed: ${e.message ?? e.type}`));
    ws.onmessage = (m) => {
      const msg = JSON.parse(String(m.data));
      if (msg.id && pending.has(msg.id)) {
        const p = pending.get(msg.id);
        pending.delete(msg.id);
        msg.error ? p.rej(new Error(msg.error.message)) : p.res(msg.result);
      } else if (msg.method) for (const fn of listeners) fn(msg);
    };
  });
}

/** Runs IN THE PAGE: find each field by its visible label and set the value React/Vue-safely. */
function applyEdits(edits) {
  const norm = (s) => String(s || "").replace(/\*/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
  const controlOf = (lab) =>
    lab.control || lab.querySelector("input, textarea, select") || (lab.htmlFor ? document.getElementById(lab.htmlFor) : null);
  const labels = [...document.querySelectorAll("label, legend, [id$=label], .label")];
  return edits.map((e) => {
    const want = norm(e.label);
    let ctl = null;
    for (const pass of [(t) => t === want, (t) => t.length > 8 && (t.startsWith(want) || want.startsWith(t))]) {
      const lab = labels.find((l) => pass(norm(l.innerText)) && controlOf(l));
      if (lab) {
        ctl = controlOf(lab);
        break;
      }
      const byAttr = [...document.querySelectorAll("input, textarea")].find((x) =>
        pass(norm(x.getAttribute("aria-label") || x.getAttribute("placeholder") || "")),
      );
      if (byAttr) {
        ctl = byAttr;
        break;
      }
    }
    if (!ctl || !("value" in ctl) || /^(checkbox|radio|file|submit|button)$/.test(ctl.type)) return { label: e.label, ok: false };
    const proto = ctl.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : ctl.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(ctl, e.value);
    ctl.dispatchEvent(new Event("input", { bubbles: true }));
    ctl.dispatchEvent(new Event("change", { bubbles: true }));
    ctl.dispatchEvent(new Event("blur", { bubbles: true }));
    return { label: e.label, ok: ctl.value === e.value };
  });
}

async function main() {
  let st;
  try {
    st = readStatus();
  } catch {
    console.error("no status.json");
    process.exit(2);
  }
  if (st.state !== "submitting" && st.state !== "filled_awaiting_confirm") fail("submit_failed", `state is ${st.state}, not filled_awaiting_confirm`);
  const label = st.submitControl?.text?.trim();
  if (!label) fail("submit_failed", "the fill run did not record the final submit control (submitControl.text)");
  const endpoint = st.cdpEndpoint ?? `http://127.0.0.1:${process.env.APPLY_CDP_PORT ?? 9223}`;
  const base = httpBase(endpoint);

  // 1. same browser (CDP endpoint left alive by the fill run); restart on the same profile if it died
  let targets;
  try {
    targets = await getJson(`${base}/json/list`);
  } catch {
    const cmd = process.env.APPLY_BROWSER_CMD;
    const r = cmd ? spawnSync("sh", ["-c", cmd], { stdio: "ignore" }) : spawnSync("bash", [path.join(here, "apply-browser.sh")], { stdio: "ignore" });
    fail("submit_failed", r.status === 0
      ? "page_lost: the apply browser had closed; it was restarted on the same profile (logins kept) but the filled form is gone. Run Auto apply again."
      : "page_lost: the apply browser is not running and could not be restarted.", { pageLost: true });
  }
  const page = targets.find((t) => t.type === "page" && (t.id === st.targetId || sameDoc(t.url, st.url)));
  if (!page) fail("submit_failed", `page_lost: no open tab for ${st.url}. Run Auto apply again.`, { pageLost: true });

  const cdp = await connect(page.webSocketDebuggerUrl);
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  // Record what the site answers to the submit (status + a slice of the body, no request bodies: they hold
  // the candidate's data) so a "could not be sent" can be diagnosed afterwards → submit-network.json.
  await cdp.send("Network.enable").catch(() => undefined);
  const net = new Map();
  const consoleErrors = [];
  let clickedAt = 0;
  cdp.on(async (msg) => {
    const p = msg.params || {};
    if (msg.method === "Runtime.exceptionThrown") consoleErrors.push(String(p.exceptionDetails?.exception?.description ?? p.exceptionDetails?.text ?? "").slice(0, 300));
    if (msg.method === "Runtime.consoleAPICalled" && (p.type === "error" || p.type === "warning"))
      consoleErrors.push(`${p.type}: ${(p.args || []).map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 300)}`);
    if (!clickedAt) return;
    if (msg.method === "Network.requestWillBeSent") {
      const r = p.request || {};
      if (r.method !== "GET" || /turnstile|challenges\.cloudflare|captcha/i.test(r.url))
        net.set(p.requestId, { t: Date.now() - clickedAt, method: r.method, url: r.url.slice(0, 300), type: p.type });
    }
    if (msg.method === "Network.responseReceived" && net.has(p.requestId)) {
      Object.assign(net.get(p.requestId), { status: p.response?.status, mime: p.response?.mimeType });
    }
    if (msg.method === "Network.loadingFinished" && net.has(p.requestId)) {
      const e = net.get(p.requestId);
      const b = await cdp.send("Network.getResponseBody", { requestId: p.requestId }).catch(() => null);
      if (b && !b.base64Encoded) e.body = String(b.body).replace(/\s+/g, " ").slice(0, 1500);
    }
    if (msg.method === "Network.loadingFailed" && net.has(p.requestId)) {
      Object.assign(net.get(p.requestId), { failed: p.errorText, blocked: p.blockedReason });
    }
  });
  let guardAccepted = 0;
  cdp.on(async (msg) => {
    if (msg.method !== "Page.javascriptDialogOpening") return;
    const ours = String(msg.params.message ?? "").startsWith(GUARD_PREFIX);
    if (ours) guardAccepted++;
    await cdp.send("Page.handleJavaScriptDialog", { accept: ours }).catch(() => undefined);
  });
  const evaluate = async (expression) => {
    const r = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r.result.value;
  };
  const shot = async (name) => {
    try {
      const { data } = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
      writeFileSync(path.join(outDir, name), Buffer.from(data, "base64"));
      return name;
    } catch {
      return null;
    }
  };

  // 2. re-write the candidate's edits (edits.json from the Apply status panel) into the page
  let edits = [];
  try {
    edits = JSON.parse(readFileSync(path.join(outDir, "edits.json"), "utf-8"));
  } catch {
    /* no edits */
  }
  if (edits.length) {
    const results = await evaluate(`(${applyEdits.toString()})(${JSON.stringify(edits)})`);
    const bad = results.filter((r) => !r.ok).map((r) => r.label);
    if (bad.length) fail("submit_failed", `could not write your edit into the page for: ${bad.join("; ")}. Nothing was submitted.`);
    writeStatus({ editsApplied: results.length });
  }

  // 3. locate the exact control the fill run recorded
  await cdp.send("Page.bringToFront").catch(() => undefined);
  const want = label.replace(/\s+/g, " ").toLowerCase();
  const rect = await evaluate(`(() => {
    const norm = (s) => String(s || "").replace(/\\s+/g, " ").trim().toLowerCase();
    const els = [...document.querySelectorAll("button, input[type=submit], input[type=button], [role=button], a")]
      .filter((e) => norm(e.innerText || e.value || e.getAttribute("aria-label")) === ${JSON.stringify(want)});
    const el = els.find((e) => e.offsetParent !== null) ?? els[0];
    if (!el) return null;
    el.scrollIntoView({ block: "center" });
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, n: els.length, disabled: !!el.disabled };
  })()`);
  if (!rect) fail("submit_failed", `submit control "${label}" not found on the page`);
  if (rect.disabled) fail("submit_failed", `submit control "${label}" is disabled (a required field is probably still empty)`);
  await sleep(300);
  const before = await evaluate("location.href");
  const pre = await shot("pre-submit.png");

  // 4. the click (trusted mouse event → guard confirm() → accepted above → submit)
  clickedAt = Date.now();
  writeStatus({ submitStartedAt: now() });
  for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) {
    await cdp.send("Input.dispatchMouseEvent", { type, x: rect.x, y: rect.y, button: "left", clickCount: type === "mouseMoved" ? 0 : 1 });
  }

  // 5. wait for a confirmation page / message
  let confirmed = null, rejected = null, after = before, invalid = null;
  const until = Date.now() + WAIT_MS;
  while (Date.now() < until) {
    await sleep(700);
    try {
      const s = await evaluate(`({ url: location.href, text: (document.body?.innerText || "").slice(0, 20000),
        invalid: [...document.querySelectorAll("input:invalid, textarea:invalid, select:invalid, [aria-invalid=true]")].length })`);
      after = s.url;
      invalid = s.invalid;
      const r = s.text.match(REJECT_RE);
      if (r) {
        rejected = r[0];
        break;
      }
      const m = s.text.match(CONFIRM_RE);
      if (m) {
        confirmed = m[0];
        break;
      }
    } catch {
      /* navigating */
    }
  }
  await sleep(500);
  const conf = await shot("confirmation.png");
  cdp.close();
  try {
    writeFileSync(path.join(outDir, "submit-network.json"), JSON.stringify({ at: now(), requests: [...net.values()], consoleErrors: consoleErrors.slice(-30) }, null, 2));
  } catch {
    /* diagnostics only */
  }
  const common = { submitFinishedAt: now(), screenshots: { preSubmit: pre, confirmation: conf }, afterUrl: after, guardAccepted };
  if (rejected) {
    writeStatus({
      state: "submit_failed",
      submitted: false,
      submitError: `the site said "${rejected}" — the application was NOT sent. Fill again (the form is cleared) and check required fields / the CAPTCHA.`,
      ...common,
    });
    console.log(JSON.stringify({ state: "submit_failed", rejected }));
    return;
  }
  if (confirmed) {
    writeStatus({ state: "submitted", submitted: true, submittedAt: now(), confirmationText: confirmed, ...common });
    console.log(JSON.stringify({ state: "submitted", confirmationText: confirmed }));
    return;
  }
  writeStatus({
    state: "submit_uncertain",
    submitted: false,
    submitError: `no confirmation message within ${Math.round(WAIT_MS / 1000)} s${invalid ? `; ${invalid} field(s) marked invalid` : ""}${after !== before ? "; the page URL changed" : ""}. Check confirmation.png and your e-mail.`,
    ...common,
  });
  console.log(JSON.stringify({ state: "submit_uncertain" }));
}

main().catch((e) => fail("submit_failed", String(e?.message ?? e)));
