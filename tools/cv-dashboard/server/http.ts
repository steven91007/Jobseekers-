// node:http routes for /api/*, plus the built frontend in production.
import { createReadStream, existsSync, statSync } from "node:fs";
import http, { type IncomingMessage, type ServerResponse } from "node:http";
import path from "node:path";
import type { ClaudeTarget, TargetKind } from "../shared/types.js";
import { appDir, applicationDetail, getApplicationRec, listApplications } from "./application.js";
import { findPdf } from "./artifacts.js";
import { ClaudeRunner } from "./claude.js";
import { DIST_DIR } from "./config.js";
import { pickSuggestion } from "./libcli.js";
import {
  APPLY_TIMEOUT_MS,
  applyArgv,
  applyInfo,
  applyPrompt,
  ensureApplyBrowser,
  finishApply,
  jobUrl,
  prepareApply,
  screenshotPath,
  coverLetterPdf,
  saveEdits,
  startSubmit,
  validJobUrl,
} from "./apply.js";
import { ANSWERS_TIMEOUT_MS, answersArgv, answersInfo, answersPrompt, saveQuestions, validQuestions } from "./answers.js";
import { alignFor, gapsFor, sotLeadsFor, invalidateConceptCache } from "./concepts.js";
import { LiveError, liveDialog, liveInput, liveStream, liveTabs } from "./live.js";
import { MockError, answerMock, finishMock, mockApps, reportMock, retryMock, sessionView, skipMock, startMock, validId } from "./mock.js";
import { PipelineError, addInterest, dailyList, interestList, referralSent, setDecision, setNote, submissionsList } from "./pipeline.js";

const TARGET_KINDS: TargetKind[] = [
  "name", "headline", "contact", "section", "summary", "skill_row", "role",
  "scope", "card", "bullet", "other_bullet", "other", "term",
];

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".json": "application/json",
};

class HttpError extends Error {
  constructor(public status: number, message: string, public extra: Record<string, unknown> = {}) {
    super(message);
  }
}

function json(res: ServerResponse, status: number, body: unknown): void {
  const data = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  res.end(data);
}

function sse(res: ServerResponse): void {
  res.writeHead(200, {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-store",
    connection: "keep-alive",
  });
}

// Browser origins allowed to start or cancel runs. Any site open in the same browser could otherwise
// POST a CORS-simple text/plain body here and start a paid Claude run. Extra origins: CV_DASHBOARD_ORIGINS
// (comma-separated); any https://*.ts.net origin is accepted for the Tailscale-served page.
const ALLOWED_ORIGINS = new Set([
  "http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174",
  ...(process.env.CV_DASHBOARD_ORIGINS ?? "").split(",").map((s) => s.trim()).filter(Boolean),
]);

function originAllowed(origin: string): boolean {
  if (ALLOWED_ORIGINS.has(origin)) return true;
  try {
    const u = new URL(origin);
    return u.protocol === "https:" && u.hostname.endsWith(".ts.net");
  } catch {
    return false;
  }
}

/** Reject cross-site state changes: unknown Origin, a cross-site fetch, or a non-JSON body. */
function guardMutation(req: IncomingMessage): void {
  const origin = req.headers.origin;
  if (origin !== undefined && !originAllowed(origin)) throw new HttpError(403, `Origin not allowed: ${origin}`);
  if (origin === undefined && req.headers["sec-fetch-site"] === "cross-site") throw new HttpError(403, "Cross-site request refused");
  if (req.method === "POST") {
    const ct = String(req.headers["content-type"] ?? "").split(";")[0].trim().toLowerCase();
    if (ct !== "application/json") throw new HttpError(415, "Content-Type must be application/json");
  }
}

/** Live browser routes show and drive the apply browser: only for the local or Tailscale host name
 *  (blocks DNS rebinding onto 127.0.0.1:5174), and a cross-site Origin is refused even on GET. */
function guardLive(req: IncomingMessage): void {
  const host = String(req.headers.host ?? "").replace(/:\d+$/, "").toLowerCase();
  if (!(host === "127.0.0.1" || host === "localhost" || host === "[::1]" || host.endsWith(".ts.net"))) throw new HttpError(403, `Host not allowed: ${host}`);
  const origin = req.headers.origin;
  if (origin !== undefined && !originAllowed(origin)) throw new HttpError(403, `Origin not allowed: ${origin}`);
  if (req.headers["sec-fetch-site"] === "cross-site") throw new HttpError(403, "Cross-site request refused");
}

async function readBody(req: IncomingMessage): Promise<unknown> {
  let size = 0;
  const chunks: Buffer[] = [];
  for await (const c of req) {
    size += (c as Buffer).length;
    if (size > 64 * 1024) throw new HttpError(413, "Body too large");
    chunks.push(c as Buffer);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf-8") || "{}");
  } catch {
    throw new HttpError(400, "Body must be JSON");
  }
}

function requireApp(jd: string) {
  const rec = getApplicationRec(jd);
  if (!rec) throw new HttpError(404, `Unknown application: ${jd}`);
  return rec;
}

function parseTarget(raw: unknown): ClaudeTarget {
  const t = (raw ?? {}) as Record<string, unknown>;
  if (typeof t.kind !== "string" || !TARGET_KINDS.includes(t.kind as TargetKind)) throw new HttpError(400, "target.kind is invalid");
  const str = (v: unknown, max: number) => (typeof v === "string" && v.trim() ? v.trim().slice(0, max) : undefined);
  return { kind: t.kind as TargetKind, code: str(t.code, 120), text: str(t.text, 2000), term: str(t.term, 200) };
}

function serveStatic(req: IncomingMessage, res: ServerResponse, pathname: string): boolean {
  if (!existsSync(DIST_DIR)) return false;
  let file = path.normalize(path.join(DIST_DIR, decodeURIComponent(pathname)));
  if (!file.startsWith(DIST_DIR)) return false;
  if (!existsSync(file) || statSync(file).isDirectory()) file = path.join(DIST_DIR, "index.html");
  if (!existsSync(file)) return false;
  res.writeHead(200, { "content-type": MIME[path.extname(file)] ?? "application/octet-stream" });
  if (req.method === "HEAD") res.end();
  else createReadStream(file).pipe(res);
  return true;
}

export function createApp(runner = new ClaudeRunner()) {
  // A finished run may have changed the library: align/gaps are recomputed on the next request.
  runner.on("board-changed", () => invalidateConceptCache());
  async function route(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", "http://localhost");
    const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
    const method = req.method ?? "GET";

    if (parts[0] !== "api") {
      if ((method === "GET" || method === "HEAD") && serveStatic(req, res, url.pathname)) return;
      throw new HttpError(404, "Not found");
    }
    if (method !== "GET" && method !== "HEAD") guardMutation(req);

    // /api/apply/live — see and control the apply browser (frames in memory only)
    if (parts[1] === "apply" && parts[2] === "live") {
      guardLive(req);
      try {
        if (parts[3] === "tabs" && parts.length === 4 && method === "GET") return json(res, 200, await liveTabs());
        if (parts.length === 5 && parts[4] === "stream" && method === "GET") return await liveStream(parts[3], req, res);
        if (parts.length === 5 && parts[4] === "input" && method === "POST") return json(res, 200, await liveInput(parts[3], await readBody(req)));
        if (parts.length === 5 && parts[4] === "dialog" && method === "POST") return json(res, 200, await liveDialog(parts[3], await readBody(req)));
      } catch (e) {
        if (e instanceof LiveError) throw new HttpError(e.status, e.message);
        throw e;
      }
      throw new HttpError(404, "Not found");
    }

    // /api/pipeline — Google Sheet tabs 每日職缺 / 興趣職缺 / 投遞表單 via push_to_sheet.py (Google Sheets API)
    if (parts[1] === "pipeline") {
      try {
        if (parts.length === 3 && method === "GET") {
          if (parts[2] === "daily") return json(res, 200, await dailyList());
          if (parts[2] === "interest") return json(res, 200, await interestList());
          if (parts[2] === "submissions") return json(res, 200, await submissionsList());
        }
        if (method === "POST") {
          if (parts[2] === "daily" && parts[3] === "decision" && parts.length === 4) return json(res, 200, await setDecision(await readBody(req)));
          if (parts[2] === "daily" && parts[3] === "note" && parts.length === 4) return json(res, 200, await setNote(await readBody(req)));
          if (parts[2] === "interest" && parts.length === 3) return json(res, 200, await addInterest(await readBody(req)));
          if (parts[2] === "referral-sent" && parts.length === 3) return json(res, 200, await referralSent(await readBody(req)));
        }
      } catch (e) {
        if (e instanceof PipelineError) throw new HttpError(e.status, e.message);
        throw e;
      }
      throw new HttpError(404, "Not found");
    }

    // /api/mock — 模擬面試: prepared questions, typed/spoken answers, follow-ups, rubric scores (interview-prep mock.py)
    if (parts[1] === "mock") {
      try {
        if (parts.length === 2 && method === "GET") return json(res, 200, mockApps());
        const rec = requireApp(parts[2] ?? "");
        if (parts.length === 4 && parts[3] === "start" && method === "POST") return json(res, 200, await startMock(rec, (await readBody(req)) as Record<string, unknown>));
        const id = validId(parts[3] ?? "");
        if (parts.length === 4 && method === "GET") return json(res, 200, sessionView(rec, id));
        if (parts.length === 5 && method === "POST") {
          const body = (await readBody(req)) as Record<string, unknown>;
          if (parts[4] === "answer") return json(res, 200, await answerMock(rec, id, body));
          if (parts[4] === "finish") return json(res, 200, await finishMock(rec, id, body));
          if (parts[4] === "skip") return json(res, 200, await skipMock(rec, id, body));
          if (parts[4] === "retry") return json(res, 200, await retryMock(rec, id, body));
          if (parts[4] === "report") return json(res, 200, await reportMock(rec, id));
        }
      } catch (e) {
        if (e instanceof MockError) throw new HttpError(e.status, e.message);
        throw e;
      }
      throw new HttpError(404, "Not found");
    }

    // /api/applications
    if (parts[1] === "applications") {
      if (parts.length === 2 && method === "GET") return json(res, 200, listApplications());
      const rec = requireApp(parts[2]);
      if (parts.length === 3 && method === "GET") return json(res, 200, applicationDetail(rec));
      if (parts[3] === "pick" && method === "GET") return json(res, 200, await pickSuggestion(rec.jd));
      if (parts[3] === "align" && method === "GET") return json(res, 200, await alignFor(rec.jd));
      if (parts[3] === "sot-leads" && method === "GET") return json(res, 200, await sotLeadsFor(rec.jd));
      if (parts[3] === "gaps" && method === "GET") return json(res, 200, await gapsFor(rec.jd, appDir(rec)));
      if (parts[3] === "answers" && parts.length === 4) {
        // 表單問答: paste the form's questions → paste-ready answers (never fills or submits a form)
        const dir = appDir(rec);
        const busyRun = () => runner.list().find((r) => r.jd === rec.jd && r.mode === "answers" && (r.status === "running" || r.status === "queued"));
        if (method === "GET") {
          const busy = busyRun();
          return json(res, 200, { ...answersInfo(rec, dir), runId: busy?.id ?? null, runStatus: busy?.status ?? null });
        }
        if (method === "POST") {
          const body = (await readBody(req)) as Record<string, unknown>;
          let questions: string;
          try {
            questions = validQuestions(body.questions);
          } catch (e) {
            throw new HttpError(400, e instanceof Error ? e.message : String(e));
          }
          const busy = busyRun();
          if (busy) throw new HttpError(409, "這份申請的表單問答已在排隊或執行中", { runId: busy.id });
          saveQuestions(dir, questions);
          const run = runner.start({
            jd: rec.jd,
            appDir: dir,
            target: { kind: "other", text: "表單問答 (form-answers)" },
            instruction: `表單問答:回答 ${questions.split("\n").filter((l) => l.trim()).length} 行問題`,
            custom: {
              mode: "answers",
              cwd: dir,
              timeoutMs: ANSWERS_TIMEOUT_MS,
              prompt: () => answersPrompt(rec, dir),
              argv: (prompt) => answersArgv(prompt),
            },
          });
          return json(res, 202, { runId: run.id, position: runner.position(run.id) ?? 0, status: run.status });
        }
      }
      if (parts[3] === "apply") {
        const dir = appDir(rec);
        if (parts.length === 4 && method === "GET") {
          const busy = runner.list().find((r) => r.jd === rec.jd && r.mode === "apply" && (r.status === "running" || r.status === "queued"));
          return json(res, 200, { ...applyInfo(rec, dir), runId: busy?.id ?? null, runStatus: busy?.status ?? null });
        }
        if (parts.length === 4 && method === "POST") {
          const body = (await readBody(req)) as Record<string, unknown>;
          const url = body.url === undefined || body.url === "" ? jobUrl(rec.jd, dir).url : validJobUrl(body.url);
          if (!url) throw new HttpError(400, "A valid http(s) job URL is required");
          const busy = runner.list().find((r) => r.jd === rec.jd && r.mode === "apply" && (r.status === "running" || r.status === "queued"));
          if (busy) throw new HttpError(409, "An Apply run for this application is already queued or running", { runId: busy.id });
          let plan: ReturnType<typeof prepareApply>;
          try {
            plan = prepareApply(rec, dir, url);
          } catch (e) {
            throw new HttpError(400, e instanceof Error ? e.message : String(e));
          }
          const run = runner.start({
            jd: rec.jd,
            appDir: dir,
            target: { kind: "other", text: "Auto apply (job-apply: fill form, stop at review)" },
            instruction: `Auto apply: fill the form at ${url} and stop at the review page (never submit)`,
            custom: {
              mode: "apply",
              cwd: plan.outDir,
              timeoutMs: APPLY_TIMEOUT_MS,
              beforeLaunch: () => {
                const b = ensureApplyBrowser();
                return b.ok ? `browser: ${b.message}` : `browser start failed: ${b.message}`;
              },
              prompt: () => applyPrompt(rec, plan),
              argv: (prompt) => applyArgv(prompt, plan),
              afterExit: (r) => finishApply(rec, plan, r),
            },
          });
          return json(res, 202, { runId: run.id, position: runner.position(run.id) ?? 0, status: run.status, url });
        }
        if (parts[4] === "submit" && parts.length === 5 && method === "POST") {
          // The only AUTOMATED submit: explicit button + two confirmations in the UI (the Live browser view
          // submits only when the candidate taps the button and accepts the guard dialog themselves),
          // and the body must repeat the confirmation and the URL shown to the candidate.
          const body = (await readBody(req)) as Record<string, unknown>;
          if (body.confirm !== "SUBMIT") throw new HttpError(400, 'Body must be { "confirm": "SUBMIT", "url": <form URL> }');
          const info = applyInfo(rec, dir);
          if (!info.canSubmit || !info.status) {
            throw new HttpError(409, `Nothing to submit: state is ${info.submitting ? "submitting" : info.status?.state ?? "none"}`);
          }
          if (body.url !== info.status.url) throw new HttpError(409, "The form URL changed since you reviewed it; reload the page");
          try {
            saveEdits(rec.jd, body.edits);
          } catch (e) {
            throw new HttpError(400, e instanceof Error ? e.message : String(e));
          }
          const done = await startSubmit(rec);
          return json(res, 200, { state: done.state, submitted: done.submitted, error: done.submitError ?? null, confirmationText: done.confirmationText ?? null });
        }
        if (parts[4] === "cover-letter" && parts.length === 5 && method === "GET") {
          const pdf = coverLetterPdf(rec);
          if (!pdf) throw new HttpError(404, "No cover letter PDF for this application");
          res.writeHead(200, {
            "content-type": "application/pdf",
            "content-length": statSync(pdf).size,
            "content-disposition": `inline; filename="${path.basename(pdf)}"`,
            "cache-control": "no-store",
          });
          createReadStream(pdf).pipe(res);
          return;
        }
        if (parts[4] === "screenshot" && (parts.length === 5 || parts.length === 6) && method === "GET") {
          const shot = screenshotPath(rec.jd, parts[5] ?? "review.png");
          if (!shot) throw new HttpError(404, "No screenshot yet");
          res.writeHead(200, { "content-type": "image/png", "content-length": statSync(shot).size, "cache-control": "no-store" });
          createReadStream(shot).pipe(res);
          return;
        }
      }
      if (parts[3] === "pdf" && method === "GET") {
        const pdf = findPdf(appDir(rec));
        if (!pdf) throw new HttpError(404, "No CV PDF in this application folder");
        res.writeHead(200, {
          "content-type": "application/pdf",
          "content-length": statSync(pdf).size,
          "content-disposition": `inline; filename="${path.basename(pdf)}"`,
          "cache-control": "no-store",
        });
        createReadStream(pdf).pipe(res);
        return;
      }
    }

    // /api/claude
    if (parts[1] === "claude") {
      if (parts.length === 2 && method === "POST") {
        const body = (await readBody(req)) as Record<string, unknown>;
        if (typeof body.jd !== "string") throw new HttpError(400, "jd is required");
        const rec = requireApp(body.jd);
        const instruction = typeof body.instruction === "string" ? body.instruction.trim() : "";
        if (!instruction) throw new HttpError(400, "instruction is required");
        if (instruction.length > 4000) throw new HttpError(400, "instruction is too long (max 4000 chars)");
        const run = runner.start({ jd: rec.jd, appDir: appDir(rec), target: parseTarget(body.target), instruction });
        return json(res, 202, { runId: run.id, position: runner.position(run.id) ?? 0, status: run.status });
      }
      if (parts[2] === "runs" && parts.length === 3 && method === "GET") {
        return json(res, 200, { active: runner.activeIds()[0] ?? null, running: runner.activeIds(), queued: runner.queuedIds(), runs: runner.list() });
      }
      if (parts[2] === "board" && parts.length === 3 && method === "GET") return json(res, 200, runner.board());
      if (parts[2] === "events" && parts.length === 3 && method === "GET") {
        sse(res);
        const send = (ev: unknown) => res.write(`data: ${JSON.stringify(ev)}\n\n`);
        send({ type: "board", board: runner.board() });
        const ping = setInterval(() => res.write(": ping\n\n"), 15_000);
        runner.on("board", send);
        runner.on("board-changed", send);
        req.on("close", () => {
          clearInterval(ping);
          runner.off("board", send);
          runner.off("board-changed", send);
        });
        return;
      }
      const run = parts[2] ? runner.get(parts[2]) : undefined;
      if (!run) throw new HttpError(404, "Unknown run");
      if (parts.length === 3 && method === "GET") return json(res, 200, run);
      if (parts.length === 3 && method === "DELETE") {
        if (!runner.cancel(run.id)) throw new HttpError(409, "Run is not queued or running");
        return json(res, 202, { cancelled: run.id });
      }
      if (parts[3] === "stream" && method === "GET") {
        sse(res);
        const send = (ev: unknown) => res.write(`data: ${JSON.stringify(ev)}\n\n`);
        for (const ev of run.events) send(ev);
        if (run.status !== "running" && run.status !== "queued") return void res.end();
        const onEvent = (ev: unknown) => send(ev);
        const onEnd = () => {
          cleanup();
          res.end();
        };
        const ping = setInterval(() => res.write(": ping\n\n"), 15_000);
        const cleanup = () => {
          clearInterval(ping);
          runner.off(`event:${run.id}`, onEvent);
          runner.off(`end:${run.id}`, onEnd);
        };
        runner.on(`event:${run.id}`, onEvent);
        runner.once(`end:${run.id}`, onEnd);
        req.on("close", cleanup);
        return;
      }
    }
    throw new HttpError(404, "Not found");
  }

  const server = http.createServer((req, res) => {
    route(req, res).catch((e: unknown) => {
      const status = e instanceof HttpError ? e.status : 500;
      if (status === 500) console.error(e);
      if (res.headersSent) return void res.end();
      json(res, status, { error: e instanceof Error ? e.message : String(e), ...(e instanceof HttpError ? e.extra : {}) });
    });
  });
  return { server, runner };
}
