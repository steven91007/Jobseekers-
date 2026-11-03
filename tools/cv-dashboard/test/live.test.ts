// Live browser view: screencast frames over SSE, input mapped from frame coordinates, the guard's
// confirm() answered from the viewer, and the submit detected afterwards. Runs only against a local
// fixture page in its own Chromium (temp profile, random CDP port), never the real apply browser.
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { frameToPage, type LiveEvent, type LiveFrameMeta } from "../shared/live";

const dataDir = mkdtempSync(path.join(tmpdir(), "cvd-live-"));
const sheetOut = path.join(dataDir, "sheet_rows.jsonl");
const CDP_PORT = 9800 + Math.floor(Math.random() * 190);
process.env.CV_DASHBOARD_DATA = dataDir;
process.env.CV_SHEET_PUSH = path.resolve(__dirname, "fixtures/fake-sheet-push.py");
process.env.FAKE_SHEET_OUT = sheetOut;
process.env.APPLY_BROWSER_CMD = "echo fake browser up";
process.env.APPLY_CDP_PORT = String(CDP_PORT);
process.env.APPLY_HEADLESS = "1"; // no browser window on the desktop during tests
process.env.APPLY_SUBMIT_WAIT_MS = "15000";
process.env.LIVE_NET_CAPTURE_MS = "3000";

const BROWSER_SH = path.resolve(__dirname, "../scripts/apply-browser.sh");
const GUARD_JS = readFileSync(path.resolve(__dirname, "../scripts/apply-guard.js"), "utf-8");
const chromium = (() => {
  try {
    const dir = path.join(homedir(), ".cache", "ms-playwright");
    const c = readdirSync(dir).filter((d) => d.startsWith("chromium-")).sort().pop();
    const bin = c && path.join(dir, c, "chrome-linux64", "chrome");
    return bin && existsSync(bin) ? bin : null;
  } catch {
    return null;
  }
})();

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
async function until<T>(fn: () => T | undefined | null | false, ms = 8000, step = 50): Promise<T> {
  const end = Date.now() + ms;
  for (;;) {
    const v = fn();
    if (v) return v;
    if (Date.now() > end) throw new Error("timed out");
    await sleep(step);
  }
}

// ------------------------------------------------------------------ fixture site
const posted: string[] = [];
const fixture = http.createServer((req, res) => {
  if (req.method === "POST") {
    let b = "";
    req.on("data", (c) => (b += c));
    req.on("end", () => {
      posted.push(b);
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      res.end("<!doctype html><h1>Vielen Dank für deine Bewerbung!</h1>");
    });
    return;
  }
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  if (req.url === "/anim") {
    return void res.end(`<!doctype html><div id="n" style="font-size:80px">0</div>
      <script>let i = 0; (function f() { document.getElementById("n").textContent = ++i; requestAnimationFrame(f); })()</script>`);
  }
  res.end(`<!doctype html><title>Fixture job</title><body style="margin:20px;font:18px sans-serif">
    <form method="post" action="/apply">
      <p><label><input type="checkbox" id="consent" name="consent" style="width:40px;height:40px"> Datenschutz</label></p>
      <p><label for="salary">Gehaltsvorstellung</label><br><input id="salary" name="salary" style="width:300px;height:40px;font-size:20px"></p>
      <p><button type="submit" style="width:320px;height:60px;font-size:20px">Bewerbung abschicken</button></p>
    </form><div style="height:2000px"></div></body>`);
});

/** The test's own CDP session on a new tab (setup + assertions; the guard as --init-script would add it). */
async function cdpPage(url: string, initScript?: string) {
  const t = (await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?about:blank`, { method: "PUT" })).json()) as { id: string; webSocketDebuggerUrl: string };
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r, j) => ((ws.onopen = r), (ws.onerror = j)));
  let id = 0;
  const pend = new Map<number, (v: any) => void>();
  ws.onmessage = (m) => {
    const msg = JSON.parse(String(m.data));
    if (msg.id) pend.get(msg.id)?.(msg.result);
  };
  const send = (method: string, params = {}) =>
    new Promise<any>((r) => {
      const i = ++id;
      pend.set(i, r);
      ws.send(JSON.stringify({ id: i, method, params }));
    });
  await send("Page.enable");
  if (initScript) await send("Page.addScriptToEvaluateOnNewDocument", { source: initScript });
  await send("Page.navigate", { url });
  await sleep(800);
  const evaluate = async (expression: string) => (await send("Runtime.evaluate", { expression, returnByValue: true })).result.value;
  return { id: t.id, send, evaluate, close: () => ws.close() };
}

let base = "";
let closeApi: () => void = () => undefined;
const H = { "content-type": "application/json", origin: "http://127.0.0.1:5173" };
const live = (p: string) => `${base}/api/apply/live/${p}`;
const input = (tab: string, body: unknown, headers: Record<string, string> = {}) =>
  fetch(live(`${tab}/input`), { method: "POST", headers: { ...H, ...headers }, body: JSON.stringify(body) });

type Ev = LiveEvent & { _t: number };
function openStream(tab: string) {
  const ac = new AbortController();
  const events: Ev[] = [];
  const ready = fetch(live(`${tab}/stream`), { signal: ac.signal }).then(async (res) => {
    const reader = res.body!.getReader();
    const dec = new TextDecoder();
    let buf = "";
    void (async () => {
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i: number;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const line = buf.slice(0, i).split("\n").find((l) => l.startsWith("data: "));
            buf = buf.slice(i + 2);
            if (line) events.push({ ...JSON.parse(line.slice(6)), _t: Date.now() });
          }
        }
      } catch {
        /* aborted */
      }
    })();
    return res;
  });
  return { events, ready, close: () => ac.abort() };
}
const frames = (evs: Ev[]) => evs.filter((e): e is Extract<Ev, { type: "frame" }> => e.type === "frame");

/** Width/height from the JPEG's SOF marker. */
function jpegSize(b64: string): { w: number; h: number } {
  const b = Buffer.from(b64, "base64");
  for (let i = 2; i < b.length - 9; ) {
    if (b[i] !== 0xff) return { w: 0, h: 0 };
    const m = b[i + 1];
    if (m >= 0xc0 && m <= 0xc2) return { h: b.readUInt16BE(i + 5), w: b.readUInt16BE(i + 7) };
    i += 2 + b.readUInt16BE(i + 2);
  }
  return { w: 0, h: 0 };
}

/** What the phone does: a page point shown on a 390 px wide image → tap → frameToPage. */
function tapFor(cssX: number, cssY: number, meta: LiveFrameMeta) {
  const dispW = 390;
  const dispH = (dispW * meta.deviceHeight) / meta.deviceWidth;
  return frameToPage((cssX * dispW) / meta.deviceWidth, (cssY * dispH) / meta.deviceHeight, dispW, dispH, meta);
}

const sheetRows = () => (existsSync(sheetOut) ? readFileSync(sheetOut, "utf-8").trim().split("\n").map((l) => JSON.parse(l)) : []);

describe.skipIf(!chromium)("Live browser view (local fixture, real Chromium)", () => {
  const profile = path.join(dataDir, "profile");
  const env = { ...process.env, APPLY_CDP_PORT: String(CDP_PORT), APPLY_BROWSER_DIR: profile };
  let fx = "";
  let page: Awaited<ReturnType<typeof cdpPage>>;
  const statusFile = path.join(dataDir, "apply", "Taktile", "status.json");

  beforeAll(async () => {
    const { createApp } = await import("../server/http");
    const { server } = createApp();
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    closeApi = () => server.close();
    await new Promise<void>((r) => fixture.listen(0, "127.0.0.1", r));
    fx = `http://127.0.0.1:${(fixture.address() as AddressInfo).port}`;
    execFileSync("bash", [BROWSER_SH], { env, encoding: "utf-8" });
    page = await cdpPage(`${fx}/job`, GUARD_JS);
    expect(await page.evaluate("window.__applyGuard === true")).toBe(true);
    // the state a fill run leaves for this tab
    mkdirSync(path.dirname(statusFile), { recursive: true });
    writeFileSync(
      statusFile,
      JSON.stringify({
        state: "filled_awaiting_confirm", reachedReview: true, submitted: false, url: `${fx}/job`, submitControl: { text: "Bewerbung abschicken" },
        pendingCount: 0, blocker: null, finishedAt: new Date().toISOString(), cdpEndpoint: `http://127.0.0.1:${CDP_PORT}`, targetId: page.id,
      }),
    );
  }, 30_000);
  afterAll(async () => {
    page?.close();
    try {
      const v = (await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json()) as { webSocketDebuggerUrl: string };
      const ws = new WebSocket(v.webSocketDebuggerUrl);
      await new Promise((r) => (ws.onopen = r));
      ws.send(JSON.stringify({ id: 1, method: "Browser.close" }));
      for (let i = 0; i < 40 && spawnSync("curl", ["-sf", `http://127.0.0.1:${CDP_PORT}/json/version`]).status === 0; i++) await sleep(250);
    } catch {
      /* down */
    }
    closeApi();
    fixture.close();
    rmSync(dataDir, { recursive: true, force: true });
  });

  it("frames, mapped click, insertText, guard dialog → accept → submitted + Sheet + network capture", async () => {
    const tabs = (await (await fetch(live("tabs"))).json()) as { id: string; url: string; jd: string | null }[];
    expect(tabs.find((t) => t.id === page.id)).toMatchObject({ url: `${fx}/job`, jd: "Taktile" });

    const s = openStream(page.id);
    expect((await s.ready).headers.get("content-type")).toContain("text/event-stream");
    const hello = await until(() => s.events.find((e) => e.type === "hello"));
    expect(hello).toMatchObject({ targetId: page.id, app: { jd: "Taktile", state: "filled_awaiting_confirm", submitControl: "Bewerbung abschicken" }, dialog: null });
    const f0 = await until(() => frames(s.events)[0]);
    expect(f0.meta.deviceWidth).toBeGreaterThan(100);
    const size = jpegSize(f0.data);
    expect(size.w).toBeGreaterThan(0);
    expect(size.w).toBeLessThanOrEqual(1280);
    const meta = f0.meta;

    // tap on the checkbox as seen on a 390 px phone → trusted click fills it
    const box = await page.evaluate(`(() => { const r = document.getElementById("consent").getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
    const n0 = frames(s.events).length;
    const t0 = Date.now();
    const r1 = await input(page.id, { type: "click", ...tapFor(box.x, box.y, meta) });
    const rtt = Date.now() - t0;
    expect(r1.status).toBe(200);
    const next = await until(() => frames(s.events)[n0]);
    console.log(`[live] input RTT ${rtt} ms, click → next frame ${next._t - t0} ms, frame ${size.w}x${size.h} ${Math.round(f0.data.length / 1024)} KiB b64`);
    expect(await page.evaluate(`document.getElementById("consent").checked`)).toBe(true);

    // tap the salary field, then "Type"
    const sal = await page.evaluate(`(() => { const r = document.getElementById("salary").getBoundingClientRect(); return { x: r.x + 20, y: r.y + r.height / 2 }; })()`);
    expect((await input(page.id, { type: "click", ...tapFor(sal.x, sal.y, meta) })).status).toBe(200);
    expect((await input(page.id, { type: "text", text: "55000" })).status).toBe(200);
    expect(await page.evaluate(`document.getElementById("salary").value`)).toBe("55000");
    expect((await input(page.id, { type: "key", key: "Tab" })).status).toBe(200);
    expect(await page.evaluate(`document.activeElement.innerText`)).toBe("Bewerbung abschicken");

    // guards: evil origin, non-JSON, unknown tab, bad input
    expect((await input(page.id, { type: "click", x: 1, y: 1 }, { origin: "https://evil.example" })).status).toBe(403);
    expect((await fetch(live(`${page.id}/input`), { method: "POST", headers: { origin: H.origin, "content-type": "text/plain" }, body: "{}" })).status).toBe(415);
    expect((await fetch(live(`${page.id}/dialog`), { method: "POST", headers: { ...H, origin: "https://evil.example" }, body: '{"accept":true}' })).status).toBe(403);
    // DNS rebinding onto 127.0.0.1: a foreign Host header is refused (fetch cannot set Host, node:http can)
    const rebind = await new Promise<number>((r) => http.get(live(`${page.id}/stream`), { headers: { host: "evil.example" } }, (res) => (res.resume(), r(res.statusCode ?? 0))));
    expect(rebind).toBe(403);
    expect((await input("NOSUCHTAB0000000", { type: "click", x: 1, y: 1 })).status).toBe(404);
    expect((await input(page.id, { type: "key", key: "F12" })).status).toBe(400);
    expect((await input(page.id, { type: "click", x: -5, y: 1 })).status).toBe(400);
    expect(await page.evaluate(`document.getElementById("salary").value`)).toBe("55000");

    // tap "Bewerbung abschicken": the guard's confirm() reaches the viewer; nothing is sent yet
    const btn = await page.evaluate(`(() => { const b = document.querySelector("button[type=submit]"); const r = b.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
    const r2 = await (await input(page.id, { type: "click", ...tapFor(btn.x, btn.y, meta) })).json();
    expect(r2).toMatchObject({ ok: true, control: "Bewerbung abschicken", submitControl: true });
    const dlg = await until(() => s.events.find((e) => e.type === "dialog"));
    expect(dlg.dialog).toMatchObject({ type: "confirm", guard: true });
    expect(dlg.dialog.message).toMatch(/^Job-apply guard/);
    await sleep(300);
    expect(posted).toHaveLength(0);
    expect((await input(page.id, { type: "click", x: 5, y: 5 })).status).toBe(409); // answer the dialog first

    // the candidate's Accept in the live view is the human OK
    const acc = await fetch(live(`${page.id}/dialog`), { method: "POST", headers: H, body: JSON.stringify({ accept: true }) });
    expect(await acc.json()).toMatchObject({ ok: true, guard: true, accepted: true });
    await until(() => posted.length === 1);
    expect(posted[0]).toContain("consent=on");
    expect(posted[0]).toContain("salary=55000");
    const st = await until(() => s.events.find((e) => e.type === "status"), 15_000);
    expect(st).toMatchObject({ jd: "Taktile", state: "submitted" });
    expect(st.confirmationText).toMatch(/Vielen Dank/);
    const file = await until(() => {
      const j = JSON.parse(readFileSync(statusFile, "utf-8"));
      return j.state === "submitted" && existsSync(path.join(dataDir, "apply", "Taktile", "sheet.json")) && j;
    });
    expect(file).toMatchObject({ submitted: true, submittedVia: "live", guardAccepted: 1 });
    expect(existsSync(path.join(dataDir, "apply", "Taktile", "confirmation.png"))).toBe(true);
    // fake push_to_sheet.py asserts argv[1] == --set-status
    expect(sheetRows()[0][0]).toMatchObject({ company: "Taktile", url: `${fx}/job`, status: "履歷送出" });
    expect(JSON.parse(readFileSync(path.join(dataDir, "apply", "Taktile", "sheet.json"), "utf-8"))).toMatchObject({ ok: true, added: 1 });
    // network for the submit click (30 s in production, 3 s here)
    const netFile = path.join(dataDir, "apply", "Taktile", "submit-network.json");
    await until(() => existsSync(netFile), 8000);
    const net = JSON.parse(readFileSync(netFile, "utf-8"));
    expect(net.via).toBe("live");
    expect(net.requests).toEqual(expect.arrayContaining([expect.objectContaining({ method: "POST", url: `${fx}/apply`, status: 200 })]));
    expect(JSON.stringify(net)).not.toContain("55000"); // request bodies are never recorded

    // > 30 inputs/s → 429
    const codes = await Promise.all(Array.from({ length: 40 }, () => input(page.id, { type: "scroll", deltaY: 10 }).then((r) => r.status)));
    expect(codes).toContain(429);

    // last viewer gone → screencast stopped
    s.close();
    const tab = async () => ((await (await fetch(live("tabs"))).json()) as { id: string; viewers: number; screencasting: boolean }[]).find((x) => x.id === page.id);
    let t2 = await tab();
    for (let i = 0; i < 40 && t2?.screencasting; i++) {
      await sleep(100);
      t2 = await tab();
    }
    expect(t2).toMatchObject({ viewers: 0, screencasting: false });
  }, 90_000);

  it("an animating page streams at the capped frame rate", async () => {
    const anim = await cdpPage(`${fx}/anim`);
    const s = openStream(anim.id);
    await until(() => frames(s.events)[0]);
    const start = Date.now();
    const n0 = frames(s.events).length;
    await sleep(3000);
    const fps = (frames(s.events).length - n0) / ((Date.now() - start) / 1000);
    console.log(`[live] animating page: ${fps.toFixed(1)} fps (cap 15)`);
    s.close();
    anim.close();
    expect(fps).toBeGreaterThan(3);
    expect(fps).toBeLessThan(17);
  }, 30_000);
});
