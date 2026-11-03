// Pipeline page API against the fake Google Sheets REST server (tools/pipeline/fake_sheets): the dashboard
// spawns the real push_to_sheet.py, which talks to the fake through the Sheets API.
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import type { AddressInfo } from "node:net";
import { createInterface } from "node:readline";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

const dataDir = mkdtempSync(path.join(tmpdir(), "cvd-pipe-"));
process.env.CV_DASHBOARD_DATA = dataDir;
process.env.CLAUDE_BIN = path.resolve(__dirname, "fixtures/fake-claude.mjs");
const configFile = path.join(dataDir, "sheet.json");
process.env.AI_JOB_TRACKER_SHEET_CONFIG = configFile;

const FAKE_SERVER = path.resolve(__dirname, "../../pipeline/fake_sheets/server.py");
const SLOW = 180_000; // the fake runs every action through the Python backend (~1–3 s each)
const SHEET_URL = "https://docs.google.com/spreadsheets/d/FAKE/edit";
const JSON_HEADERS = { "content-type": "application/json" };

let base = "";
let fake = "";
let fakeProc: ChildProcess;
let close: () => void = () => undefined;

function writeConfig(apiBase: string): void {
  writeFileSync(configFile, JSON.stringify({ api_base: apiBase, access_token: "fake-token", sheet_url: SHEET_URL }));
}

async function sheet(action: string, extra: Record<string, unknown> = {}) {
  const r = await fetch(`${fake}/`, { method: "POST", body: JSON.stringify({ action, ...extra }) });
  const body = await r.json();
  expect(body.ok, JSON.stringify(body)).toBe(true);
  return body;
}

/** Rows of a tab as objects keyed by its header row (display values). */
async function table(name: string): Promise<Record<string, string>[]> {
  const d = (await (await fetch(`${fake}/__dump`)).json())[name].display as string[][];
  return d.slice(1).filter((r) => r.some(Boolean)).map((r) => Object.fromEntries(d[0].map((h, i) => [h, r[i]])));
}

const post = (p: string, body: unknown, headers: Record<string, string> = JSON_HEADERS) =>
  fetch(`${base}${p}`, { method: "POST", headers, body: JSON.stringify(body) });

const DAILY = [
  { company: "Older Co", title: "ML Engineer", url: "https://jobs.example.com/old-1", type: "ML learning", city: "Amsterdam",
    country: "荷蘭", mode: "remote", score: 9, reasons: ["舊的"], date: "2026-10-30" },
  { company: "Agent Co", title: "AI Agent Engineer", url: "https://jobs.example.com/agent-1", type: "AI agent", city: "Munich",
    country: "德國", mode: "hybrid", score: 6, reasons: ["LangGraph agent 經驗", "Python 後端"], date: "2026-11-01" },
  { company: "Top Co", title: "AI Engineer", url: "https://jobs.example.com/top-1", type: "automation", city: "Berlin",
    country: "德國", mode: "on-site", score: 8, reasons: ["RAG"], date: "2026-11-01" },
];

beforeAll(async () => {
  fakeProc = spawn("python3", [FAKE_SERVER, "--port", "0"], { stdio: ["ignore", "pipe", "inherit"] });
  const line: string = await new Promise((resolve, reject) => {
    const rl = createInterface({ input: fakeProc.stdout! });
    rl.once("line", resolve);
    fakeProc.once("error", reject);
  });
  expect(line).toMatch(/^LISTENING \d+$/);
  fake = `http://127.0.0.1:${line.split(" ")[1]}`;
  writeConfig(fake);
  const { createApp } = await import("../server/http");
  const { server } = createApp();
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  close = () => server.close();
}, SLOW);
afterAll(() => {
  close();
  fakeProc?.kill();
  rmSync(dataDir, { recursive: true, force: true });
});
beforeEach(async () => {
  writeConfig(fake);
  await fetch(`${fake}/__reset`, { method: "POST" });
  await sheet("setup_pipeline");
  await sheet("pipeline_append", { rows: DAILY });
}, SLOW);

describe("Pipeline API", { timeout: SLOW }, () => {
  it("GET /api/pipeline/daily lists 每日職缺 with sheet_url and no token", async () => {
    const res = await fetch(`${base}/api/pipeline/daily`);
    expect(res.status).toBe(200);
    const text = await res.text();
    expect(text).not.toContain("fake-token");
    const d = JSON.parse(text);
    expect(d.sheetUrl).toBe(SHEET_URL);
    expect(d.rows.map((r: any) => r.company).sort()).toEqual(["Agent Co", "Older Co", "Top Co"]);
    const agent = d.rows.find((r: any) => r.company === "Agent Co");
    expect(agent).toMatchObject({ date: "2026-11-01", type: "AI agent", mode: "Hybrid", score: 6, decision: "未確認", state: "待決定" });
    expect(agent.reasons).toBe("• LangGraph agent 經驗\n• Python 後端");
    expect(typeof agent.row).toBe("number");
  });

  it("POST decision and note change the 每日職缺 row", async () => {
    const url = "https://jobs.example.com/agent-1";
    const r = await post("/api/pipeline/daily/decision", { url, decision: "是" });
    expect(r.status).toBe(200);
    expect((await r.json()).url).toBe(url);
    const n = await post("/api/pipeline/daily/note", { url, note: "強調 RAG 專案" });
    expect(n.status).toBe(200);
    let row = (await table("每日職缺")).find((x) => x["連結"] === url)!;
    expect([row["決定"], row["備註（人工）"]]).toEqual(["是", "強調 RAG 專案"]);
    // the list shows it, and clearing the note works
    const d = await (await fetch(`${base}/api/pipeline/daily`)).json();
    expect(d.rows.find((x: any) => x.url === url)).toMatchObject({ decision: "是", note: "強調 RAG 專案" });
    expect((await post("/api/pipeline/daily/note", { url, note: "" })).status).toBe(200);
    row = (await table("每日職缺")).find((x) => x["連結"] === url)!;
    expect(row["備註（人工）"]).toBe("");
    // validation and unknown rows
    expect((await post("/api/pipeline/daily/decision", { url, decision: "maybe" })).status).toBe(400);
    expect((await post("/api/pipeline/daily/decision", { url: "javascript:alert(1)", decision: "是" })).status).toBe(400);
    const missing = await post("/api/pipeline/daily/decision", { url: "https://nope.example/x", decision: "否" });
    expect(missing.status).toBe(404);
    expect((await missing.json()).error).toBe("每日職缺找不到這個連結");
  });

  it("POST /api/pipeline/interest appends, reports duplicates and refuses bad URLs", async () => {
    const url = "https://jobs.example.com/interest-1";
    const r = await post("/api/pipeline/interest", { url, note: "想試試" });
    expect(r.status).toBe(200);
    const added = await r.json();
    expect(added.added).toEqual([{ url, row: 2 }]);
    expect(added.skipped).toEqual([]);
    const rows = await table("興趣職缺");
    expect(rows.map((x) => [x["連結"], x["備註（人工）"]])).toEqual([[url, "想試試"]]);
    expect(rows[0]["日期"]).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const dup = await (await post("/api/pipeline/interest", { url })).json();
    expect(dup.added).toEqual([]);
    expect(dup.skipped[0]).toMatchObject({ reason: "duplicate", tab: "興趣職缺" });
    for (const bad of ["not a url", "ftp://x.example/1", "", 42]) {
      const res = await post("/api/pipeline/interest", { url: bad });
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("需要有效的 http(s) 連結");
    }
    expect(await table("興趣職缺")).toHaveLength(1);
    const list = await (await fetch(`${base}/api/pipeline/interest`)).json();
    expect(list.rows).toHaveLength(1);
    expect(list.rows[0]).toMatchObject({ url, note: "想試試", state: "" });
  });

  it("GET /api/pipeline/submissions resolves app_dir to the application key", async () => {
    await sheet("submissions_append", {
      rows: [
        { company: "TOMRA", title: "Senior Python Software Engineer", url: "https://jobs.example.com/tomra", source: "AI 找的",
          ats: 80.3, cv: "https://www.notion.so/cv1", cv_label: "CV PDF", cover_letter: "cv/cover_letters/x/CoverLetter.pdf",
          app_dir: "cv/applications/20261023_TOMRA_Senior-Python-Software-Engineer", notion: "https://www.notion.so/row1" },
        { company: "Sub Co", title: "AI Engineer", url: "https://jobs.example.com/sub-1", source: "興趣",
          app_dir: "cv/applications/20261101_SubCo_AI-Engineer/v3_cards" },
      ],
    });
    const d = await (await fetch(`${base}/api/pipeline/submissions`)).json();
    expect(d.sheetUrl).toBe(SHEET_URL);
    const [tomra, sub] = d.rows;
    expect(tomra).toMatchObject({ jd: "TOMRA", source: "AI 找的", cv: "CV PDF", cv_url: "https://www.notion.so/cv1", status: "準備中",
      cover_letter: "cv/cover_letters/x/CoverLetter.pdf", notion_url: "https://www.notion.so/row1" });
    expect(tomra.cover_letter_url).toBeUndefined();
    expect(sub.jd).toBeNull();
  });

  it("投遞表單 referrals: listed, then 標記已送出 sets 內推狀態 and a 求職過程 line", async () => {
    const url = "https://jobs.example.com/ref-1";
    await sheet("submissions_append", {
      rows: [{ date: "2026-11-02", company: "Ref Co", title: "ML Engineer", url, type: "ML learning", source: "AI 找的", city: "Berlin",
        country: "德國", mode: "Remote", ats: 75.8, notion: "https://www.notion.so/rec1", cv: "https://drive.google.com/file/d/cv1/view",
        cover_letter: "https://drive.google.com/file/d/cl1/view", app_dir: "cv/applications/20261102_RefCo_ML-Engineer" }],
    });
    const people = [
      { name: "Ann Lee", title: "ML Engineer", url: "https://www.linkedin.com/in/annlee", why: "同團隊", group: "team",
        full: "Hi Ann, I am applying for the ML Engineer role…", note: "Hi Ann, could you refer me? 謝謝" },
      { name: "Bo Chen", title: "Engineering Manager", url: "https://www.linkedin.com/in/bochen", why: "主管", group: "manager" },
    ];
    await sheet("submissions_update", { rows: [{ url, referrers: people, referral_data: people, referral_message: people[0].full }] });
    const d = await (await fetch(`${base}/api/pipeline/submissions`)).json();
    const row = d.rows.find((x: any) => x.url === url);
    expect(row).toMatchObject({ cv_url: "https://drive.google.com/file/d/cv1/view", cover_letter_url: "https://drive.google.com/file/d/cl1/view",
      resume_version_url: "https://www.notion.so/rec1", referral_status: "", jd: null });
    expect(row.referrals.map((p: any) => [p.name, p.group, p.note ?? null])).toEqual([["Ann Lee", "team", people[0].note], ["Bo Chen", "manager", null]]);
    expect(row.process).toMatch(/^- 11\/02 CV \+ cover letter 產生 \(ATS 75.8\)/);

    const r = await post("/api/pipeline/referral-sent", { url, name: "Ann Lee" });
    expect(r.status).toBe(200);
    const t = (await table("投遞表單")).find((x) => x["公司"] === "Ref Co")!;
    expect(t["內推狀態"]).toBe("⚠️等待回覆");
    expect(t["求職過程"].split("\n").at(-1)).toMatch(/^- \d{2}\/\d{2} Sent referral request to Ann Lee$/);
    const after = (await (await fetch(`${base}/api/pipeline/submissions`)).json()).rows.find((x: any) => x.url === url);
    expect(after.referral_status).toBe("⚠️等待回覆");
    // validation and unknown rows
    expect((await post("/api/pipeline/referral-sent", { url, name: " " })).status).toBe(400);
    expect((await post("/api/pipeline/referral-sent", { url: "nope", name: "Ann Lee" })).status).toBe(400);
    const missing = await post("/api/pipeline/referral-sent", { url: "https://jobs.example.com/none", name: "Ann Lee" });
    expect(missing.status).toBe(404);
    expect((await missing.json()).error).toBe("投遞表單找不到這個連結");
  });

  it("POSTs pass the Origin / JSON guard", async () => {
    const body = { url: "https://jobs.example.com/agent-1", decision: "否" };
    for (const p of ["/api/pipeline/daily/decision", "/api/pipeline/daily/note", "/api/pipeline/interest", "/api/pipeline/referral-sent"]) {
      expect((await fetch(`${base}${p}`, { method: "POST", body: JSON.stringify(body) })).status).toBe(415);
      expect((await post(p, body, { ...JSON_HEADERS, origin: "https://evil.example" })).status).toBe(403);
      expect((await post(p, body, { ...JSON_HEADERS, "sec-fetch-site": "cross-site" })).status).toBe(403);
    }
    expect((await table("每日職缺")).find((x) => x["連結"] === body.url)!["決定"]).toBe("未確認");
    expect(await table("興趣職缺")).toHaveLength(0);
    const ok = await post("/api/pipeline/daily/decision", body, { ...JSON_HEADERS, origin: "https://desktop-1.tail546de3.ts.net" });
    expect(ok.status).toBe(200);
  });

  it("without AI_JOB_TRACKER_SHEET_CONFIG or PIPELINE_SHEET_LIVE=1 nothing reaches a Sheet", async () => {
    const saved = process.env.AI_JOB_TRACKER_SHEET_CONFIG;
    delete process.env.AI_JOB_TRACKER_SHEET_CONFIG;
    delete process.env.PIPELINE_SHEET_LIVE;
    const before = ((await (await fetch(`${fake}/__log`)).json()) as unknown[]).length;
    try {
      for (const tab of ["daily", "interest", "submissions"]) {
        const res = await fetch(`${base}/api/pipeline/${tab}`);
        expect(res.status).toBe(503);
        expect((await res.json()).error).toBe(
          "Pipeline 未連正式 Google Sheet:用 npm run dev:live(或 start:live)啟動;測試請設 AI_JOB_TRACKER_SHEET_CONFIG",
        );
      }
      expect((await post("/api/pipeline/interest", { url: "https://jobs.example.com/x" })).status).toBe(503);
      expect(((await (await fetch(`${fake}/__log`)).json()) as unknown[]).length).toBe(before);
    } finally {
      process.env.AI_JOB_TRACKER_SHEET_CONFIG = saved;
    }
  });

  it("missing config gives a clear error", async () => {
    rmSync(configFile);
    const res = await fetch(`${base}/api/pipeline/daily`);
    expect(res.status).toBe(503);
    expect((await res.json()).error).toContain("找不到 Google Sheet 設定檔");
  });

  it("a backend failure maps to 502 with the Sheet error", async () => {
    writeConfig("http://127.0.0.1:9/"); // nothing listens there
    const res = await fetch(`${base}/api/pipeline/daily`);
    expect(res.status).toBe(502);
    expect((await res.json()).error).toMatch(/^Google Sheet 錯誤:/);
  });
});
