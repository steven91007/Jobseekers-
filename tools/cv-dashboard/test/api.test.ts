// HTTP-level checks against a real server on an ephemeral port; Claude is replaced by a fake CLI.
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const dataDir = mkdtempSync(path.join(tmpdir(), "cvd-test-"));
process.env.CV_DASHBOARD_DATA = dataDir;
process.env.CLAUDE_BIN = path.resolve(__dirname, "fixtures/fake-claude.mjs");

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

describe("API", () => {
  it("GET /api/applications returns the six rows", async () => {
    const rows = await (await fetch(`${base}/api/applications`)).json();
    expect(rows).toHaveLength(6);
    expect(rows[0]).toMatchObject({ jd: "TOMRA", atsOverall: 80.3, keywordsFound: 43, keywordsTotal: 60, layoutStatus: "PASS", hr: 82, hm: 79 });
  });
  it("GET /api/applications/:jd and 404s", async () => {
    const d = await (await fetch(`${base}/api/applications/TOMRA`)).json();
    expect(d.cv.lines.length).toBeGreaterThan(30);
    expect(d.pdfUrl).toBe("/api/applications/TOMRA/pdf");
    expect(d.notion.rootUrl).toBe("https://www.notion.so/40877152eb3d813b8468d0f508b78789");
    expect(d.cv.lines.find((l: any) => l.pick?.code === "VI1").library.variants.length).toBeGreaterThan(1);
    expect((await fetch(`${base}/api/applications/Nope`)).status).toBe(404);
  });
  it("GET /api/applications/:jd/pdf streams the PDF", async () => {
    const res = await fetch(`${base}/api/applications/TOMRA/pdf`);
    expect(res.headers.get("content-type")).toBe("application/pdf");
    expect(Buffer.from(await res.arrayBuffer()).subarray(0, 4).toString()).toBe("%PDF");
  });
  it("GET /api/applications/:jd/pick runs library_db.py pick", async () => {
    const p = await (await fetch(`${base}/api/applications/TOMRA/pick`)).json();
    expect(p.exitCode).toBe(0);
    expect(p.current).toMatch(/^scope=SCOPE-ATS;card=VI/);
    expect(p.suggested).toMatch(/^scope=/);
    expect(p.diff.length).toBeGreaterThan(5);
  });
  it("GET /api/applications/:jd/align and /gaps, concepts on the detail", async () => {
    const a = await (await fetch(`${base}/api/applications/Scout24/align`)).json();
    // the recorded Scout24 picks change as the CV is revised, so check shape, not a specific lead swap
    expect(a.exitCode).toBe(0);
    expect(a.rows.length).toBeGreaterThan(0);
    for (const r of a.rows.filter((r: any) => r.actionKind === "use_lead")) expect(typeof r.leadOrd).toBe("number");
    const g = await (await fetch(`${base}/api/applications/Scout24/gaps`)).json();
    expect(g.groups.sotSupported.length).toBe(5);
    const d = await (await fetch(`${base}/api/applications/Scout24`)).json();
    expect(d.concepts.length).toBeGreaterThan(10);
    expect(d.keywords.every((k: any) => "concept" in k)).toBe(true);
  });
  it("POST /api/claude streams a run to exit 0 and keeps history", async () => {
    // CORS-simple text/plain POST (what a hostile page could send) → refused before any run starts
    const bad = await fetch(`${base}/api/claude`, { method: "POST", body: JSON.stringify({ jd: "TOMRA", target: { kind: "x" }, instruction: "hi" }) });
    expect(bad.status).toBe(415);
    const json = { "content-type": "application/json" };
    const body = JSON.stringify({ jd: "TOMRA", target: { kind: "x" }, instruction: "hi" });
    const evil = await fetch(`${base}/api/claude`, { method: "POST", headers: { ...json, origin: "https://evil.example" }, body });
    expect(evil.status).toBe(403);
    const crossSite = await fetch(`${base}/api/claude`, { method: "POST", headers: { ...json, "sec-fetch-site": "cross-site" }, body });
    expect(crossSite.status).toBe(403);
    for (const origin of ["http://localhost:5173", "https://desktop-1.tail546de3.ts.net"]) {
      const ok = await fetch(`${base}/api/claude`, { method: "POST", headers: { ...json, origin }, body });
      expect(ok.status).toBe(400); // passes the guard, fails validation on target kind "x"
    }
    const badDelete = await fetch(`${base}/api/claude/nope`, { method: "DELETE", headers: { origin: "https://evil.example" } });
    expect(badDelete.status).toBe(403);
    const res = await fetch(`${base}/api/claude`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jd: "TOMRA", target: { kind: "bullet", code: "VI1", text: "x" }, instruction: "change nothing" }),
    });
    expect(res.status).toBe(202);
    const { runId } = await res.json();
    // A second run on the same application waits in the queue instead of being refused.
    const second = await fetch(`${base}/api/claude`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jd: "TOMRA", target: { kind: "summary" }, instruction: "again" }),
    });
    expect(second.status).toBe(202);
    const queued = await second.json();
    expect(queued).toMatchObject({ position: 1, status: "queued" });
    expect((await fetch(`${base}/api/claude/${queued.runId}`, { method: "DELETE" })).status).toBe(202);
    const sse = await (await fetch(`${base}/api/claude/${runId}/stream`)).text();
    const events = sse.split("\n\n").filter((b) => b.startsWith("data: ")).map((b) => JSON.parse(b.slice(6)));
    expect(events.map((e) => e.type)).toEqual(["system", "system", "tool", "tool_result", "text", "result", "exit"]);
    expect(events[0].text).toMatch(/^started \(1\/\d running\)$/);
    expect(events[4].text).toBe("argv ok: true");
    expect(events[5].text).toBe(`Done. Changed nothing. run=${runId}`);
    expect(events.at(-1)).toMatchObject({ code: 0, status: "succeeded" });
    const hist = await (await fetch(`${base}/api/claude/runs`)).json();
    expect(hist.running).toEqual([]);
    expect(hist.queued).toEqual([]);
    expect(hist.runs[0]).toMatchObject({ id: queued.runId, status: "cancelled", startedAt: null, position: null });
    expect(hist.runs[1]).toMatchObject({ id: runId, status: "succeeded", exitCode: 0, cvChanges: [], affectedJds: ["TOMRA"] });
    const run = await (await fetch(`${base}/api/claude/${runId}`)).json();
    expect(run.prompt).toContain("Application: TOMRA (cv/applications/20261023_TOMRA_Senior-Python-Software-Engineer/v3_cards)");
    expect(run.prompt).toContain("Target: bullet VI1 'x'");
    expect(run.prompt).toContain(`Coordination: you are dashboard run ${runId}`);
    // bullet VI1 page first, then its project page (ids live in cv/profile/notion_ids.json)
    expect(run.prompt).toContain("Notion evidence pages for this target: https://www.notion.so/40877152eb3d814392f0f82055557a2a, https://www.notion.so/40877152eb3d814eb6dbffe3007a018b");
    expect(run.argv).toContain(
      "--allowedTools=Read,Edit,Write,Glob,Grep,Bash(python3 .claude/skills/tailor-cv/scripts/*),Bash(bash .claude/skills/tailor-cv/scripts/*)," +
        "Bash(python3 tools/cv-dashboard/scripts/shared_lock.py *)," +
        "mcp__plugin_Notion_notion__notion-search,mcp__plugin_Notion_notion__notion-fetch," +
        "mcp__plugin_Notion_notion__notion-create-pages,mcp__plugin_Notion_notion__notion-update-page," +
        "mcp__plugin_Notion_notion__notion-get-async-task",
    );
  });
});
