// Live browser view: see and control a tab of the apply browser (CDP 127.0.0.1:APPLY_CDP_PORT) from the
// phone. One LiveSession per tab: a CDP connection with Page.startScreencast (JPEG frames, kept in memory
// only, never written to disk) fanned out to SSE viewers, input forwarded as trusted CDP Input events,
// and JS dialogs (the apply guard's confirm() included) forwarded to the viewer to answer.
// A human answering the guard dialog here IS the human OK for that submit click. After a click (or
// Enter) the page is watched for the confirmation / rejection text, and status.json of the linked
// application is updated like scripts/apply-submit.mjs does (submitted → Sheet row 履歷送出).
import { existsSync, readdirSync, writeFileSync } from "node:fs";
import type { ServerResponse } from "node:http";
import path from "node:path";
import type { ApplyStatusFile } from "../shared/types.js";
import { LIVE_KEYS, type LiveAppLink, type LiveDialog, type LiveEvent, type LiveFrameMeta, type LiveInput, type LiveTab } from "../shared/live.js";
import { CONFIRM_RE, GUARD_PREFIX, REJECT_RE } from "../scripts/apply-confirm.mjs";
import { APPLY_DIR, CDP_ENDPOINT, applyDir, isSubmitting, pushSubmittedToSheet, writeStatus } from "./apply.js";
import { readJson } from "./artifacts.js";
import { getApplicationRec } from "./application.js";

const MAX_FPS = Math.max(1, Number(process.env.LIVE_MAX_FPS ?? 15) || 15);
const JPEG_QUALITY = 60;
const MAX_WIDTH = 1280;
/** Inputs per tab per second. */
export const LIVE_RATE_LIMIT = 30;
/** Network capture after a click on the recorded submit control (or an accepted guard dialog). */
const NET_CAPTURE_MS = Number(process.env.LIVE_NET_CAPTURE_MS ?? 30_000);
/** How long the page is watched for a confirmation text after a click / Enter. */
const WATCH_MS = Number(process.env.APPLY_SUBMIT_WAIT_MS ?? 20_000);
/** A session nobody watches or uses is closed after this. */
const IDLE_CLOSE_MS = 10_000;
const TARGET_ID_RE = /^[A-Za-z0-9_-]{8,64}$/;

export class LiveError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type CdpMsg = { id?: number; method?: string; params?: Record<string, any>; result?: any; error?: { message: string } };

/** Minimal CDP client on one page target (same shape as scripts/apply-submit.mjs). */
class Cdp {
  private id = 0;
  private pending = new Map<number, { res: (v: any) => void; rej: (e: Error) => void }>();
  private listeners = new Set<(m: CdpMsg) => void>();
  closed = false;
  onClose: () => void = () => undefined;
  private constructor(private ws: WebSocket) {
    ws.onmessage = (m) => {
      const msg = JSON.parse(String(m.data)) as CdpMsg;
      if (msg.id && this.pending.has(msg.id)) {
        const p = this.pending.get(msg.id)!;
        this.pending.delete(msg.id);
        msg.error ? p.rej(new Error(msg.error.message)) : p.res(msg.result);
      } else if (msg.method) for (const fn of this.listeners) fn(msg);
    };
    ws.onclose = () => {
      this.closed = true;
      for (const p of this.pending.values()) p.rej(new Error("CDP connection closed"));
      this.pending.clear();
      this.onClose();
    };
  }
  static connect(url: string): Promise<Cdp> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(url);
      const t = setTimeout(() => reject(new Error("CDP connect timeout")), 5000);
      ws.onopen = () => {
        clearTimeout(t);
        resolve(new Cdp(ws));
      };
      ws.onerror = () => {
        clearTimeout(t);
        reject(new Error("CDP connect failed"));
      };
    });
  }
  send<T = any>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (this.closed) return Promise.reject(new Error("CDP connection closed"));
    return new Promise((res, rej) => {
      const i = ++this.id;
      this.pending.set(i, { res, rej });
      this.ws.send(JSON.stringify({ id: i, method, params }));
    });
  }
  /** send() that gives up waiting after ms (the command still runs; e.g. a click that opened a dialog). */
  sendT<T = any>(method: string, params: Record<string, unknown>, ms: number): Promise<T | undefined> {
    const p = this.send<T>(method, params).catch(() => undefined);
    return Promise.race([p, new Promise<undefined>((r) => setTimeout(() => r(undefined), ms))]);
  }
  on(fn: (m: CdpMsg) => void) {
    this.listeners.add(fn);
  }
  close() {
    try {
      this.ws.close();
    } catch {
      /* gone */
    }
  }
}

interface RawTarget {
  id: string;
  type: string;
  title: string;
  url: string;
  webSocketDebuggerUrl?: string;
}

async function listTargets(): Promise<RawTarget[]> {
  try {
    const r = await fetch(`${CDP_ENDPOINT}/json/list`, { signal: AbortSignal.timeout(3000) });
    return ((await r.json()) as RawTarget[]).filter((t) => t.type === "page" && !t.url.startsWith("devtools://"));
  } catch {
    throw new LiveError(503, `The apply browser is not running (no CDP on ${CDP_ENDPOINT}). Start it with Auto apply or scripts/apply-browser.sh.`);
  }
}

const sameDoc = (a: string, b: string) => {
  try {
    const x = new URL(a), y = new URL(b);
    return x.origin === y.origin && x.pathname.replace(/\/$/, "") === y.pathname.replace(/\/$/, "");
  } catch {
    return false;
  }
};

/** The application whose status.json points at this tab: its targetId first, else the same URL. */
function linkedApp(targetId: string, url: string): { jd: string; file: string; st: ApplyStatusFile } | null {
  if (!existsSync(APPLY_DIR)) return null;
  let byUrl: { jd: string; file: string; st: ApplyStatusFile } | null = null;
  for (const jd of readdirSync(APPLY_DIR)) {
    if (!/^[\w.-]+$/.test(jd)) continue;
    const file = path.join(applyDir(jd), "status.json");
    const st = readJson<ApplyStatusFile>(file);
    if (!st?.state) continue;
    if (st.targetId === targetId) return { jd, file, st };
    if (!byUrl && st.url && url && (sameDoc(st.url, url) || (st.afterUrl && sameDoc(st.afterUrl, url)))) byUrl = { jd, file, st };
  }
  return byUrl;
}

const norm = (s: string | null | undefined) => String(s ?? "").replace(/\s+/g, " ").trim().toLowerCase();

interface Viewer {
  res: ServerResponse;
  blocked: boolean;
  /** Latest frame not yet written because the socket was backed up. */
  pendingFrame: string | null;
}

interface Watch {
  until: number;
  /** Click on the recorded submit control, or the guard dialog was accepted. */
  submitish: boolean;
  preConfirm: boolean;
  preReject: boolean;
  timer: NodeJS.Timeout | null;
}

class LiveSession {
  viewers = new Set<Viewer>();
  screencasting = false;
  dialog: LiveDialog | null = null;
  lastFrame: string | null = null;
  seq = 0;
  private nextAckAt = 0;
  private idleTimer: NodeJS.Timeout | null = null;
  private inputTimes: number[] = [];
  private watch: Watch | null = null;
  private guardAccepted = 0;
  private net: { until: number; timer: NodeJS.Timeout | null; startedAt: number; requests: Map<string, Record<string, unknown>>; consoleErrors: string[] } | null = null;

  constructor(public targetId: string, public url: string, public title: string, private cdp: Cdp, onDead: () => void) {
    cdp.onClose = () => {
      this.broadcast({ type: "closed", reason: "the tab was closed or the browser stopped" });
      for (const v of this.viewers) v.res.end();
      this.viewers.clear();
      this.stopTimers();
      onDead();
    };
    cdp.on((m) => this.onEvent(m));
  }

  async init(): Promise<void> {
    await this.cdp.send("Page.enable");
    // keyboard focus and :focus styles work while the Windows window is not focused
    await this.cdp.send("Emulation.setFocusEmulationEnabled", { enabled: true }).catch(() => undefined);
    this.scheduleIdleClose();
  }

  // ------------------------------------------------------------------ events from the page

  private onEvent(m: CdpMsg): void {
    const p = m.params ?? {};
    switch (m.method) {
      case "Page.screencastFrame": {
        // ack every frame; pacing the ack caps the frame rate (Chrome waits for the ack)
        // (Chrome keeps two frames in flight, so the acks are spaced one interval apart)
        const at = Math.max(Date.now(), this.nextAckAt);
        this.nextAckAt = at + 1000 / MAX_FPS;
        setTimeout(() => {
          this.cdp.send("Page.screencastFrameAck", { sessionId: p.sessionId }).catch(() => undefined);
        }, at - Date.now());
        this.pushFrame(String(p.data), p.metadata as LiveFrameMeta);
        break;
      }
      case "Page.javascriptDialogOpening":
        this.dialog = {
          type: p.type,
          message: String(p.message ?? ""),
          defaultPrompt: p.defaultPrompt || undefined,
          guard: String(p.message ?? "").startsWith(GUARD_PREFIX),
          url: p.url,
        };
        this.broadcast({ type: "dialog", dialog: this.dialog });
        break;
      case "Page.javascriptDialogClosed":
        this.dialog = null;
        this.broadcast({ type: "dialog-closed", accepted: !!p.result });
        break;
      case "Page.frameNavigated":
        if (!p.frame?.parentId) {
          this.url = p.frame.url;
          this.cdp.sendT<{ result: { value: string } }>("Runtime.evaluate", { expression: "document.title", returnByValue: true }, 1500).then((r) => {
            this.title = r?.result?.value ?? this.title;
            this.broadcast({ type: "page", url: this.url, title: this.title });
          });
        }
        break;
      case "Inspector.detached":
      case "Inspector.targetCrashed":
        this.cdp.close();
        break;
    }
    if (this.net && m.method?.startsWith("Network.")) this.onNetwork(m.method, p);
    if (this.net && (m.method === "Runtime.exceptionThrown" || m.method === "Runtime.consoleAPICalled")) {
      if (m.method === "Runtime.exceptionThrown") this.net.consoleErrors.push(String(p.exceptionDetails?.exception?.description ?? p.exceptionDetails?.text ?? "").slice(0, 300));
      else if (p.type === "error" || p.type === "warning")
        this.net.consoleErrors.push(`${p.type}: ${(p.args || []).map((a: any) => a.value ?? a.description ?? "").join(" ").slice(0, 300)}`);
    }
  }

  private pushFrame(data: string, meta: LiveFrameMeta): void {
    this.seq++;
    const ev: LiveEvent = {
      type: "frame",
      seq: this.seq,
      data,
      meta: {
        deviceWidth: meta.deviceWidth,
        deviceHeight: meta.deviceHeight,
        offsetTop: meta.offsetTop ?? 0,
        pageScaleFactor: meta.pageScaleFactor ?? 1,
        scrollOffsetX: meta.scrollOffsetX ?? 0,
        scrollOffsetY: meta.scrollOffsetY ?? 0,
        timestamp: meta.timestamp,
      },
      at: Date.now(),
    };
    const line = `data: ${JSON.stringify(ev)}\n\n`;
    this.lastFrame = line;
    for (const v of this.viewers) {
      if (v.blocked) v.pendingFrame = line; // drop older frames for a slow client, keep the newest
      else this.write(v, line);
    }
  }

  private write(v: Viewer, line: string): void {
    if (!v.res.write(line)) {
      v.blocked = true;
      v.res.once("drain", () => {
        v.blocked = false;
        const next = v.pendingFrame;
        v.pendingFrame = null;
        if (next) this.write(v, next);
      });
    }
  }

  broadcast(ev: LiveEvent): void {
    const line = `data: ${JSON.stringify(ev)}\n\n`;
    for (const v of this.viewers) v.res.write(line);
  }

  // ------------------------------------------------------------------ viewers / screencast

  async addViewer(res: ServerResponse): Promise<Viewer> {
    const v: Viewer = { res, blocked: false, pendingFrame: null };
    this.viewers.add(v);
    this.cancelIdleClose();
    const link = linkedApp(this.targetId, this.url);
    const app: LiveAppLink | null = link ? { jd: link.jd, state: link.st.state, submitControl: link.st.submitControl?.text ?? null } : null;
    res.write(`data: ${JSON.stringify({ type: "hello", targetId: this.targetId, url: this.url, title: this.title, dialog: this.dialog, app } satisfies LiveEvent)}\n\n`);
    if (this.lastFrame) res.write(this.lastFrame);
    if (!this.screencasting) {
      this.screencasting = true;
      // a background tab does not paint, so it sends no frames
      await this.cdp.sendT("Page.bringToFront", {}, 2000);
      await this.cdp.send("Page.startScreencast", { format: "jpeg", quality: JPEG_QUALITY, maxWidth: MAX_WIDTH, everyNthFrame: 1 }).catch(() => undefined);
      const before = this.seq;
      // a page that is idle may not repaint: send one screenshot so the viewer sees something
      setTimeout(() => {
        if (this.seq === before && this.screencasting && !this.dialog) void this.snapshotFrame();
      }, 1200);
    }
    return v;
  }

  removeViewer(v: Viewer): void {
    this.viewers.delete(v);
    if (this.viewers.size === 0) {
      if (this.screencasting) {
        this.screencasting = false;
        this.cdp.send("Page.stopScreencast").catch(() => undefined);
      }
      this.scheduleIdleClose();
    }
  }

  private async snapshotFrame(): Promise<void> {
    const [shot, metrics] = await Promise.all([
      this.cdp.sendT<{ data: string }>("Page.captureScreenshot", { format: "jpeg", quality: JPEG_QUALITY }, 3000),
      this.cdp.sendT<any>("Page.getLayoutMetrics", {}, 3000),
    ]);
    const vp = metrics?.cssVisualViewport ?? metrics?.visualViewport;
    if (!shot?.data || !vp) return;
    this.pushFrame(shot.data, {
      deviceWidth: vp.clientWidth,
      deviceHeight: vp.clientHeight,
      offsetTop: 0,
      pageScaleFactor: vp.scale ?? 1,
      scrollOffsetX: vp.pageX ?? 0,
      scrollOffsetY: vp.pageY ?? 0,
    });
  }

  private scheduleIdleClose(): void {
    this.cancelIdleClose();
    this.idleTimer = setTimeout(() => {
      if (this.viewers.size === 0 && !this.watch && !this.net && !this.dialog) this.cdp.close();
      else this.scheduleIdleClose();
    }, IDLE_CLOSE_MS);
  }
  private cancelIdleClose(): void {
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.idleTimer = null;
  }
  private stopTimers(): void {
    this.cancelIdleClose();
    if (this.watch?.timer) clearTimeout(this.watch.timer);
    if (this.net?.timer) clearTimeout(this.net.timer);
  }

  // ------------------------------------------------------------------ input

  private rateLimited(): boolean {
    const now = Date.now();
    this.inputTimes = this.inputTimes.filter((t) => now - t < 1000);
    if (this.inputTimes.length >= LIVE_RATE_LIMIT) return true;
    this.inputTimes.push(now);
    return false;
  }

  private async evaluate<T>(expression: string, ms = 1500): Promise<T | undefined> {
    const r = await this.cdp.sendT<{ result?: { value?: T }; exceptionDetails?: unknown }>("Runtime.evaluate", { expression, returnByValue: true }, ms);
    return r && !r.exceptionDetails ? r.result?.value : undefined;
  }

  async input(inp: LiveInput): Promise<Record<string, unknown>> {
    if (this.rateLimited()) throw new LiveError(429, `More than ${LIVE_RATE_LIMIT} inputs per second`);
    this.scheduleIdleClose();
    if (this.dialog && inp.type !== "scroll") throw new LiveError(409, `A ${this.dialog.type} dialog is open on the page: answer it first`);
    const c = this.cdp;
    if (inp.type === "click") {
      const probe = await this.evaluate<{ label: string; preConfirm: boolean; preReject: boolean }>(`(() => {
        const el = document.elementFromPoint(${inp.x}, ${inp.y});
        const ctl = el && el.closest("button, input[type=submit], input[type=button], a, [role=button]");
        const text = (document.body?.innerText || "").slice(0, 20000);
        return { label: ctl ? String(ctl.innerText || ctl.value || ctl.getAttribute("aria-label") || "") : "",
          preConfirm: new RegExp(${JSON.stringify(CONFIRM_RE.source)}, "i").test(text),
          preReject: new RegExp(${JSON.stringify(REJECT_RE.source)}, "i").test(text) };
      })()`, 800);
      const link = linkedApp(this.targetId, this.url);
      const submitish = !!(probe?.label && link?.st.submitControl?.text && norm(probe.label) === norm(link.st.submitControl.text));
      if (submitish) await this.startNetCapture();
      await c.sendT("Input.dispatchMouseEvent", { type: "mouseMoved", x: inp.x, y: inp.y }, 1000);
      await c.sendT("Input.dispatchMouseEvent", { type: "mousePressed", x: inp.x, y: inp.y, button: "left", clickCount: 1 }, 1000);
      // the release may open a dialog (guard confirm()), and then CDP answers only after it closes
      await c.sendT("Input.dispatchMouseEvent", { type: "mouseReleased", x: inp.x, y: inp.y, button: "left", clickCount: 1 }, 400);
      this.startWatch({ submitish, preConfirm: !!probe?.preConfirm, preReject: !!probe?.preReject });
      return { ok: true, control: probe?.label || null, submitControl: submitish, dialog: this.dialog };
    }
    if (inp.type === "scroll") {
      if (this.dialog) throw new LiveError(409, "A dialog is open on the page: answer it first");
      await c.sendT("Input.dispatchMouseEvent", { type: "mouseWheel", x: inp.x ?? 100, y: inp.y ?? 100, deltaX: 0, deltaY: inp.deltaY }, 1000);
      return { ok: true };
    }
    if (inp.type === "key") {
      const k = LIVE_KEYS[inp.key]!;
      const key = inp.key === "Space" ? " " : inp.key;
      const pre = inp.key === "Enter" ? await this.evaluate<{ c: boolean; r: boolean }>(`(() => { const t = (document.body?.innerText || "").slice(0, 20000);
        return { c: new RegExp(${JSON.stringify(CONFIRM_RE.source)}, "i").test(t), r: new RegExp(${JSON.stringify(REJECT_RE.source)}, "i").test(t) }; })()`, 800) : undefined;
      await c.sendT("Input.dispatchKeyEvent", { type: k.text ? "keyDown" : "rawKeyDown", key, code: k.code, windowsVirtualKeyCode: k.vk, text: k.text, unmodifiedText: k.text }, 400);
      await c.sendT("Input.dispatchKeyEvent", { type: "keyUp", key, code: k.code, windowsVirtualKeyCode: k.vk }, 400);
      if (inp.key === "Enter") this.startWatch({ submitish: false, preConfirm: !!pre?.c, preReject: !!pre?.r });
      return { ok: true, dialog: this.dialog };
    }
    await c.sendT("Input.insertText", { text: inp.text }, 1500);
    return { ok: true };
  }

  async answerDialog(accept: boolean, promptText?: string): Promise<Record<string, unknown>> {
    const d = this.dialog;
    if (!d) throw new LiveError(409, "No dialog is open on this tab");
    if (d.guard && accept) {
      // the candidate's OK in the live view is the human OK for this submit
      this.guardAccepted++;
      await this.startNetCapture();
      if (this.watch) this.watch.submitish = true;
    }
    await this.cdp.sendT("Page.handleJavaScriptDialog", { accept, ...(promptText !== undefined ? { promptText } : {}) }, 3000);
    this.dialog = null;
    if (d.guard && accept) this.startWatch({ submitish: true, preConfirm: this.watch?.preConfirm ?? false, preReject: this.watch?.preReject ?? false });
    return { ok: true, guard: d.guard, accepted: accept };
  }

  // ------------------------------------------------------------------ after a click: confirmation + network

  private startWatch(w: { submitish: boolean; preConfirm: boolean; preReject: boolean }): void {
    if (!linkedApp(this.targetId, this.url)) return; // nothing to update
    if (this.watch) {
      this.watch.until = Date.now() + WATCH_MS;
      this.watch.submitish ||= w.submitish;
      return;
    }
    this.watch = { until: Date.now() + WATCH_MS, ...w, timer: null };
    this.tickWatch();
  }

  private tickWatch(): void {
    const w = this.watch;
    if (!w) return;
    w.timer = setTimeout(async () => {
      if (this.watch !== w || this.cdp.closed) return;
      if (Date.now() > w.until) {
        this.watch = null;
        return;
      }
      if (!this.dialog) {
        const s = await this.evaluate<{ url: string; text: string }>(`({ url: location.href, text: (document.body?.innerText || "").slice(0, 20000) })`);
        if (s) {
          const rej = s.text.match(REJECT_RE);
          const conf = s.text.match(CONFIRM_RE);
          if (rej && !w.preReject && (w.submitish || this.guardAccepted > 0)) return void this.finish("submit_failed", rej[0], s.url);
          if (conf && !w.preConfirm) return void this.finish("submitted", conf[0], s.url);
        }
      }
      this.tickWatch();
    }, 700);
  }

  private async finish(state: "submitted" | "submit_failed", text: string, afterUrl: string): Promise<void> {
    this.watch = null;
    const link = linkedApp(this.targetId, this.url) ?? linkedApp(this.targetId, afterUrl);
    if (!link) return;
    const cur = readJson<ApplyStatusFile>(link.file);
    if (!cur || cur.state === "submitted" || cur.state === "submitting" || isSubmitting(link.jd)) return;
    const now = new Date().toISOString();
    const out = applyDir(link.jd);
    // the confirmation page as evidence, like Confirm & submit (a screenshot, not a screencast frame)
    const shot = await this.cdp.sendT<{ data: string }>("Page.captureScreenshot", { format: "png", captureBeyondViewport: true }, 5000);
    if (shot?.data) writeFileSync(path.join(out, "confirmation.png"), Buffer.from(shot.data, "base64"));
    const common = { submitFinishedAt: now, afterUrl, guardAccepted: (cur.guardAccepted ?? 0) + this.guardAccepted, submittedVia: "live" as const };
    const next: ApplyStatusFile =
      state === "submitted"
        ? { ...cur, ...common, state, submitted: true, submittedAt: now, confirmationText: text, submitError: undefined }
        : {
            ...cur,
            ...common,
            state,
            submitted: false,
            submitError: `the site said "${text}" — the application was NOT sent (submitted from the Live browser view). Check required fields / the CAPTCHA and try again.`,
          };
    writeStatus(link.file, next);
    this.broadcast({ type: "status", jd: link.jd, state, confirmationText: state === "submitted" ? text : null, error: next.submitError ?? null });
    if (state === "submitted") {
      const rec = getApplicationRec(link.jd);
      if (rec) await pushSubmittedToSheet(rec, out, next);
    }
  }

  private async startNetCapture(): Promise<void> {
    if (this.net) {
      this.net.until = Date.now() + NET_CAPTURE_MS;
      return;
    }
    this.net = { until: Date.now() + NET_CAPTURE_MS, timer: null, startedAt: Date.now(), requests: new Map(), consoleErrors: [] };
    await this.cdp.sendT("Network.enable", {}, 2000);
    await this.cdp.sendT("Runtime.enable", {}, 2000);
    const tick = () => {
      const n = this.net;
      if (!n) return;
      n.timer = setTimeout(() => (Date.now() >= n.until ? this.stopNetCapture() : tick()), Math.max(200, n.until - Date.now()));
    };
    tick();
  }

  private onNetwork(method: string, p: Record<string, any>): void {
    const n = this.net!;
    if (method === "Network.requestWillBeSent") {
      const r = p.request || {};
      if (r.method !== "GET" || /turnstile|challenges\.cloudflare|captcha/i.test(r.url))
        n.requests.set(p.requestId, { t: Date.now() - n.startedAt, method: r.method, url: String(r.url).slice(0, 300), type: p.type });
    } else if (method === "Network.responseReceived" && n.requests.has(p.requestId)) {
      Object.assign(n.requests.get(p.requestId)!, { status: p.response?.status, mime: p.response?.mimeType });
    } else if (method === "Network.loadingFinished" && n.requests.has(p.requestId)) {
      const e = n.requests.get(p.requestId)!;
      this.cdp.sendT<{ body: string; base64Encoded: boolean }>("Network.getResponseBody", { requestId: p.requestId }, 2000).then((b) => {
        if (b && !b.base64Encoded) e.body = String(b.body).replace(/\s+/g, " ").slice(0, 1500);
      });
    } else if (method === "Network.loadingFailed" && n.requests.has(p.requestId)) {
      Object.assign(n.requests.get(p.requestId)!, { failed: p.errorText, blocked: p.blockedReason });
    }
  }

  private stopNetCapture(): void {
    const n = this.net;
    if (!n) return;
    this.net = null;
    this.cdp.send("Network.disable").catch(() => undefined);
    this.cdp.send("Runtime.disable").catch(() => undefined);
    const link = linkedApp(this.targetId, this.url);
    if (!link) return;
    try {
      // same shape as scripts/apply-submit.mjs (request bodies are never recorded: they hold the candidate's data)
      writeFileSync(
        path.join(applyDir(link.jd), "submit-network.json"),
        JSON.stringify({ at: new Date().toISOString(), via: "live", requests: [...n.requests.values()], consoleErrors: n.consoleErrors.slice(-30) }, null, 2),
      );
    } catch {
      /* diagnostics only */
    }
  }

  /** For tests and shutdown: write the capture now. */
  flushNetwork(): void {
    if (this.net?.timer) clearTimeout(this.net.timer);
    this.stopNetCapture();
  }
}

const sessions = new Map<string, LiveSession>();
const connecting = new Map<string, Promise<LiveSession>>();

function checkTargetId(id: string): void {
  if (!TARGET_ID_RE.test(id)) throw new LiveError(400, "Bad tab id");
}

/** Only tabs that exist on the apply browser's CDP endpoint. */
async function session(targetId: string): Promise<LiveSession> {
  checkTargetId(targetId);
  const s = sessions.get(targetId);
  if (s) return s;
  const pending = connecting.get(targetId);
  if (pending) return pending;
  const p = (async () => {
    const t = (await listTargets()).find((x) => x.id === targetId);
    if (!t?.webSocketDebuggerUrl) throw new LiveError(404, "No such tab in the apply browser");
    const cdp = await Cdp.connect(t.webSocketDebuggerUrl);
    const ses = new LiveSession(targetId, t.url, t.title, cdp, () => sessions.delete(targetId));
    await ses.init();
    sessions.set(targetId, ses);
    return ses;
  })();
  connecting.set(targetId, p);
  try {
    return await p;
  } finally {
    connecting.delete(targetId);
  }
}

export async function liveTabs(): Promise<LiveTab[]> {
  const targets = await listTargets();
  return targets.map((t) => {
    const s = sessions.get(t.id);
    return {
      id: t.id,
      title: s?.title || t.title,
      url: s?.url || t.url,
      jd: linkedApp(t.id, t.url)?.jd ?? null,
      viewers: s?.viewers.size ?? 0,
      screencasting: s?.screencasting ?? false,
    };
  });
}

/** SSE: hello, then frames, dialogs, navigations and status changes until the viewer disconnects. */
export async function liveStream(targetId: string, req: { on(ev: "close", fn: () => void): void }, res: ServerResponse): Promise<void> {
  const s = await session(targetId);
  res.writeHead(200, {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-store, no-transform",
    connection: "keep-alive",
    "x-accel-buffering": "no",
  });
  res.flushHeaders();
  let closed = false;
  const ping = setInterval(() => res.write(": ping\n\n"), 15_000);
  let viewer: Viewer | null = null;
  req.on("close", () => {
    closed = true;
    clearInterval(ping);
    if (viewer) s.removeViewer(viewer);
  });
  viewer = await s.addViewer(res);
  if (closed) s.removeViewer(viewer);
}

export function parseLiveInput(raw: unknown): LiveInput {
  const b = (raw ?? {}) as Record<string, unknown>;
  const num = (v: unknown, lo: number, hi: number, name: string) => {
    if (typeof v !== "number" || !Number.isFinite(v) || v < lo || v > hi) throw new LiveError(400, `${name} must be a number in ${lo}..${hi}`);
    return v;
  };
  switch (b.type) {
    case "click":
      return { type: "click", x: num(b.x, 0, 20000, "x"), y: num(b.y, 0, 20000, "y") };
    case "scroll":
      return {
        type: "scroll",
        deltaY: num(b.deltaY, -10000, 10000, "deltaY"),
        x: b.x === undefined ? undefined : num(b.x, 0, 20000, "x"),
        y: b.y === undefined ? undefined : num(b.y, 0, 20000, "y"),
      };
    case "key":
      if (typeof b.key !== "string" || !(b.key in LIVE_KEYS)) throw new LiveError(400, `key must be one of ${Object.keys(LIVE_KEYS).join(", ")}`);
      return { type: "key", key: b.key };
    case "text":
      if (typeof b.text !== "string" || !b.text || b.text.length > 4000) throw new LiveError(400, "text must be a string of 1..4000 chars");
      return { type: "text", text: b.text };
    default:
      throw new LiveError(400, "type must be click | scroll | key | text");
  }
}

export async function liveInput(targetId: string, raw: unknown): Promise<Record<string, unknown>> {
  const inp = parseLiveInput(raw);
  return (await session(targetId)).input(inp);
}

export async function liveDialog(targetId: string, raw: unknown): Promise<Record<string, unknown>> {
  const b = (raw ?? {}) as Record<string, unknown>;
  if (typeof b.accept !== "boolean") throw new LiveError(400, "Body must be { accept: true|false, promptText? }");
  const promptText = typeof b.promptText === "string" ? b.promptText.slice(0, 4000) : undefined;
  checkTargetId(targetId);
  const s = sessions.get(targetId);
  if (!s) throw new LiveError(409, "No dialog is open on this tab");
  return s.answerDialog(b.accept, promptText);
}

/** Test hook: write pending network captures now. */
export function flushLiveNetwork(): void {
  for (const s of sessions.values()) s.flushNetwork();
}
