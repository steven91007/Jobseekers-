// Auto apply, in two separate steps (the candidate may be on a phone over Tailscale):
//  1. FILL: a headless Claude run with the job-apply plugin (neonwatty/job-apply-plugin) fills the
//     form in the persistent apply browser on this machine and STOPS at the review page. It can never
//     submit (prompt + denied tools + scripts/apply-guard.js confirm() gate). Outputs: step-NN.png,
//     review.png, fields.json, summary.md, status.json (filled_awaiting_confirm | blocked_* | failed).
//  2. SUBMIT: only POST …/apply/submit (explicit button, two confirmations) runs
//     scripts/apply-submit.mjs — plain CDP code, no LLM, not the plugin — which reattaches to the SAME
//     browser/tab, clicks the recorded final control, screenshots the confirmation and sets submitted;
//     then the job's Google Sheet row is set to 狀態 履歷送出.
//  The Live browser view (server/live.ts) can also submit: the candidate answers the guard dialog there.
// Browser: Playwright Chromium on a persistent profile (data/apply/profile), CDP 127.0.0.1:9223,
// started by scripts/apply-browser.sh and left running between the two steps.
import { spawn, spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ApplyField, ApplyInfo, ApplyState, ApplyStatusFile, ClaudeRun } from "../shared/types.js";
import type { AppRec } from "./db.js";
import { DASHBOARD_DIR, DATA_DIR, PYTHON, REPO_ROOT } from "./config.js";
import { findPdf, readJson, readText } from "./artifacts.js";

export const APPLY_DIR = path.join(DATA_DIR, "apply");
export const APPLY_PROFILE_DIR = path.resolve(process.env.APPLY_BROWSER_DIR ?? path.join(APPLY_DIR, "profile"));
export const APPLY_CDP_PORT = Number(process.env.APPLY_CDP_PORT ?? 9223);
export const CDP_ENDPOINT = `http://127.0.0.1:${APPLY_CDP_PORT}`;
export const PLAYWRIGHT_MCP = process.env.APPLY_PLAYWRIGHT_MCP ?? "@playwright/mcp@0.0.83";
const GUARD_JS = path.join(DASHBOARD_DIR, "scripts", "apply-guard.js");
const BROWSER_SH = path.join(DASHBOARD_DIR, "scripts", "apply-browser.sh");
const SUBMIT_MJS = path.join(DASHBOARD_DIR, "scripts", "apply-submit.mjs");
const SHEET_PUSH = process.env.CV_SHEET_PUSH ?? path.join(REPO_ROOT, ".claude/skills/daily-job-tracker/scripts/push_to_sheet.py");
// must be one of the Sheet's 狀態 dropdown options (push_to_sheet.py --statuses)
export const SHEET_STATUS = "履歷送出";
const LIBRARY_DB = path.join(REPO_ROOT, ".claude/skills/tailor-cv/scripts/library_db.py");
export const SHEET_NOTE = "submitted from CV dashboard (Auto apply)";
export const APPLY_TIMEOUT_MS = 25 * 60 * 1000;
const SUBMIT_TIMEOUT_MS = 3 * 60 * 1000;

export const FILL_STATES: ApplyState[] = ["filled_awaiting_confirm", "blocked_login", "blocked_captcha", "blocked_mfa", "failed"];

/** What to do at the desk for each blocker (shown in the Apply status panel). */
export const BLOCKER_HELP: Record<string, string> = {
  blocked_login:
    "The site needs you to sign in (or create an account). At the PC, sign in inside the apply browser window (it keeps the login in data/apply/profile), then press Auto apply again. Away from the PC you can do it in Live browser.",
  blocked_captcha:
    "A CAPTCHA / bot check appeared before the form. At the PC, solve it in the apply browser window, then press Auto apply again (Live browser works from the phone). The assistant never solves CAPTCHAs.",
  blocked_mfa:
    "The site asked for a verification code / MFA. At the PC, complete it in the apply browser window (or in Live browser), then press Auto apply again.",
  failed: "The fill run did not finish. Open the run log in the Claude drawer, fix the URL if needed and press Auto apply again.",
  page_lost: "The apply browser was closed, so the filled form is gone. Press Auto apply again to refill it.",
};

/** Playwright MCP tools the fill run may not use: run_code_unsafe executes arbitrary code in the MCP's
 *  Node process (RCE-equivalent: files, network, dialog auto-accept), evaluate runs page JS that could
 *  call form.submit() or stub confirm(), and handle_dialog could accept the guard's confirm(). */
export const APPLY_DENIED_TOOLS = [
  "mcp__playwright__browser_run_code_unsafe",
  "mcp__playwright__browser_run_code",
  "mcp__playwright__browser_evaluate",
  "mcp__playwright__browser_handle_dialog",
  "mcp__playwright__browser_close",
  "mcp__playwright__browser_install",
];

export function applyDir(jd: string): string {
  if (!/^[\w.-]+$/.test(jd)) throw new Error(`bad application key: ${jd}`);
  return path.join(APPLY_DIR, jd);
}

/** Install path of job-apply@neonwatty-plugins (JOB_APPLY_PLUGIN_ROOT overrides). */
export function pluginRoot(): string | null {
  if (process.env.JOB_APPLY_PLUGIN_ROOT) return process.env.JOB_APPLY_PLUGIN_ROOT;
  const reg = readJson<{ plugins?: Record<string, { installPath?: string }[]> }>(
    path.join(os.homedir(), ".claude", "plugins", "installed_plugins.json"),
  );
  const p = reg?.plugins?.["job-apply@neonwatty-plugins"]?.[0]?.installPath;
  return p && existsSync(path.join(p, "scripts", "job-apply-store.py")) ? p : null;
}

/** cv/cover_letters/<YYYYMMDD>_<Co>_<Role>/ — written by the /cover-letter skill (COVER_LETTERS_DIR overrides). */
export const COVER_LETTERS_DIR = path.resolve(process.env.COVER_LETTERS_DIR ?? path.join(REPO_ROOT, "cv", "cover_letters"));
const COVER_SKILL_DIR = path.join(REPO_ROOT, ".claude", "skills", "cover-letter");

export interface CoverLetter {
  dir: string;
  pdf: string | null;
  txt: string | null;
  /** review.md exists = the Codex score ran. */
  scored: boolean;
}

/** Newest letter folder whose <Co> segment matches the application (jd key, company, or its first word). */
export function findCoverLetter(rec: Pick<AppRec, "jd" | "company">): CoverLetter | null {
  if (!existsSync(COVER_LETTERS_DIR)) return null;
  const want = new Set([rec.jd, rec.company, rec.company?.split(/\s+/)[0]].filter(Boolean).map((x) => String(x).toLowerCase()));
  const dirs = readdirSync(COVER_LETTERS_DIR)
    .filter((d) => /^\d{8}_[^_]+_/.test(d) && want.has(d.split("_")[1].toLowerCase()))
    .sort()
    .reverse();
  for (const d of dirs) {
    const dir = path.join(COVER_LETTERS_DIR, d);
    if (!statSync(dir).isDirectory()) continue;
    const pdf = readdirSync(dir).find((n) => /^CoverLetter_.*\.pdf$/i.test(n));
    const txt = existsSync(path.join(dir, "letter.txt")) ? path.join(dir, "letter.txt") : null;
    if (!pdf && !txt) continue;
    return { dir, pdf: pdf ? path.join(dir, pdf) : null, txt, scored: existsSync(path.join(dir, "review.md")) };
  }
  return null;
}

const URL_RE = /https?:\/\/[^\s)>\]"'`]+/;

/** Job URL: the one saved by the last Apply (url.txt), else the first URL in the JD files. */
export function jobUrl(jd: string, dir: string): { url: string | null; source: string | null } {
  const saved = readText(path.join(applyDir(jd), "url.txt"))?.trim();
  if (saved) return { url: saved, source: "data/apply/url.txt" };
  const parent = path.dirname(dir);
  for (const f of [path.join(dir, "01_jd.md"), path.join(dir, "jd.md"), path.join(parent, "01_jd.md"), path.join(parent, "jd.md")]) {
    const m = readText(f)?.match(URL_RE);
    if (m) return { url: m[0].replace(/[.,;]+$/, ""), source: path.relative(REPO_ROOT, f).split(path.sep).join("/") };
  }
  return { url: null, source: null };
}

export function validJobUrl(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  try {
    const u = new URL(raw.trim());
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

const SHOT_RE = /^(step-\d{2}|review|pre-submit|confirmation)\.png$/;

export function listScreenshots(jd: string): { name: string; mtime: number }[] {
  const dir = applyDir(jd);
  if (!existsSync(dir)) return [];
  const order = (n: string) => (n.startsWith("step-") ? 0 : n === "review.png" ? 1 : n === "pre-submit.png" ? 2 : 3);
  return readdirSync(dir)
    .filter((n) => SHOT_RE.test(n))
    .sort((a, b) => order(a) - order(b) || a.localeCompare(b))
    .map((name) => ({ name, mtime: statSync(path.join(dir, name)).mtimeMs }));
}

export function screenshotPath(jd: string, name = "review.png"): string | null {
  if (!SHOT_RE.test(name)) return null;
  const f = path.join(applyDir(jd), name);
  return existsSync(f) ? f : null;
}

function readFields(out: string): ApplyField[] | null {
  const raw = readJson<unknown>(path.join(out, "fields.json"));
  const arr = Array.isArray(raw) ? raw : raw && typeof raw === "object" && Array.isArray((raw as { fields?: unknown }).fields) ? (raw as { fields: unknown[] }).fields : null;
  if (!arr) return null;
  return arr
    .filter((f): f is Record<string, unknown> => !!f && typeof f === "object")
    .map((f) => ({
      label: String(f.label ?? f.field ?? ""),
      value: f.value == null ? "" : String(f.value),
      source: (["profile", "answers", "generated", "inferred", "blank"].includes(String(f.source)) ? String(f.source) : "blank") as ApplyField["source"],
      edited: f.edited === true,
      required: f.required === true,
      step: typeof f.step === "number" ? f.step : undefined,
      note: typeof f.note === "string" ? f.note : undefined,
    }));
}

const submitting = new Set<string>();

export function applyInfo(rec: AppRec, dir: string): ApplyInfo {
  const out = applyDir(rec.jd);
  const { url, source } = jobUrl(rec.jd, dir);
  const status = readJson<ApplyStatusFile>(path.join(out, "status.json"));
  const helpKey = status?.pageLost ? "page_lost" : status?.state;
  return {
    jd: rec.jd,
    url,
    urlSource: source,
    cvPdf: findPdf(dir),
    coverLetter: coverInfo(rec, status),
    pluginRoot: pluginRoot(),
    status,
    help: helpKey ? BLOCKER_HELP[helpKey] ?? null : null,
    canSubmit: status?.state === "filled_awaiting_confirm" && !submitting.has(rec.jd),
    submitting: submitting.has(rec.jd),
    fields: readFields(out),
    summary: readText(path.join(out, "summary.md")),
    screenshots: listScreenshots(rec.jd),
    sheet: readJson(path.join(out, "sheet.json")),
  };
}

function coverInfo(rec: AppRec, status: ApplyStatusFile | null): ApplyInfo["coverLetter"] {
  const found = findCoverLetter(rec);
  const reported = status?.coverLetter;
  if (!found) return reported && reported.source !== "none" ? { ...reported, pdf: null, available: false } : null;
  return {
    source: reported?.source === "generated" ? "generated" : "existing",
    dir: path.relative(REPO_ROOT, found.dir).split(path.sep).join("/"),
    pdf: found.pdf ? path.basename(found.pdf) : null,
    scored: found.scored,
    uploaded: reported?.uploaded ?? null,
    pasted: reported?.pasted ?? null,
    available: Boolean(found.pdf),
  };
}

export function coverLetterPdf(rec: AppRec): string | null {
  return findCoverLetter(rec)?.pdf ?? null;
}

export interface ApplyPlan {
  outDir: string;
  url: string;
  cvLocal: string;
  coverLocal: string | null;
  coverTxt: string | null;
  /** No letter yet: the run generates one with the cover-letter skill first. */
  coverGenerate: { dir: string; jdFile: string | null } | null;
  mcpConfig: string;
  pluginRoot: string | null;
  startedAt: string;
  /** The application's CV markdown (projects to prefer in generated answers). */
  cvMd: string;
}

/** Fresh output folder: copies the PDFs in (the MCP may upload only from its workspace root = cwd),
 *  remembers the URL, writes the MCP config and clears the previous attempt's outputs. */
export function prepareApply(rec: AppRec, dir: string, url: string): ApplyPlan {
  const outDir = applyDir(rec.jd);
  mkdirSync(outDir, { recursive: true });
  for (const f of readdirSync(outDir)) {
    if (SHOT_RE.test(f) || /^(status|fields|sheet|sheet_row)\.json$|^summary\.md$|^(page|console)-.*\.(yml|log)$/.test(f)) rmSync(path.join(outDir, f), { force: true });
  }
  writeFileSync(path.join(outDir, "url.txt"), `${url}\n`);
  const cv = findPdf(dir);
  if (!cv) throw new Error("No CV PDF in this application folder");
  const cvLocal = path.join(outDir, path.basename(cv));
  copyFileSync(cv, cvLocal);
  for (const f of readdirSync(outDir)) if (/^CoverLetter_.*\.pdf$|^letter\.txt$/.test(f)) rmSync(path.join(outDir, f), { force: true });
  const cover = findCoverLetter(rec);
  const coverLocal = cover?.pdf ? path.join(outDir, path.basename(cover.pdf)) : null;
  if (cover?.pdf && coverLocal) copyFileSync(cover.pdf, coverLocal);
  const coverTxt = cover?.txt ? path.join(outDir, "letter.txt") : null;
  if (cover?.txt && coverTxt) copyFileSync(cover.txt, coverTxt);
  let coverGenerate: ApplyPlan["coverGenerate"] = null;
  if (!cover) {
    const parent = path.dirname(dir);
    const jdFile = [path.join(dir, "jd.md"), path.join(parent, "jd.md"), path.join(dir, "01_jd.md"), path.join(parent, "01_jd.md")].find((f) => existsSync(f)) ?? null;
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    const role = (rec.title || "Role").replace(/\(.*?\)/g, "").trim().replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "");
    const co = (rec.jd || rec.company).replace(/[^A-Za-z0-9]+/g, "");
    coverGenerate = { dir: path.join(COVER_LETTERS_DIR, `${stamp}_${co}_${role}`), jdFile };
  }
  const mcpConfig = path.join(outDir, "mcp.json");
  const args = ["-y", PLAYWRIGHT_MCP, "--cdp-endpoint", CDP_ENDPOINT, "--output-dir", outDir];
  if (existsSync(GUARD_JS)) args.push("--init-script", GUARD_JS);
  writeFileSync(mcpConfig, JSON.stringify({ mcpServers: { playwright: { command: "npx", args } } }, null, 2));
  return { outDir, url, cvLocal, coverLocal, coverTxt, coverGenerate, mcpConfig, pluginRoot: pluginRoot(), startedAt: new Date().toISOString(), cvMd: path.join(dir, "03_cv.md") };
}

export function applyPrompt(rec: AppRec, plan: ApplyPlan): string {
  const co = (rec.jd || rec.company).replace(/[^A-Za-z0-9]+/g, "");
  const coverUse = (pdf: string, txt: string) =>
    `- Cover letter: if the form has a cover-letter / motivation-letter FILE field, upload ${pdf} (browser_click on its upload button/label, then browser_file_upload). If it has a cover-letter / motivation TEXT area, paste the full text of ${txt}. If it has both, upload the file and leave the text area empty unless it is required. If it has neither, skip. In fields.json give the cover-letter row source "answers" and in note say "cover letter: <existing|generated now>".`;
  let cover: string;
  if (plan.coverLocal || plan.coverTxt) {
    cover =
      coverUse(plan.coverLocal ?? "(no PDF: use the text area only)", plan.coverTxt ?? "(no letter.txt: upload the PDF only)") +
      `\n- In status.json set "coverLetter": {"source": "existing", "uploaded": true|false, "pasted": true|false}.`;
  } else if (plan.coverGenerate) {
    const g = plan.coverGenerate;
    cover = `- COVER LETTER — GENERATE FIRST (before opening the form): no cover letter exists for ${rec.company}. Invoke the cover-letter skill (Skill "cover-letter"; read ${path.join(COVER_SKILL_DIR, "SKILL.md")}) for this job and follow it: $D = ${g.dir} (write ONLY inside that folder; never into cv/applications/ or the CV library). Input JD: ${g.jdFile ?? "the job URL above"} (read-only); the application's CV for the experience slot: ${plan.cvMd}. Steps 0-5: jd.md, slots.json, build + check with \`bash ${path.join(COVER_SKILL_DIR, "scripts/build_letter.sh")} build|check …\` (output CoverLetter_AmoHsu_${co}.docx/.pdf), score with Agent(subagent_type "codex:codex-rescue", prompt starting --fresh, review-only) into review.md, at most one revision, summary.md.
  If the Codex agent cannot run here, skip the score, write review.md NOT at all, and record "scored": false.
  Then write the letter's plain text (same wording as the PDF) to ${path.join(g.dir, "letter.txt")} and copy the PDF and letter.txt into the current directory: \`cp ${g.dir}/CoverLetter_AmoHsu_${co}.pdf ${g.dir}/letter.txt ${plan.outDir}/\`.
  If the build fails, continue the application WITHOUT a cover letter and record the reason.
` + coverUse(path.join(plan.outDir, `CoverLetter_AmoHsu_${co}.pdf`), path.join(plan.outDir, "letter.txt")) +
      `\n- In status.json set "coverLetter": {"source": "generated" | "none", "dir": "${g.dir}", "scored": true|false, "verdict": "<READY|REVISE|null>", "uploaded": true|false, "pasted": true|false, "error": "<if none>"}.`;
  } else {
    cover = "- There is no cover letter for this job: leave any cover-letter upload empty (fields.json source blank).";
  }
  return `/job-apply:job-apply ${plan.url}

Operator instructions for this unattended dashboard run (they override the skill wherever they conflict; no human can answer questions mid-run — the candidate reviews your result later, possibly from a phone):

JOB
- ${rec.company} · ${rec.title} (dashboard application ${rec.jd}). Job URL: ${plan.url}. If this is an aggregator page, follow its Apply link to the employer's own application form (same browser).

BROWSER
- Claude in Chrome is not available. Use the mcp__playwright__* tools as the host-managed visible browser for the whole flow (load them with ToolSearch first). They drive a persistent Chromium profile on the candidate's PC that stays open after you finish; a separate, human-confirmed step submits later from the same tab. Reuse an open tab for this job if there is one; never close the browser or the tab, and do not open the form in more than one tab.
- A guard script in the page asks for confirmation (a native dialog) before any final Submit/Send action. Never try to get past it.

PROFILE
- Resume: ${plan.cvLocal}. If profile-get is empty, extract the profile from this PDF per the skill. The operator pre-approves the extraction: print it as a JSON block under "INFERRED PROFILE", save it with profile-replace from a temp file in the current directory, then delete the temp file.
- If the profile already has data, use it; update only resumePath to this PDF if it differs.
- Answer memory: the candidate confirmed the stored answers with consent to FILL and REMEMBER them, including the sensitive ones (gender, disability, salary rule). For every question call answer-find with its exact label and fill any matching record (state confirmed or sensitive) without asking (fields.json source "answers"). Do not answer-put anything.

FILLING
- Fill profile-backed fields (name, email, phone, LinkedIn, GitHub, location, current employer/title, education) and upload the resume to the CV/resume control; confirm the filename the page shows.
- Uploads: click the visible upload button/label, then browser_file_upload with the absolute path. If no file chooser opens, try clicking the <label> or the visible button text of that control (browser_find helps locate it); for a visible drag-and-drop zone use browser_drop with the zone as target and the path in "paths". browser_run_code_unsafe and browser_evaluate are not available. If neither works, leave the upload blank.
${cover}
- Standing answers (also in answer memory; source "answers"), for every form:
  - How did you hear about this job → "LinkedIn"; if that is not an option: "Job board", else "Online".
  - Disability / severe-disability disclosure → "No".
  - Gender → "Male".
  - Data-protection / privacy consent checkbox → tick it.
  - Certificates / additional-documents upload → leave empty.
  - Desired salary: a range or dropdown → pick the middle option; a free number field → enter 0. Never invent a figure.
- Free-text questions about the candidate's work (e.g. "Describe one AI/LLM application you personally took to production and your technical responsibility", "Share a GitHub/portfolio/project that demonstrates your work"): WRITE an answer in English, at most 120 words each, and enter it (fields.json source "generated").
  Truth rules, same as the CV: facts only from ${path.join(REPO_ROOT, "cv/profile/library_seed.json")} and ${path.join(REPO_ROOT, "cv/profile/candidate_confirmations.md")} (Read them), or from \`python3 ${LIBRARY_DB} cards\`, \`… card <CODE>\`, \`… bullet <CODE>\`. No metric that is not in those files; MaterialCheck (card MC) is a proof of concept, never "production"; no tools or employers that are not there. Prefer the projects already on this application's CV: ${plan.cvMd}. For links use https://github.com/steven91007 (and the LinkedIn URL from the profile); if the work is confidential, say so in one sentence and describe the project.
- Any other custom question (start date, notice period, work authorization, visa/relocation, ...) stays EMPTY (source "blank"). If you are confident about a harmless factual answer derived from the CV (e.g. years of experience), you may enter it with source "inferred".
- A cookie banner may be dismissed only with its reject / necessary-only choice.
- Advance only with clearly non-final Next / Continue / Review / Save buttons. After filling each step and before advancing, take a full-page screenshot named step-01.png, step-02.png, ... (one per step; a single-page form has just step-01.png).

BLOCKERS — do not bypass
- Login / sign-in / account creation required → stop, screenshot step-NN.png, state "blocked_login".
- CAPTCHA, Cloudflare/Turnstile challenge or bot check that stops you before or inside the form → stop, screenshot, state "blocked_captcha". Never solve or click it. (A Turnstile widget that only runs when the form is submitted is NOT a blocker: note it in status.json "notes".)
- Verification code / MFA → stop, screenshot, state "blocked_mfa".
- Anything else that prevents filling → state "failed" with the reason.

HARD STOP
- NEVER click Submit, Send, Apply (final), "Bewerbung abschicken" or any equivalent final action, and never press Enter in a field that could submit the form. This run must end with the form filled and NOT submitted.

OUTPUT (write into the current directory, ${plan.outDir})
1. review.png: full-page screenshot of the final review page / the filled form with the final button visible.
2. fields.json: a JSON array, one object per form field in page order: {"label": "<exact visible label>", "value": "<entered value (full text for generated answers), filename for uploads, or empty>", "source": "profile" | "answers" | "generated" | "inferred" | "blank", "required": true|false, "step": <n>, "note": "<options for choice fields, or why blank>"}.
3. summary.md: job URL, form type (ATS/host), reached review page yes/no, the field table, uploaded filenames as the page shows them, blockers, and "NOT SUBMITTED - awaiting the candidate's Confirm & submit".
4. status.json: {"state": "filled_awaiting_confirm" | "blocked_login" | "blocked_captcha" | "blocked_mfa" | "failed", "reachedReview": true|false, "submitted": false, "url": "<URL of the tab that holds the filled form>", "formType": "<ATS or host>", "submitControl": {"text": "<exact visible text of the final submit button, e.g. Bewerbung abschicken>"} | null, "filledCount": <n>, "pendingCount": <n blank fields>, "requiredBlank": <n required fields left blank>, "blocker": "<what and where>" | null, "notes": "<anything the candidate should know>" | null, "startedAt": "${plan.startedAt}", "finishedAt": "<ISO time>"}. Use "filled_awaiting_confirm" only when the profile fields and resume are in the form and the final button is on the page.
Finish with the same field summary as your final message.`;
}

export function applyAllowedTools(root: string | null): string[] {
  const store = root ? path.join(root, "scripts", "job-apply-store.py") : "*/job-apply-store.py";
  const tools = [
    "Skill",
    "Read",
    "Write",
    "Glob",
    "Grep",
    "ToolSearch",
    `Bash(python3 ${store} *)`,
    `Bash(python3 "${store}" *)`,
    "Bash(pwd)",
    "Bash(ls *)",
    "Bash(date *)",
    `Bash(python3 ${LIBRARY_DB} cards)`,
    `Bash(python3 ${LIBRARY_DB} card *)`,
    `Bash(python3 ${LIBRARY_DB} bullet *)`,
    // cover-letter generation (only when the application has no letter yet)
    `Bash(bash ${path.join(COVER_SKILL_DIR, "scripts/build_letter.sh")} *)`,
    `Bash(cp ${COVER_LETTERS_DIR}/*)`,
    `Bash(mkdir -p ${COVER_LETTERS_DIR}/*)`,
    `Write(${COVER_LETTERS_DIR}/**)`,
    `Edit(${COVER_LETTERS_DIR}/**)`,
    "Agent",
    `Bash(node ${path.join(os.homedir(), ".claude/plugins/cache/openai-codex")}/*)`,
    `Bash(node "${path.join(os.homedir(), ".claude/plugins/cache/openai-codex")}/*)`,
    "Bash(rm ./.profile_tmp.json)",
    "Bash(rm .profile_tmp.json)",
    "mcp__playwright__*",
  ];
  if (root) tools.push(`Read(${root}/**)`, `Grep(${root}/**)`);
  return tools;
}

export function applyArgv(prompt: string, plan: ApplyPlan): string[] {
  return [
    "-p",
    prompt,
    "--output-format",
    "stream-json",
    "--verbose",
    "--permission-mode",
    "acceptEdits",
    "--mcp-config",
    plan.mcpConfig,
    "--strict-mcp-config",
    `--allowedTools=${applyAllowedTools(plan.pluginRoot).join(",")}`,
    `--disallowedTools=${APPLY_DENIED_TOOLS.join(",")}`,
  ];
}

function browserEnv(): NodeJS.ProcessEnv {
  return { ...process.env, APPLY_BROWSER_DIR: APPLY_PROFILE_DIR, APPLY_CDP_PORT: String(APPLY_CDP_PORT) };
}

/** Start the visible browser on the persistent profile if it is not up. APPLY_BROWSER_CMD overrides. */
export function ensureApplyBrowser(): { ok: boolean; message: string } {
  const cmd = process.env.APPLY_BROWSER_CMD;
  const opts = { encoding: "utf-8" as const, timeout: 30_000, env: browserEnv() };
  const r = cmd ? spawnSync("sh", ["-c", cmd], opts) : spawnSync("bash", [BROWSER_SH], opts);
  return { ok: r.status === 0, message: `${r.stdout ?? ""}${r.stderr ?? ""}`.trim() || (r.error?.message ?? "") };
}

export function writeStatus(file: string, st: ApplyStatusFile): void {
  writeFileSync(`${file}.tmp`, JSON.stringify(st, null, 2));
  renameSync(`${file}.tmp`, file);
}

/** The tab that holds the filled form, so the submit step reattaches to exactly that page. */
async function findTab(url: string): Promise<string | null> {
  try {
    const res = await fetch(`${CDP_ENDPOINT}/json/list`, { signal: AbortSignal.timeout(3000) });
    const tabs = (await res.json()) as { id: string; type: string; url: string }[];
    const norm = (u: string) => {
      try {
        const x = new URL(u);
        return x.origin + x.pathname.replace(/\/$/, "");
      } catch {
        return u;
      }
    };
    return tabs.find((t) => t.type === "page" && norm(t.url) === norm(url))?.id ?? null;
  } catch {
    return null;
  }
}

/** After the fill run: normalise status.json (a run that wrote none, or a wrong state, is "failed";
 *  "submitted" is never accepted from the fill run) and record the CDP endpoint + tab for submit. */
export async function finishApply(_rec: AppRec, plan: ApplyPlan, run: ClaudeRun): Promise<void> {
  const file = path.join(plan.outDir, "status.json");
  const raw = readJson<Partial<ApplyStatusFile>>(file);
  const st: ApplyStatusFile = {
    state: "failed",
    reachedReview: false,
    submitted: false,
    url: plan.url,
    pendingCount: null,
    blocker: null,
    startedAt: plan.startedAt,
    finishedAt: new Date().toISOString(),
    ...(raw ?? {}),
  } as ApplyStatusFile;
  if (!raw) st.blocker = `run ${run.status} without writing status.json`;
  else if (!FILL_STATES.includes(st.state)) {
    st.blocker = `fill run reported state "${String(raw.state)}"; treated as failed`;
    st.state = "failed";
  }
  st.submitted = false;
  if (st.state === "filled_awaiting_confirm" && !st.submitControl?.text) {
    st.state = "failed";
    st.blocker = "the fill run did not record the final submit button (submitControl.text)";
  }
  st.cdpEndpoint = CDP_ENDPOINT;
  st.targetId = await findTab(st.url);
  st.runId = run.id;
  writeStatus(file, st);
}

export interface FieldEdit {
  label: string;
  value: string;
}

/** Edits from the Apply status panel: only labels of text-like rows in fields.json, bounded sizes.
 *  Saved to edits.json (read by apply-submit.mjs) and reflected in fields.json (edited: true). */
export function saveEdits(jd: string, raw: unknown): FieldEdit[] {
  const out = applyDir(jd);
  rmSync(path.join(out, "edits.json"), { force: true });
  if (raw === undefined || raw === null) return [];
  if (!Array.isArray(raw) || raw.length > 30) throw new Error("edits must be an array of at most 30 { label, value }");
  const fields = readFields(out) ?? [];
  const edits: FieldEdit[] = raw.map((e) => {
    const label = typeof e?.label === "string" ? e.label : "";
    const value = typeof e?.value === "string" ? e.value : null;
    if (value === null || value.length > 4000) throw new Error(`edit for "${label}" needs a string value of at most 4000 chars`);
    if (!fields.some((f) => f.label === label)) throw new Error(`no field "${label}" in fields.json`);
    return { label, value };
  });
  if (!edits.length) return [];
  writeFileSync(path.join(out, "edits.json"), JSON.stringify(edits, null, 2));
  const all = readJson<Record<string, unknown>[]>(path.join(out, "fields.json"));
  if (Array.isArray(all)) {
    for (const e of edits) for (const f of all) if (f && (f.label ?? f.field) === e.label) Object.assign(f, { value: e.value, edited: true });
    writeFileSync(path.join(out, "fields.json"), JSON.stringify(all, null, 2));
  }
  return edits;
}

/** POST …/apply/submit: the only way an application gets submitted. Resolves when the click is done. */
export function startSubmit(rec: AppRec): Promise<ApplyStatusFile> {
  const out = applyDir(rec.jd);
  const file = path.join(out, "status.json");
  const st = readJson<ApplyStatusFile>(file);
  if (!st || st.state !== "filled_awaiting_confirm") throw new Error(`Nothing to submit: state is ${st?.state ?? "none"}`);
  if (submitting.has(rec.jd)) throw new Error("Submit already in progress");
  submitting.add(rec.jd);
  writeStatus(file, { ...st, state: "submitting", submitRequestedAt: new Date().toISOString() });
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [SUBMIT_MJS, out], { env: browserEnv(), stdio: ["ignore", "pipe", "pipe"] });
    let log = "";
    child.stdout.on("data", (c) => (log += c));
    child.stderr.on("data", (c) => (log += c));
    const timer = setTimeout(() => child.kill("SIGKILL"), SUBMIT_TIMEOUT_MS);
    child.on("close", async (code) => {
      clearTimeout(timer);
      let now = readJson<ApplyStatusFile>(file) ?? st;
      if (now.state === "submitting") {
        now = { ...now, state: "submit_failed", submitted: false, submitError: `submit script exited ${code}: ${log.trim().slice(-300)}` };
        writeStatus(file, now);
      }
      if (now.state === "submitted") await pushSubmittedToSheet(rec, out, now);
      submitting.delete(rec.jd);
      resolve(now);
    });
  });
}

/** After a submit (Confirm & submit or the Live browser view): set the job's Sheet row to 履歷送出
 *  (push_to_sheet.py --set-status; a missing row is appended) and record the result in sheet.json. */
export async function pushSubmittedToSheet(rec: AppRec, out: string, st: ApplyStatusFile): Promise<void> {
  const row = {
    url: jobUrl(rec.jd, out).url ?? st.url,
    company: rec.company,
    title: rec.title,
    country: rec.country || undefined,
    added: new Date().toISOString().slice(0, 10),
    status: SHEET_STATUS,
    category: SHEET_NOTE,
    note: "CV dashboard Auto apply",
  };
  const rowsFile = path.join(out, "sheet_row.json");
  writeFileSync(rowsFile, JSON.stringify([row], null, 2));
  const result = await runPush(rowsFile);
  writeFileSync(path.join(out, "sheet.json"), JSON.stringify({ ...result, row, at: new Date().toISOString() }, null, 2));
}

/** Submit in progress through Confirm & submit (the Live view leaves those alone). */
export function isSubmitting(jd: string): boolean {
  return submitting.has(jd);
}

function runPush(rowsFile: string): Promise<{ ok: boolean; updated: number | null; added: number | null; skipped: number | null; output: string }> {
  return new Promise((resolve) => {
    // push_to_sheet.py refuses the real Sheet without --live; same opt-in as the Pipeline page (npm run dev:live)
    const argv = ["-I", SHEET_PUSH, "--set-status", rowsFile, ...(process.env.PIPELINE_SHEET_LIVE === "1" ? ["--live"] : [])];
    const child = spawn(PYTHON, argv, { cwd: REPO_ROOT, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    child.stdout.on("data", (c) => (out += c));
    child.stderr.on("data", (c) => (out += c));
    const timer = setTimeout(() => child.kill("SIGKILL"), 120_000);
    const done = (code: number | null) => {
      clearTimeout(timer);
      // --set-status: the existing row (same URL or company+title) gets the new 狀態; a missing one is appended
      const m = out.match(/updated=(\d+) added=(\d+) skipped\(duplicate\)=(\d+)/);
      resolve({
        ok: code === 0 && !!m && Number(m[1]) + Number(m[2]) > 0,
        updated: m ? Number(m[1]) : null,
        added: m ? Number(m[2]) : null,
        skipped: m ? Number(m[3]) : null,
        output: out.trim().slice(-500),
      });
    };
    child.on("error", (e) => {
      out += String(e);
      done(null);
    });
    child.on("close", done);
  });
}
