// Queue, concurrency, run board and the shared lock helper; Claude is the fake CLI with a slower run.
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { BoardEvent, ClaudeRun, RunBoard } from "../shared/types";

const dataDir = mkdtempSync(path.join(tmpdir(), "cvd-queue-"));
process.env.CV_DASHBOARD_DATA = dataDir;
process.env.CLAUDE_BIN = path.resolve(__dirname, "fixtures/fake-claude.mjs");
process.env.FAKE_CLAUDE_DELAY_MS = "1200";

const LOCK_PY = path.resolve(__dirname, "../scripts/shared_lock.py");
const APPS = ["TOMRA", "Ixopay", "Scout24", "AIVenture", "Mapbox"];

let base = "";
let close: () => void = () => undefined;

beforeAll(async () => {
  const { createApp } = await import("../server/http");
  const { ClaudeRunner } = await import("../server/claude");
  const { server } = createApp(new ClaudeRunner({ maxConcurrent: 3 }));
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  close = () => server.close();
});
afterAll(() => {
  close();
  rmSync(dataDir, { recursive: true, force: true });
});

async function post(jd: string, instruction = "change nothing") {
  const res = await fetch(`${base}/api/claude`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jd, target: { kind: "summary" }, instruction }),
  });
  expect(res.status).toBe(202);
  return (await res.json()) as { runId: string; position: number; status: string };
}

/** Resolves when the run's stream ends (its exit event). */
async function finished(id: string): Promise<ClaudeRun> {
  await (await fetch(`${base}/api/claude/${id}/stream`)).text();
  return (await (await fetch(`${base}/api/claude/${id}`)).json()) as ClaudeRun;
}

const boardFile = () => JSON.parse(readFileSync(path.join(dataDir, "run_board.json"), "utf-8")) as RunBoard;

describe("run queue", () => {
  it("5 POSTs → 3 running + 2 queued with positions, then all finish", async () => {
    const started: Awaited<ReturnType<typeof post>>[] = [];
    for (const jd of APPS) started.push(await post(jd, `job for ${jd}`));
    expect(started.map((s) => [s.status, s.position])).toEqual([
      ["running", 0], ["running", 0], ["running", 0], ["queued", 1], ["queued", 2],
    ]);
    const runs = await (await fetch(`${base}/api/claude/runs`)).json();
    expect(runs.running).toEqual(started.slice(0, 3).map((s) => s.runId));
    expect(runs.queued).toEqual(started.slice(3).map((s) => s.runId));
    expect(runs.runs.find((r: { id: string }) => r.id === started[4].runId).position).toBe(2);

    // the board file lists every run with application, instruction, status and position
    const board = boardFile();
    expect(board).toMatchObject({ maxConcurrent: 3, running: 3, queued: 2 });
    expect(board.runs.map((r) => [r.jd, r.status, r.position])).toEqual([
      ["TOMRA", "running", null], ["Ixopay", "running", null], ["Scout24", "running", null],
      ["AIVenture", "queued", 1], ["Mapbox", "queued", 2],
    ]);
    expect(board.runs[3]).toMatchObject({ instruction: "job for AIVenture", startedAt: null, filesChanged: [] });
    expect(board.runs[0].appDir).toMatch(/^cv\/applications\/.*TOMRA/);

    const done = await Promise.all(started.map((s) => finished(s.runId)));
    expect(done.map((r) => r.status)).toEqual(Array(5).fill("succeeded"));
    // queued runs started only after a slot was freed
    const firstEnd = Math.min(...done.slice(0, 3).map((r) => Date.parse(r.endedAt!)));
    for (const r of done.slice(3)) expect(Date.parse(r.startedAt!)).toBeGreaterThanOrEqual(firstEnd);
    // a run started later sees the others in its prompt
    expect(done[3].prompt).toContain("Other runs in progress:\n- Ixopay (summary, running): job for Ixopay");
    expect(done[3].prompt).toContain("- Mapbox (summary, queued): job for Mapbox");
    expect(done[3].prompt).toContain("run_board.json");
    expect(done[3].prompt).toContain(`shared_lock.py hold --run ${done[3].id} --seconds 600`);
  });

  it("runs on the same application never overlap", async () => {
    const a = await post("Taktile", "first");
    const b = await post("Taktile", "second");
    const c = await post("TOMRA", "other app");
    expect([a.status, b.status, b.position, c.status]).toEqual(["running", "queued", 1, "running"]);
    const [ra, rb] = await Promise.all([finished(a.runId), finished(b.runId), finished(c.runId)]);
    expect(Date.parse(rb.startedAt!)).toBeGreaterThanOrEqual(Date.parse(ra.endedAt!));
    expect(rb.events[0]).toMatchObject({ type: "system", text: "queued at position 1 (waits for the other Taktile run)" });
  });

  it("cancels a queued run without starting it", async () => {
    const live = await Promise.all(APPS.slice(0, 3).map((jd) => post(jd)));
    const q = await post("Mapbox", "never runs");
    expect(q).toMatchObject({ status: "queued", position: 1 });
    const stream = fetch(`${base}/api/claude/${q.runId}/stream`).then((r) => r.text());
    expect((await fetch(`${base}/api/claude/${q.runId}`, { method: "DELETE" })).status).toBe(202);
    const sse = await stream;
    expect(sse).toContain('"type":"exit","code":null,"signal":null,"status":"cancelled"');
    const run = (await (await fetch(`${base}/api/claude/${q.runId}`)).json()) as ClaudeRun;
    expect(run).toMatchObject({ status: "cancelled", startedAt: null, prompt: "", argv: [] });
    expect((await fetch(`${base}/api/claude/${q.runId}`, { method: "DELETE" })).status).toBe(409);
    expect(boardFile().runs.find((r) => r.id === q.runId)).toMatchObject({ status: "cancelled", position: null });
    await Promise.all(live.map((s) => finished(s.runId)));
  });

  it("board tracks files changed and last log lines; /events pushes board-changed", async () => {
    const ctrl = new AbortController();
    const events: BoardEvent[] = [];
    const sub = fetch(`${base}/api/claude/events`, { signal: ctrl.signal }).then(async (res) => {
      const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) return;
        buf += value;
        let i: number;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i);
          buf = buf.slice(i + 2);
          if (block.startsWith("data: ")) events.push(JSON.parse(block.slice(6)));
        }
      }
    }).catch(() => undefined);

    const s = await post("Scout24", "FAKE_EDIT the seed");
    await new Promise((r) => setTimeout(r, 700));
    const mid = boardFile().runs.find((r) => r.id === s.runId)!;
    expect(mid.status).toBe("running");
    expect(mid.filesChanged).toEqual(["cv/profile/library_seed.json"]);
    expect(mid.lastLines).toHaveLength(3);
    expect(mid.lastLines.at(-1)).toBe("argv ok: true");

    await finished(s.runId);
    await new Promise((r) => setTimeout(r, 100));
    ctrl.abort();
    await sub;
    const end = boardFile().runs.find((r) => r.id === s.runId)!;
    expect(end).toMatchObject({ status: "succeeded", filesChanged: ["cv/profile/library_seed.json"] });
    expect(end.lastLines.at(-1)).toMatch(/^result: Done\./);
    expect(events[0].type).toBe("board");
    expect(events.find((e) => e.type === "board-changed")).toMatchObject({ runId: s.runId, jds: ["Scout24"], changedLines: {} });
  });
});

describe("shared_lock.py", () => {
  const work = mkdtempSync(path.join(tmpdir(), "cvd-lock-"));
  const lockData = path.join(work, "data");
  mkdirSync(path.join(work, ".claude/skills/tailor-cv/scripts"), { recursive: true });
  // a stand-in "tailor-cv script": sleeps, then appends its name to order.txt
  writeFileSync(
    path.join(work, ".claude/skills/tailor-cv/scripts/mark.py"),
    'import sys, time\ntime.sleep(float(sys.argv[2]))\nopen("order.txt", "a").write(sys.argv[1] + "\\n")\n',
  );
  afterAll(() => rmSync(work, { recursive: true, force: true }));

  function lock(...args: string[]): Promise<{ code: number | null; out: string; err: string; endedAt: number }> {
    return new Promise((resolve) => {
      const p = spawn("python3", [LOCK_PY, ...args], { cwd: work, env: { ...process.env, CV_DASHBOARD_DATA: lockData } });
      let out = "";
      let err = "";
      p.stdout.on("data", (d) => (out += d));
      p.stderr.on("data", (d) => (err += d));
      p.on("close", (code) => resolve({ code, out, err, endedAt: Date.now() }));
    });
  }
  const order = () => readFileSync(path.join(work, "order.txt"), "utf-8");

  it("second process waits for the first; both complete in order", async () => {
    const a = lock("run", "--run", "A", "--", "python3", ".claude/skills/tailor-cv/scripts/mark.py", "A", "1.0");
    await new Promise((r) => setTimeout(r, 300));
    const b = lock("run", "--run", "B", "--", "python3", ".claude/skills/tailor-cv/scripts/mark.py", "B", "0");
    const [ra, rb] = await Promise.all([a, b]);
    expect([ra.code, rb.code]).toEqual([0, 0]);
    expect(order()).toBe("A\nB\n");
    expect(rb.err).toContain("waiting for shared lock, held by run A");
    expect(rb.err).toContain("shared lock acquired by run B");
    expect(rb.endedAt).toBeGreaterThanOrEqual(ra.endedAt);
    expect((await lock("status")).out.trim()).toBe("free");
  });

  it("hold … release: others wait, the holder's own run passes, release frees it", async () => {
    rmSync(path.join(work, "order.txt"), { force: true });
    const h = await lock("hold", "--run", "H", "--seconds", "60");
    expect(h.code).toBe(0);
    expect((await lock("status")).out).toMatch(/^held by run H /);
    const waiter = lock("run", "--run", "W", "--", "python3", ".claude/skills/tailor-cv/scripts/mark.py", "W", "0");
    const own = await lock("run", "--run", "H", "--", "python3", ".claude/skills/tailor-cv/scripts/mark.py", "H", "0");
    expect(own.code).toBe(0);
    expect((await lock("release", "--run", "W")).code).toBe(1); // not W's lock
    await new Promise((r) => setTimeout(r, 500));
    expect(order()).toBe("H\n");
    expect((await lock("release", "--run", "H")).code).toBe(0);
    expect((await waiter).code).toBe(0);
    expect(order()).toBe("H\nW\n");
  });

  it("only runs tailor-cv scripts", async () => {
    expect((await lock("run", "--", "rm", "-rf", "x")).code).toBe(1);
    expect((await lock("run", "--", "python3", ".claude/skills/tailor-cv/scripts/../../x.py")).code).toBe(1);
  });
});
