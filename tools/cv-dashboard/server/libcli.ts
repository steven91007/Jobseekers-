// library_db.py helpers (spawned without a shell; stdout is display text).
import { spawn } from "node:child_process";
import type { PickDiffRow, PickSuggestion } from "../shared/types.js";
import { LIBRARY_DB_PY, PYTHON, REPO_ROOT } from "./config.js";

export interface CliResult {
  stdout: string;
  stderr: string;
  code: number | null;
}

export function libraryDb(args: string[], timeoutMs = 60_000): Promise<CliResult> {
  return new Promise((resolve) => {
    const child = spawn(PYTHON, [LIBRARY_DB_PY, ...args], {
      cwd: REPO_ROOT,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf-8").on("data", (d: string) => (stdout += d));
    child.stderr.setEncoding("utf-8").on("data", (d: string) => (stderr += d));
    const timer = setTimeout(() => child.kill("SIGKILL"), timeoutMs);
    child.on("error", (e) => {
      clearTimeout(timer);
      resolve({ stdout, stderr: stderr + String(e), code: null });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ stdout, stderr, code });
    });
  });
}

/** `kind=CODE` without the variant part — the key two pick strings are aligned on. */
function pickKey(token: string): string {
  const [kind, rest = ""] = token.split(/=(.*)/s);
  return `${kind}=${rest.split(":")[0].split("=")[0]}`;
}

export function diffPicks(current: string, suggested: string): PickDiffRow[] {
  const cur = current.split(";").map((s) => s.trim()).filter(Boolean);
  const sug = suggested.split(";").map((s) => s.trim()).filter(Boolean);
  const sugByKey = new Map(sug.map((s) => [pickKey(s), s]));
  const rows: PickDiffRow[] = [];
  const seen = new Set<string>();
  for (const c of cur) {
    const key = pickKey(c);
    seen.add(key);
    const s = sugByKey.get(key) ?? null;
    rows.push({ key, current: c, suggested: s, state: s == null ? "current only" : s === c ? "same" : "changed" });
  }
  for (const s of sug) {
    const key = pickKey(s);
    if (!seen.has(key)) rows.push({ key, current: null, suggested: s, state: "suggested only" });
  }
  return rows;
}

export function parsePickOutput(raw: string, current: string): Omit<PickSuggestion, "stderr" | "exitCode"> {
  const lines = raw.split(/\r?\n/).filter((l) => l.trim());
  const pickIdx = lines.findIndex((l) => /^(scope|card|bullet|skill_row)=/.test(l.trim()));
  const header = lines.slice(0, Math.max(pickIdx, 0)).join("\n");
  const suggested = pickIdx >= 0 ? lines[pickIdx].trim() : "";
  const after = pickIdx >= 0 ? lines.slice(pickIdx + 1) : lines;
  const fitStart = after.findIndex((l) => /^(ok|warn|fail|over|FAIL|WARN)\s{2,}/i.test(l));
  const rationale = fitStart >= 0 ? after.slice(0, fitStart) : after.filter((l) => !/^fit:/.test(l));
  const fit = fitStart >= 0 ? after.slice(fitStart) : after.filter((l) => /^fit:/.test(l));
  const verdictLine = fit.find((l) => /^fit:/.test(l));
  return {
    header,
    suggested,
    current,
    diff: diffPicks(current, suggested),
    rationale,
    fit,
    fitVerdict: verdictLine ? verdictLine.replace(/^fit:\s*/, "") : null,
    raw,
  };
}

export async function pickSuggestion(jd: string): Promise<PickSuggestion> {
  const [pick, picks] = await Promise.all([libraryDb(["pick", "--jd", jd]), libraryDb(["picks", jd])]);
  const current = picks.stdout.trim().split(/\r?\n/)[0] ?? "";
  return {
    ...parsePickOutput(pick.stdout, current),
    stderr: [pick.stderr, picks.stderr].filter(Boolean).join("\n"),
    exitCode: pick.code,
  };
}
