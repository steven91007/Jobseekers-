// Auto apply: the fill run (fake claude) and the separate Confirm & submit step. The submit path is
// exercised only against a local fixture form served by this test, in a real Chromium on a temp
// persistent profile and its own CDP port (never the real apply browser, never a real site).
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import type { ApplyStatusFile, ClaudeRun } from "../shared/types";

const dataDir = mkdtempSync(path.join(tmpdir(), "cvd-apply-"));
const sheetOut = path.join(dataDir, "sheet_rows.jsonl");
const CDP_PORT = 9300 + Math.floor(Math.random() * 500);
process.env.CV_DASHBOARD_DATA = dataDir;
process.env.CLAUDE_BIN = path.resolve(__dirname, "fixtures/fake-claude.mjs");
process.env.CV_SHEET_PUSH = path.resolve(__dirname, "fixtures/fake-sheet-push.py");
process.env.FAKE_SHEET_OUT = sheetOut;
process.env.APPLY_BROWSER_CMD = "echo fake browser up";
process.env.JOB_APPLY_PLUGIN_ROOT = "/opt/fake/job-apply";
process.env.APPLY_CDP_PORT = String(CDP_PORT);
process.env.APPLY_HEADLESS = "1"; // no browser window on the desktop during tests
process.env.APPLY_SUBMIT_WAIT_MS = "6000";
// cover letters live in a temp copy: Ixopay has one, every other application has none
const coverDir = path.join(dataDir, "cover_letters");
process.env.COVER_LETTERS_DIR = coverDir;
mkdirSync(path.join(coverDir, "20261025_Ixopay_Sr-AI-Engineer"), { recursive: true });
writeFileSync(path.join(coverDir, "20261025_Ixopay_Sr-AI-Engineer", "CoverLetter_AmoHsu_Ixopay.pdf"), "%PDF-1.4 ixopay letter");
writeFileSync(path.join(coverDir, "20261025_Ixopay_Sr-AI-Engineer", "letter.txt"), "Dear Ixopay team,\nI am applying for Sr. AI Engineer.");
writeFileSync(path.join(coverDir, "20261025_Ixopay_Sr-AI-Engineer", "review.md"), "VERDICT READY");

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

let base = "";
let close: () => void = () => undefined;

beforeAll(async () => {
  const { createApp } = await import("../server/http");
  const { server } = createApp();
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  close = () => server.close();
});
afterAll(() => {
  close();
  rmSync(dataDir, { recursive: true, force: true });
});
beforeEach(() => {
  delete process.env.FAKE_APPLY_STATE;
  delete process.env.FAKE_APPLY_URL;
  rmSync(sheetOut, { force: true });
});

const H = { "content-type": "application/json", origin: "http://127.0.0.1:5173" };
const post = (p: string, body: unknown, headers: Record<string, string> = {}) =>
  fetch(`${base}/api/applications/${p}`, { method: "POST", headers: { ...H, ...headers }, body: JSON.stringify(body) });
const info = async (jd: string) => (await fetch(`${base}/api/applications/${jd}/apply`)).json();

async function finished(id: string): Promise<ClaudeRun> {
  await (await fetch(`${base}/api/claude/${id}/stream`)).text();
  return (await (await fetch(`${base}/api/claude/${id}`)).json()) as ClaudeRun;
}
async function fill(jd: string, body: unknown = {}): Promise<ClaudeRun> {
  const res = await post(`${jd}/apply`, body);
  expect(res.status).toBe(202);
  return finished((await res.json()).runId);
}
const sheetRows = () => (existsSync(sheetOut) ? readFileSync(sheetOut, "utf-8").trim().split("\n").map((l) => JSON.parse(l)) : []);

describe("Auto apply: fill run", () => {
  it("GET reports the URL from the JD files, the CV and plugin root", async () => {
    expect(await info("Ixopay")).toMatchObject({
      jd: "Ixopay",
      url: "https://www.wearedevelopers.com/jobs/ext/3831405-sr-ai-engineer",
      pluginRoot: "/opt/fake/job-apply",
      status: null,
      canSubmit: false,
      screenshots: [],
      runId: null,
    });
  });

  it("fills, never submits: prompt, restricted tools, outputs, no sheet row yet", async () => {
    const res = await post("Scout24/apply", { url: "https://www.scout24.com/karriere/jobs/engineering/x-1" });
    expect(res.status).toBe(202);
    const { runId } = await res.json();
    expect((await post("Scout24/apply", {})).status).toBe(409); // one fill run per application
    const run = await finished(runId);
    expect(run).toMatchObject({ status: "succeeded", mode: "apply" });
    expect(run.prompt.startsWith("/job-apply:job-apply https://www.scout24.com/karriere/jobs/engineering/x-1")).toBe(true);
    for (const s of ["NEVER click Submit", "fields.json", "step-01.png", "blocked_captcha", "submitControl"]) expect(run.prompt).toContain(s);
    // standing answers, salary rule and truth rules for generated text
    for (const s of ['"LinkedIn"', '"No"', '"Male"', "middle option", "enter 0", "at most 120 words", "MaterialCheck (card MC) is a proof of concept", "https://github.com/steven91007", "library_seed.json", "candidate_confirmations.md", "03_cv.md"]) expect(run.prompt).toContain(s);
    const outDir = path.join(dataDir, "apply", "Scout24");
    expect(run.prompt).toContain(path.join(outDir, "CV_ChiYiHsu_Scout24.pdf"));

    const argv = run.argv.join(" ");
    expect(argv).toContain(`--mcp-config ${path.join(outDir, "mcp.json")}`);
    expect(argv).toContain("--strict-mcp-config");
    expect(argv).toContain("Bash(python3 /opt/fake/job-apply/scripts/job-apply-store.py *)");
    for (const t of ["browser_run_code_unsafe", "browser_evaluate", "browser_handle_dialog"]) expect(argv).toMatch(new RegExp(`--disallowedTools=[^ ]*mcp__playwright__${t}`));
    expect(argv).not.toContain("Bash(python3:*)");
    expect(argv).toMatch(/Bash\(python3 [^,]*library_db\.py card \*\)/);
    const mcp = JSON.parse(readFileSync(path.join(outDir, "mcp.json"), "utf-8"));
    expect(mcp.mcpServers.playwright.args).toEqual(expect.arrayContaining(["--cdp-endpoint", `http://127.0.0.1:${CDP_PORT}`, "--init-script"]));

    const i = await info("Scout24");
    expect(i.status).toMatchObject({ state: "filled_awaiting_confirm", submitted: false, cdpEndpoint: `http://127.0.0.1:${CDP_PORT}`, runId });
    expect(i.canSubmit).toBe(true);
    expect(i.fields.map((f: { source: string }) => f.source)).toEqual(["profile", "inferred", "blank", "generated"]);
    expect(i.screenshots.map((s: { name: string }) => s.name)).toEqual(["step-01.png", "review.png"]);
    expect(i.url).toBe("https://www.scout24.com/karriere/jobs/engineering/x-1");
    expect(i.sheet).toBeNull();
    expect(sheetRows()).toHaveLength(0);
    const shot = await fetch(`${base}/api/applications/Scout24/apply/screenshot/step-01.png`);
    expect([shot.status, shot.headers.get("content-type")]).toEqual([200, "image/png"]);
    expect((await fetch(`${base}/api/applications/Scout24/apply/screenshot/..%2Fstatus.json`)).status).toBe(404);
  });

  it("cover letter: an existing one is copied in for upload/paste; a missing one is generated first", async () => {
    let run = await fill("Ixopay", { url: "https://example.test/ixopay" });
    const ixDir = path.join(dataDir, "apply", "Ixopay");
    expect(readFileSync(path.join(ixDir, "CoverLetter_AmoHsu_Ixopay.pdf"), "utf-8")).toContain("ixopay letter");
    expect(readFileSync(path.join(ixDir, "letter.txt"), "utf-8")).toContain("Dear Ixopay team");
    expect(run.prompt).toContain(`upload ${path.join(ixDir, "CoverLetter_AmoHsu_Ixopay.pdf")} (browser_click`);
    expect(run.prompt).toContain(`paste the full text of ${path.join(ixDir, "letter.txt")}`);
    expect(run.prompt).not.toContain("GENERATE FIRST");
    let i = await info("Ixopay");
    expect(i.coverLetter).toMatchObject({ source: "existing", pdf: "CoverLetter_AmoHsu_Ixopay.pdf", scored: true, available: true, uploaded: true });
    const pdf = await fetch(`${base}/api/applications/Ixopay/apply/cover-letter`);
    expect([pdf.status, pdf.headers.get("content-type")]).toEqual([200, "application/pdf"]);

    expect((await fetch(`${base}/api/applications/AIVenture/apply/cover-letter`)).status).toBe(404);
    run = await fill("AIVenture", { url: "https://example.test/aiventure" });
    expect(run.prompt).toContain("COVER LETTER — GENERATE FIRST");
    expect(run.prompt).toMatch(new RegExp(`\\$D = ${coverDir.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/\\d{8}_AIVenture_Agentic-Platform-Engineer \\(write ONLY`));
    expect(run.prompt).toContain("codex:codex-rescue");
    expect(run.argv.join(" ")).toContain(`Write(${coverDir}/**)`);
    expect(run.argv.join(" ")).toMatch(/build_letter\.sh \*\)/);
    expect(run.events.some((e) => e.type === "tool" && e.name === "Skill")).toBe(true);
    i = await info("AIVenture");
    expect(i.coverLetter).toMatchObject({ source: "generated", scored: false, available: true, pdf: "CoverLetter_AmoHsu_AIVenture.pdf" });
    expect((await fetch(`${base}/api/applications/AIVenture/apply/cover-letter`)).status).toBe(200);
    // the next fill finds it as an existing letter
    run = await fill("AIVenture");
    expect(run.prompt).not.toContain("GENERATE FIRST");
  });

  it("a fill run cannot mark itself submitted; blockers carry desk instructions", async () => {
    process.env.FAKE_APPLY_STATE = "submitted";
    await fill("Mapbox", { url: "https://example.test/mapbox" });
    let i = await info("Mapbox");
    expect(i.status).toMatchObject({ state: "failed", submitted: false });
    expect(i.status.blocker).toContain('"submitted"');
    expect(i.canSubmit).toBe(false);

    process.env.FAKE_APPLY_STATE = "blocked_login";
    await fill("Mapbox");
    i = await info("Mapbox");
    expect(i.status).toMatchObject({ state: "blocked_login", blocker: "sign-in page before the form" });
    expect(i.help).toMatch(/sign in inside the apply browser/);
    expect(i.screenshots.map((s: { name: string }) => s.name)).toEqual(["step-01.png"]);

    process.env.FAKE_APPLY_STATE = "none";
    await fill("Mapbox");
    i = await info("Mapbox");
    expect(i.status).toMatchObject({ state: "failed", submitted: false, url: "https://example.test/mapbox" });
    expect(i.screenshots).toEqual([]); // previous attempt's outputs were cleared
  });

  it("validates URL, application, origin; submit needs the explicit confirmation", async () => {
    expect((await post("Taktile/apply", { url: "javascript:alert(1)" })).status).toBe(400);
    expect((await post("Taktile/apply", { url: "file:///etc/passwd" })).status).toBe(400);
    expect((await post("Nope/apply", {})).status).toBe(404);
    expect((await post("Taktile/apply", {}, { origin: "https://evil.example" })).status).toBe(403);
    expect((await post("Taktile/apply/submit", { confirm: "SUBMIT" }, { origin: "https://evil.example" })).status).toBe(403);
    expect((await post("Taktile/apply/submit", {})).status).toBe(400);
    expect((await post("Taktile/apply/submit", { confirm: "SUBMIT", url: "x" })).status).toBe(409); // nothing filled
    // filled but the URL the phone shows differs from the tab → refused
    expect((await post("Scout24/apply/submit", { confirm: "SUBMIT", url: "https://other.example/" })).status).toBe(409);
  });
});

// ------------------------------------------------------------------ submit against a local fixture

const posted: string[] = [];
const bridged: string[] = [];
const fixture = http.createServer((req, res) => {
  if (req.method === "POST" && req.url === "/bridge") {
    bridged.push("1");
    res.writeHead(204);
    return void res.end();
  }
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
  if (req.url === "/cover") {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    return void res.end(`<!doctype html><form><label class="up" for="cl">Cover Letter (pdf) <span>Datei auswählen</span>
      <input type="file" id="cl" name="cl" style="display:none"></label>
      <label for="mot">Motivation</label><textarea id="mot" name="mot"></textarea>
      <button type="submit">Bewerbung abschicken</button></form>`);
  }
  if (req.url === "/login") {
    res.writeHead(200, { "set-cookie": "session=kept; Max-Age=86400; Path=/", "content-type": "text/html" });
    return void res.end("<p>logged in</p>");
  }
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  // like Scout24: a site script submits a hidden (non-application) form on load
  res.end(`<!doctype html><form method="post" action="/apply"><input name="first" id="first">
    <label for="why">Describe one AI application *</label><textarea id="why" name="why"></textarea>
    <button type="button" id="next">Weiter</button><button type="submit">Bewerbung abschicken</button></form>
    <form id="bridge" method="post" action="/bridge" target="sink"><input name="x" value="1"></form><iframe name="sink"></iframe>
    <script>document.getElementById("bridge").submit()</script>`);
});

/** Tiny CDP client for the test's own setup (open/fill the tab, inject the guard). */
async function cdpPage(port: number, url: string, initScript?: string) {
  const t = (await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json()) as { webSocketDebuggerUrl: string };
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r, j) => ((ws.onopen = r), (ws.onerror = j)));
  let id = 0;
  const pend = new Map<number, (v: any) => void>();
  const events: any[] = [];
  ws.onmessage = (m) => {
    const msg = JSON.parse(String(m.data));
    if (msg.id) pend.get(msg.id)?.(msg.result);
    else events.push(msg);
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
  await new Promise((r) => setTimeout(r, 800));
  return { send, events, close: () => ws.close() };
}

describe.skipIf(!chromium)("Confirm & submit (local fixture form, real Chromium)", () => {
  const profile = path.join(dataDir, "profile");
  let fx = "";
  const env = { ...process.env, APPLY_CDP_PORT: String(CDP_PORT), APPLY_BROWSER_DIR: profile };
  const startBrowser = () => execFileSync("bash", [BROWSER_SH], { env, encoding: "utf-8" });
  const stopBrowser = async () => {
    try {
      const v = (await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json()) as { webSocketDebuggerUrl: string };
      const ws = new WebSocket(v.webSocketDebuggerUrl);
      await new Promise((r) => (ws.onopen = r));
      ws.send(JSON.stringify({ id: 1, method: "Browser.close" }));
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 250));
        if (spawnSync("curl", ["-sf", `http://127.0.0.1:${CDP_PORT}/json/version`]).status !== 0) return;
      }
    } catch {
      /* already down */
    }
  };
  beforeAll(async () => {
    await new Promise<void>((r) => fixture.listen(0, "127.0.0.1", r));
    fx = `http://127.0.0.1:${(fixture.address() as AddressInfo).port}`;
    startBrowser();
  });
  afterAll(async () => {
    await stopBrowser();
    fixture.close();
  });

  it("guard blocks a final click without a human OK; Confirm & submit reattaches and submits", async () => {
    // a "login" whose cookie must survive a browser restart (persistent profile)
    const login = await cdpPage(CDP_PORT, `${fx}/login`);
    await new Promise((r) => setTimeout(r, 500));
    login.close();

    // the state a fill run leaves: tab open, field filled, guard injected
    const page = await cdpPage(CDP_PORT, `${fx}/job`, GUARD_JS); // guard as Playwright --init-script would add it
    expect(await page.send("Runtime.evaluate", { expression: "window.__applyGuard === true", returnByValue: true })).toMatchObject({ result: { value: true } });
    // the site's own load-time submit of a non-application form passed without a dialog
    expect(bridged).toHaveLength(1);
    expect(page.events.some((e) => e.method === "Page.javascriptDialogOpening")).toBe(false);
    await page.send("Runtime.evaluate", {
      expression: `document.getElementById("first").value = "Chi-Yi"; document.getElementById("why").value = "Generated draft about deliverytrack."`,
    });

    // the guard: a trusted click on the final button opens confirm(); dismissing it sends nothing
    const r = (await page.send("Runtime.evaluate", {
      expression: `(() => { const b = [...document.querySelectorAll("button")].find(x => x.innerText === "Bewerbung abschicken"); const q = b.getBoundingClientRect(); return { x: q.x + q.width / 2, y: q.y + q.height / 2 }; })()`,
      returnByValue: true,
    })).result.value;
    for (const type of ["mousePressed", "mouseReleased"]) page.send("Input.dispatchMouseEvent", { type, x: r.x, y: r.y, button: "left", clickCount: 1 });
    for (let i = 0; i < 20 && !page.events.some((e) => e.method === "Page.javascriptDialogOpening"); i++) await new Promise((res) => setTimeout(res, 100));
    const dlg = page.events.find((e) => e.method === "Page.javascriptDialogOpening");
    expect(dlg?.params.message).toMatch(/^Job-apply guard/);
    await page.send("Page.handleJavaScriptDialog", { accept: false });
    await new Promise((res) => setTimeout(res, 500));
    expect(posted).toHaveLength(0);
    page.close();

    // fill run result for that tab
    process.env.FAKE_APPLY_URL = `${fx}/job`;
    await fill("Taktile", { url: `${fx}/job` });
    const before = await info("Taktile");
    expect(before.status).toMatchObject({ state: "filled_awaiting_confirm", url: `${fx}/job` });
    expect(before.status.targetId).toEqual(expect.any(String)); // the open tab was found over CDP

    // an edit for a label that is not in fields.json is refused before anything happens
    expect((await post("Taktile/apply/submit", { confirm: "SUBMIT", url: `${fx}/job`, edits: [{ label: "Nope", value: "x" }] })).status).toBe(400);
    const edited = "Edited by the candidate: LangGraph procurement mail agent, 100+ users.";
    const res = await post("Taktile/apply/submit", {
      confirm: "SUBMIT",
      url: `${fx}/job`,
      edits: [{ label: "Describe one AI application *", value: edited }],
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ state: "submitted", submitted: true });
    expect(posted).toHaveLength(1);
    expect(posted[0]).toContain("first=Chi-Yi"); // the filled value from the SAME page was sent
    expect(decodeURIComponent(posted[0].replace(/\+/g, " "))).toContain(`why=${edited}`); // the edit was written in before Submit

    const after = await info("Taktile");
    expect(after.status).toMatchObject({ state: "submitted", submitted: true, guardAccepted: 1, editsApplied: 1 });
    expect(after.fields.find((f: { label: string }) => f.label === "Describe one AI application *")).toMatchObject({ value: edited, edited: true, source: "generated" });
    expect(after.status.confirmationText).toMatch(/Vielen Dank/);
    expect(after.screenshots.map((s: { name: string }) => s.name)).toEqual(expect.arrayContaining(["pre-submit.png", "confirmation.png"]));
    expect(after.canSubmit).toBe(false);
    expect(after.sheet).toMatchObject({ ok: true, added: 1 });
    expect(sheetRows()[0][0]).toMatchObject({ company: "Taktile", url: `${fx}/job`, status: "履歷送出" });

    // a second submit is refused
    expect((await post("Taktile/apply/submit", { confirm: "SUBMIT", url: `${fx}/job` })).status).toBe(409);
  }, 60_000);

  it("Playwright MCP (as the fill run uses it): cover-letter PDF uploaded via click → file_upload, letter text pasted", async () => {
    const ws = path.join(dataDir, "mcp-ws");
    mkdirSync(ws, { recursive: true });
    const pdf = path.join(ws, "CoverLetter_AmoHsu_Ixopay.pdf");
    writeFileSync(pdf, "%PDF-1.4 ixopay letter");
    const letter = "Dear Ixopay team, I am applying for Sr. AI Engineer.";
    const { spawn } = await import("node:child_process");
    const mcp = spawn("npx", ["-y", "@playwright/mcp@0.0.83", "--cdp-endpoint", `http://127.0.0.1:${CDP_PORT}`, "--output-dir", ws, "--init-script", path.resolve(__dirname, "../scripts/apply-guard.js")], {
      cwd: ws,
      stdio: ["pipe", "pipe", "ignore"],
    });
    let buf = "";
    let id = 0;
    const waiters = new Map<number, (m: any) => void>();
    mcp.stdout.on("data", (d) => {
      buf += d;
      let i: number;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i);
        buf = buf.slice(i + 1);
        try {
          const m = JSON.parse(line);
          waiters.get(m.id)?.(m);
        } catch {
          /* log line */
        }
      }
    });
    const rpc = (method: string, params: unknown) =>
      new Promise<any>((r) => {
        const n = ++id;
        waiters.set(n, r);
        mcp.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: n, method, params }) + "\n");
      });
    const call = async (name: string, args: unknown) => {
      const r = await rpc("tools/call", { name, arguments: args });
      return (r.result?.content ?? []).map((c: { text?: string }) => c.text ?? "").join("\n") as string;
    };
    try {
      await rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "test", version: "1" } });
      mcp.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
      await call("browser_tabs", { action: "new" });
      await call("browser_navigate", { url: `${fx}/cover` });
      const clicked = await call("browser_click", { element: "Cover Letter upload label", target: "label.up" });
      expect(clicked).toContain("File chooser");
      const up = await call("browser_file_upload", { paths: [pdf] });
      expect(up).not.toContain("Error");
      await call("browser_type", { element: "Motivation", target: "#mot", text: letter });
      // read the result with our own CDP session (the fill run has no evaluate tool)
      const tabs = (await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json()) as { url: string; webSocketDebuggerUrl: string }[];
      const t = tabs.find((x) => x.url === `${fx}/cover`)!;
      const sock = new WebSocket(t.webSocketDebuggerUrl);
      await new Promise((r) => (sock.onopen = r));
      const res = await new Promise<any>((r) => {
        sock.onmessage = (m) => r(JSON.parse(String(m.data)));
        sock.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: { expression: `[document.getElementById("cl").files[0]?.name, document.getElementById("mot").value]`, returnByValue: true } }));
      });
      sock.close();
      expect(res.result.result.value).toEqual(["CoverLetter_AmoHsu_Ixopay.pdf", letter]);
    } finally {
      mcp.kill();
    }
  }, 90_000);

  it("browser restarted on the same profile: login kept, filled page gone → page_lost, nothing sent", async () => {
    await stopBrowser();
    startBrowser();
    const p = await cdpPage(CDP_PORT, "about:blank");
    const { cookies } = await p.send("Network.getCookies", { urls: [fx] });
    p.close();
    expect(cookies.map((c: { name: string; value: string }) => `${c.name}=${c.value}`)).toContain("session=kept");

    const file = path.join(dataDir, "apply", "Taktile", "status.json");
    const st = JSON.parse(readFileSync(file, "utf-8")) as ApplyStatusFile;
    writeFileSync(file, JSON.stringify({ ...st, state: "filled_awaiting_confirm", submitted: false, targetId: "gone", url: `${fx}/other` }));
    const n = posted.length;
    const res = await post("Taktile/apply/submit", { confirm: "SUBMIT", url: `${fx}/other` });
    expect(await res.json()).toMatchObject({ state: "submit_failed", submitted: false });
    const i = await info("Taktile");
    expect(i.status.pageLost).toBe(true);
    expect(i.help).toMatch(/filled form is gone/);
    expect(posted.length).toBe(n);
  }, 60_000);
});

it("fake sheet push mirrors push_to_sheet.py output", () => {
  const real = readFileSync(path.resolve(__dirname, "../../../.claude/skills/daily-job-tracker/scripts/push_to_sheet.py"), "utf-8");
  expect(real).toContain('"--set-status"');
  // runPush parses the "updated=… added=… skipped(duplicate)=…" prefix; later fields may be appended
  expect(real).toContain('print(f"updated={len(r[\'updated\'])} added={added} skipped(duplicate)={skipped} rejected={len(r[\'rejected\'])}');
});
