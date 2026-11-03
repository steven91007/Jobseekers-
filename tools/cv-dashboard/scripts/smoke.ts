// Scripted smoke check against a running dashboard API (npm run dev or npm start):
//   npm run smoke                      → read-only API checks
//   npm run smoke -- --claude          → also two real concurrent read-only Claude runs; writes SMOKE.md
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { ApplicationRow, ClaudeRun, RunBoard, RunEvent } from "../shared/types";
import { DASHBOARD_DIR, REPO_ROOT } from "../server/config";

const BASE = process.env.CV_DASHBOARD_URL ?? "http://127.0.0.1:5174";
const INSTRUCTION =
  "Read SKILL.md and tools/cv-dashboard/data/run_board.json, reply with the other run's application, change nothing";

function check(ok: boolean, msg: string): void {
  console.log(`${ok ? "ok  " : "FAIL"} ${msg}`);
  if (!ok) process.exitCode = 1;
}

const rows = (await (await fetch(`${BASE}/api/applications`)).json()) as ApplicationRow[];
check(rows.length === 6, `GET /api/applications → ${rows.length} rows`);
// scores change whenever a CV is revised, so compare each row with its own 07_external_ats.json
for (const r of rows) {
  const ats = JSON.parse(readFileSync(path.join(REPO_ROOT, r.dir, "07_external_ats.json"), "utf8")).ats_score.overall_score;
  check(r.atsOverall === ats, `${r.jd} ATS overall ${r.atsOverall} (file ${ats})`);
}

if (process.argv.includes("--claude")) {
  // Two real read-only runs on different applications at the same time; each must find the other on the board.
  const target = { kind: "other", text: ".claude/skills/tailor-cv/SKILL.md" };
  const jds = ["TOMRA", "Ixopay"];
  const started = Date.now();
  const posted: { jd: string; runId: string; status: string }[] = [];
  for (const jd of jds) {
    const res = await fetch(`${BASE}/api/claude`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jd, target, instruction: INSTRUCTION }),
    });
    const body = (await res.json()) as { runId: string; status: string };
    check(res.status === 202 && Boolean(body.runId), `POST /api/claude ${jd} → ${res.status} ${body.runId} (${body.status})`);
    posted.push({ jd, ...body });
  }
  const board = (await (await fetch(`${BASE}/api/claude/board`)).json()) as RunBoard;
  check(board.running === 2, `board: ${board.running} running, ${board.queued} queued`);

  async function follow(jd: string, runId: string): Promise<RunEvent[]> {
    const events: RunEvent[] = [];
    const stream = await fetch(`${BASE}/api/claude/${runId}/stream`);
    const reader = stream.body!.pipeThrough(new TextDecoderStream()).getReader();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let i: number;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        if (!block.startsWith("data: ")) continue;
        const ev = JSON.parse(block.slice(6)) as RunEvent;
        events.push(ev);
        const line = ev.type === "text" || ev.type === "result" ? ev.text : ev.type === "tool" ? `${ev.name} ${ev.summary}` : ev.type === "exit" ? `exit ${ev.code} ${ev.status}` : "";
        if (line) console.log(`  ${jd} [${ev.type}] ${line.replace(/\s+/g, " ").slice(0, 160)}`);
      }
    }
    return events;
  }
  const all = await Promise.all(posted.map((p) => follow(p.jd, p.runId)));

  const md = ["# Smoke run — two concurrent POST /api/claude", "", `Date: ${new Date(started).toISOString()} · wall time ${Math.round((Date.now() - started) / 1000)} s`, ""];
  md.push(`Instruction (both): \`${INSTRUCTION}\``, "", `Board right after both POSTs: ${board.running} running, ${board.queued} queued.`, "");
  for (const [k, p] of posted.entries()) {
    const other = posted[1 - k].jd;
    const run = (await (await fetch(`${BASE}/api/claude/${p.runId}`)).json()) as ClaudeRun;
    const events = all[k];
    const exit = events.find((e) => e.type === "exit") as Extract<RunEvent, { type: "exit" }> | undefined;
    check(exit?.code === 0, `${p.jd}: stream ended with exit code ${exit?.code}`);
    check(run.cvChanges.length === 0 && (run.filesChanged ?? []).length === 0, `${p.jd}: changed nothing`);
    check(new RegExp(`\\b${other}\\b`).test(run.resultText ?? ""), `${p.jd}: answer names the other run's application (${other})`);
    const tools = events.filter((e): e is Extract<RunEvent, { type: "tool" }> => e.type === "tool");
    md.push(
      `## ${p.jd} · run \`${p.runId}\``,
      "",
      `Started ${run.startedAt} · ended ${run.endedAt} · status **${run.status}**, exit **${run.exitCode}**, cost ` +
        `${run.costUsd != null ? `$${run.costUsd.toFixed(4)}` : "n/a"}, 03_cv.md changed lines ${run.cvChanges.length}, files edited ${(run.filesChanged ?? []).length}.`,
      "",
      "Tool calls:",
      "",
      ...tools.map((t) => `- ${t.name} ${t.summary}`),
      "",
      "Final answer:",
      "",
      ...(run.resultText ?? "").split("\n").map((l) => `> ${l}`),
      "",
    );
  }
  const first = (await (await fetch(`${BASE}/api/claude/${posted[1].runId}`)).json()) as ClaudeRun;
  md.push("## Coordination block of the second run's prompt", "", "```", first.prompt.slice(first.prompt.indexOf("Coordination:")), "```", "");
  writeFileSync(path.join(DASHBOARD_DIR, "SMOKE.md"), md.join("\n"));
  console.log("wrote SMOKE.md");
}
