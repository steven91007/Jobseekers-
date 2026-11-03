// Pipeline page backend: the Google Sheet tabs 每日職缺 / 興趣職缺 / 投遞表單 through the Python backend
// (daily-job-tracker push_to_sheet.py → Google Sheets API with the shared Google login). Credentials stay
// with Python; only sheet_url from the config reaches the browser.
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  DECISIONS,
  type DailyRow,
  type Decision,
  type InterestAppendResult,
  type InterestRow,
  type PipelineList,
  type SubmissionRow,
} from "../shared/pipeline.js";
import { listApplicationRecs } from "./application.js";
import { PYTHON, REPO_ROOT, relToRepo, resolveAppDir } from "./config.js";

export class PipelineError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const PUSH_TO_SHEET = path.join(REPO_ROOT, ".claude", "skills", "daily-job-tracker", "scripts", "push_to_sheet.py");
const SHEET_TIMEOUT_MS = 180_000;

/** Read at call time so AI_JOB_TRACKER_SHEET_CONFIG can change (tests) without a restart. */
export function sheetConfigPath(): string {
  return process.env.AI_JOB_TRACKER_SHEET_CONFIG ?? path.join(os.homedir(), ".config", "ai-job-tracker", "sheet.json");
}

export const NOT_LIVE_MESSAGE =
  "Pipeline 未連正式 Google Sheet:用 npm run dev:live(或 start:live)啟動;測試請設 AI_JOB_TRACKER_SHEET_CONFIG";
export const AUTH_MESSAGE = "Google 授權失效或未設定:在 WSL 執行 google_auth.py --auth";

const live = () => process.env.PIPELINE_SHEET_LIVE === "1";

/** The real Sheet only with an explicit opt-in (PIPELINE_SHEET_LIVE=1, npm run dev:live / start:live)
 *  or an explicit config path (tests and the fake server). Checked before any file read or spawn. */
function requireSheet(): void {
  const explicit = process.env.AI_JOB_TRACKER_SHEET_CONFIG;
  if (!explicit && !live()) throw new PipelineError(503, NOT_LIVE_MESSAGE);
  if (explicit && !existsSync(explicit)) throw new PipelineError(503, `找不到 Google Sheet 設定檔:${explicit}`);
}

type SheetResult = Record<string, unknown>;

/**
 * One action through `push_to_sheet.py --action NAME -` (body JSON on stdin, result JSON on stdout;
 * exit ≠ 0 = error with "Sheet error: …" on stderr). `--live` only with PIPELINE_SHEET_LIVE=1.
 */
export async function sheetAction(action: string, extra: Record<string, unknown> = {}): Promise<SheetResult> {
  requireSheet();
  const argv = ["-I", PUSH_TO_SHEET, "--action", action, "-", ...(live() ? ["--live"] : [])];
  const { code, stdout, stderr } = await new Promise<{ code: number | null; stdout: string; stderr: string }>((resolve, reject) => {
    const child = spawn(PYTHON, argv, { cwd: REPO_ROOT, env: { ...process.env, PYTHONIOENCODING: "utf-8" }, stdio: ["pipe", "pipe", "pipe"] });
    let out = "";
    let err = "";
    const timer = setTimeout(() => child.kill("SIGTERM"), SHEET_TIMEOUT_MS);
    child.stdout.setEncoding("utf-8").on("data", (c: string) => (out += c));
    child.stderr.setEncoding("utf-8").on("data", (c: string) => (err += c));
    child.on("error", (e) => {
      clearTimeout(timer);
      reject(new PipelineError(502, `無法執行 push_to_sheet.py:${e.message}`));
    });
    child.on("close", (c) => {
      clearTimeout(timer);
      resolve({ code: c, stdout: out, stderr: err });
    });
    child.stdin.on("error", () => undefined); // child exited before reading stdin
    child.stdin.end(JSON.stringify(extra));
  });
  if (code !== 0) {
    if (/google_auth\.py --auth|python3-google-auth-oauthlib/.test(stderr)) throw new PipelineError(503, AUTH_MESSAGE);
    const msg = stderr.trim().split("\n").filter(Boolean).at(-1)?.replace(/^Sheet error:\s*/, "") || `exit ${code}`;
    throw new PipelineError(502, `Google Sheet 錯誤:${msg}`);
  }
  try {
    return JSON.parse(stdout) as SheetResult;
  } catch {
    throw new PipelineError(502, "push_to_sheet.py 的輸出不是 JSON");
  }
}

function sheetUrl(): string | null {
  try {
    const cfg = JSON.parse(readFileSync(sheetConfigPath(), "utf-8")) as { sheet_url?: unknown };
    return typeof cfg.sheet_url === "string" ? cfg.sheet_url : null;
  } catch {
    return null;
  }
}

export function validHttpUrl(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const s = v.trim();
  if (!s || s.length > 2000) return null;
  try {
    const u = new URL(s);
    return u.protocol === "http:" || u.protocol === "https:" ? s : null;
  } catch {
    return null;
  }
}

function requireUrl(v: unknown): string {
  const u = validHttpUrl(v);
  if (!u) throw new PipelineError(400, "需要有效的 http(s) 連結");
  return u;
}

function rows<T>(r: SheetResult, key = "rows"): T[] {
  return Array.isArray(r[key]) ? (r[key] as T[]) : [];
}

export async function dailyList(): Promise<PipelineList<DailyRow>> {
  const r = await sheetAction("pipeline_list", { all: true });
  return { rows: rows<DailyRow>(r), sheetUrl: sheetUrl() };
}

export async function interestList(): Promise<PipelineList<InterestRow>> {
  const r = await sheetAction("interest_list", { all: true });
  return { rows: rows<InterestRow>(r), sheetUrl: sheetUrl() };
}

/** app_dir ("cv/applications/<dir>" with or without a sub-folder such as /v3_cards) → applications.jd. */
export function resolveSubmissionJd(appDirText: string, recs: { jd: string; dir: string }[]): string | null {
  const norm = (d: string) => relToRepo(resolveAppDir(d.trim())).replace(/\/+$/, "");
  if (!appDirText.trim()) return null;
  const want = norm(appDirText);
  const hit = recs.find((r) => {
    const have = norm(r.dir);
    return have === want || have.startsWith(want + "/") || want.startsWith(have + "/");
  });
  return hit?.jd ?? null;
}

export async function submissionsList(): Promise<PipelineList<SubmissionRow>> {
  const r = await sheetAction("submissions_list");
  let recs: { jd: string; dir: string }[] = [];
  try {
    recs = listApplicationRecs();
  } catch {
    /* library DB unavailable: rows still show, without CV links */
  }
  const out = rows<Omit<SubmissionRow, "jd">>(r).map((s) => ({ ...s, jd: resolveSubmissionJd(String(s.app_dir ?? ""), recs) }));
  return { rows: out, sheetUrl: sheetUrl() };
}

async function updateDaily(row: Record<string, unknown>): Promise<{ url: string; row: number }> {
  const r = await sheetAction("pipeline_update", { rows: [row] });
  const updated = rows<{ url: string; row: number }>(r, "updated");
  if (updated.length) return updated[0];
  if (rows(r, "not_found").length) throw new PipelineError(404, "每日職缺找不到這個連結");
  const rej = rows<{ bad?: string[] }>(r, "rejected")[0];
  throw new PipelineError(400, `Google Sheet 拒絕更新:${(rej?.bad ?? []).join(", ") || "未知原因"}`);
}

export async function setDecision(body: unknown): Promise<{ url: string; row: number }> {
  const b = (body ?? {}) as Record<string, unknown>;
  const url = requireUrl(b.url);
  if (!DECISIONS.includes(b.decision as Decision)) throw new PipelineError(400, `決定必須是 ${DECISIONS.join(" / ")}`);
  return updateDaily({ url, decision: b.decision });
}

export async function setNote(body: unknown): Promise<{ url: string; row: number }> {
  const b = (body ?? {}) as Record<string, unknown>;
  const url = requireUrl(b.url);
  if (typeof b.note !== "string") throw new PipelineError(400, "備註必須是文字");
  if (b.note.length > 2000) throw new PipelineError(400, "備註太長(最多 2000 字)");
  return updateDaily({ url, note: b.note });
}

export async function addInterest(body: unknown): Promise<InterestAppendResult> {
  const b = (body ?? {}) as Record<string, unknown>;
  const url = requireUrl(b.url);
  if (b.note !== undefined && typeof b.note !== "string") throw new PipelineError(400, "備註必須是文字");
  const note = typeof b.note === "string" ? b.note.trim().slice(0, 2000) : "";
  const r = await sheetAction("interest_append", { rows: [{ url, note }] });
  return { added: rows(r, "added"), skipped: rows(r, "skipped") };
}

/** 「標記已送出」 on a referral candidate: 內推狀態 = ⚠️等待回覆 and a dated 求職過程 line (push_to_sheet.py referral_sent). */
export async function referralSent(body: unknown): Promise<{ url: string; row: number }> {
  const b = (body ?? {}) as Record<string, unknown>;
  const url = requireUrl(b.url);
  const name = typeof b.name === "string" ? b.name.trim() : "";
  if (!name || name.length > 200) throw new PipelineError(400, "需要內推人的名字");
  const r = await sheetAction("referral_sent", { rows: [{ url, name }] });
  const updated = rows<{ url: string; row: number }>(r, "updated");
  if (!updated.length) throw new PipelineError(404, "投遞表單找不到這個連結");
  return updated[0];
}
