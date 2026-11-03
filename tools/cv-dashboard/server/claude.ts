// Headless Claude Code runs (`claude -p … --output-format stream-json`): a FIFO queue, up to
// MAX_CONCURRENT_RUNS at once, never two on the same application, coordinated through a shared run board.
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import { EventEmitter } from "node:events";
import type { BoardRun, ClaudeRun, ClaudeRunSummary, ClaudeTarget, RunBoard, RunEvent, RunMode, RunStatus } from "../shared/types.js";
import { lineChanges } from "../shared/diff.js";
import {
  BOARD_FILE,
  CLAUDE_BIN,
  CLAUDE_TIMEOUT_MS,
  DATA_DIR,
  MAX_CONCURRENT_RUNS,
  PYTHON,
  REPO_ROOT,
  RUNS_FILE,
  SHARED_LOCK_PY,
  SKILL_SCRIPTS,
  relToRepo,
} from "./config.js";
import { appDir, listApplicationRecs } from "./application.js";
import { readText } from "./artifacts.js";
import { notionEvidence } from "./library.js";
import { conceptContext, jdTermTypes } from "./concepts.js";

/** Notion pages for the target; a DB hiccup must not stop the run. */
function evidenceFor(target: ClaudeTarget): string[] {
  try {
    return notionEvidence(target);
  } catch {
    return [];
  }
}

/** "Concept context" block; a DB without the concept tables just leaves it out. */
export function conceptContextFor(target: ClaudeTarget, absAppDir: string): string {
  try {
    return conceptContext(target, jdTermTypes(absAppDir));
  } catch {
    return "";
  }
}

const NOTION = "mcp__plugin_Notion_notion__notion";
export const NOTION_TOOLS = [
  `${NOTION}-search`,
  `${NOTION}-fetch`,
  `${NOTION}-create-pages`,
  `${NOTION}-update-page`,
  `${NOTION}-get-async-task`,
];

export const ALLOWED_TOOLS = [
  "Read",
  "Edit",
  "Write",
  "Glob",
  "Grep",
  `Bash(python3 ${SKILL_SCRIPTS}/*)`,
  `Bash(bash ${SKILL_SCRIPTS}/*)`,
  // Cross-run lock for the seed, CONF file, DB and Notion rows; its `run` only executes tailor-cv scripts.
  `Bash(python3 ${SHARED_LOCK_PY} *)`,
  // Notion MCP (plugin): read the KB for evidence, mirror library rows to "Personal experiences".
  // Without these, headless runs are denied ("you haven't granted it yet") and Notion sync never happens.
  ...NOTION_TOOLS,
];

// Set when the dashboard itself was started from a Claude Code session; auth/provider
// variables (ANTHROPIC_*, CLAUDE_CODE_USE_*) are left alone.
export const PARENT_SESSION_VARS = [
  "CLAUDECODE",
  "CLAUDE_CODE_ENTRYPOINT",
  "CLAUDE_CODE_SESSION_ID",
  "CLAUDE_CODE_CHILD_SESSION",
  "CLAUDE_CODE_SESSION_ATTENDED",
  "CLAUDE_CODE_MESSAGING_SOCKET",
  "CLAUDE_CODE_MESSAGING_TOKEN",
  "CLAUDE_CODE_EXECPATH",
  "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
  "CLAUDE_PID",
  "CLAUDE_EFFORT",
  "CLAUDE_PLUGIN_DATA",
];

/** Another run as the prompt shows it. */
export interface PeerRun {
  jd: string;
  target: ClaudeTarget;
  instruction: string;
  status: RunStatus;
}

export function targetLabel(target: ClaudeTarget): string {
  const what = [target.kind, target.code].filter(Boolean).join(" ");
  return target.term ? `${what} '${target.term}'` : what;
}

/** Coordination block: the other runs, the board and the shared lock. */
export function coordinationPrompt(runId: string, peers: PeerRun[], boardPath: string, maxConcurrent: number): string {
  const lock = `python3 ${SHARED_LOCK_PY}`;
  const list = peers.length
    ? peers.map((p) => `- ${p.jd} (${targetLabel(p.target)}, ${p.status}): ${short(p.instruction, 200)}`).join("\n")
    : "- none right now (others may start while you work)";
  return (
    `\n\nCoordination: you are dashboard run ${runId}; up to ${maxConcurrent} runs work in this repo at the same time ` +
    "(never two on the same application).\nOther runs in progress:\n" +
    `${list}\n` +
    `Read ${boardPath} (every run's status, files changed so far, last log lines) before you edit any shared file and ` +
    "again before you finish. Shared state is cv/profile/library_seed.json, cv/profile/candidate_confirmations.md, the " +
    "SQLite DB (library_db.py init / record / set-*) and the Notion rows. Adapt to other runs instead of overwriting " +
    "them: if another run changed the same bullet or row, build on its result, never revert it. Shared writes go under " +
    `the lock: \`${lock} hold --run ${runId} --seconds 600\`, then re-read the seed / CONF files (another run may have ` +
    "changed them), make the edit, run library_db.py init + selftest (+ export-md, record / set-*, Notion mirror), then " +
    `\`${lock} release --run ${runId}\`. For a single shared command use \`${lock} run --run ${runId} -- python3 ` +
    `${SKILL_SCRIPTS}/library_db.py …\`. \`${lock} status\` shows who holds it. The files of this application ` +
    "(03_cv.md, PDFs, ATS files in its folder) are yours alone during this run; mention in your report any change you " +
    "built on from another run."
  );
}

export function buildPrompt(
  jd: string,
  dir: string,
  target: ClaudeTarget,
  instruction: string,
  evidence: string[] = [],
  concepts = "",
): string {
  const what = [target.kind, target.code].filter(Boolean).join(" ");
  const text = target.text ? ` '${target.text}'` : "";
  const term = target.term ? ` [JD term: '${target.term}']` : "";
  return (
    "You are working in the LinkedinJobSearcher repo with the tailor-cv skill (read " +
    ".claude/skills/tailor-cv/SKILL.md first). " +
    `Application: ${jd} (${dir}). Target: ${what}${text}${term}. ` +
    `Request from the candidate: ${/[.!?。！？]$/.test(instruction) ? instruction : `${instruction}.`} ` +
    "Follow the skill's truth rules (SoT = cv/profile/library_seed.json + candidate_confirmations.md; never invent " +
    "facts). After changing library text: library_db.py init, selftest, export-md; rebuild this " +
    `application (assemble_cv.py --jd ${jd}, build_cv.sh patch + check + ats). ` +
    "Notion: you may read any page for evidence (Notion is evidence only, never a metric source); write only to " +
    "the 'Personal experiences' page and its four databases (Projects, Bullets, Skills, JD keyword map) and the " +
    "application pages under it. After a library change, mirror the changed rows: library_db.py notion-payload " +
    "<table> --only-new for new rows (then set-notion-id), notion-update-page for changed rows that already have a " +
    "notion_id. Load each Notion tool with ToolSearch first. Report what you changed, including Notion pages." +
    (evidence.length ? `\nNotion evidence pages for this target: ${evidence.join(", ")}` : "") +
    concepts
  );
}

export function claudeArgv(prompt: string): string[] {
  return [
    "-p",
    prompt,
    "--output-format",
    "stream-json",
    "--verbose",
    "--permission-mode",
    "acceptEdits",
    // One comma-joined value with `=` so the variadic flag cannot swallow other arguments.
    `--allowedTools=${ALLOWED_TOOLS.join(",")}`,
  ];
}

// ---------------------------------------------------------------- stream-json → events

function short(s: string, n = 240): string {
  const one = s.replace(/\s+/g, " ").trim();
  return one.length > n ? `${one.slice(0, n - 1)}…` : one;
}

function toolSummary(input: Record<string, unknown> | undefined): string {
  if (!input) return "";
  const pick = input.command ?? input.file_path ?? input.pattern ?? input.path ?? input.url ?? input.description;
  if (typeof pick === "string") return short(pick, 200);
  return short(JSON.stringify(input), 200);
}

/** One stream-json line → zero or more dashboard events. */
export function parseStreamLine(line: string, now = Date.now()): RunEvent[] {
  let msg: any;
  try {
    msg = JSON.parse(line);
  } catch {
    return line.trim() ? [{ t: now, type: "text", text: line }] : [];
  }
  const out: RunEvent[] = [];
  if (msg.type === "system" && msg.subtype === "init") {
    out.push({ t: now, type: "system", text: `session ${msg.session_id ?? "?"} · model ${msg.model ?? "?"} · ${msg.permissionMode ?? ""}` });
  } else if (msg.type === "assistant") {
    for (const c of msg.message?.content ?? []) {
      if (c.type === "text" && c.text) out.push({ t: now, type: "text", text: c.text });
      if (c.type === "tool_use") out.push({ t: now, type: "tool", name: c.name, summary: toolSummary(c.input) });
    }
  } else if (msg.type === "user") {
    for (const c of msg.message?.content ?? []) {
      if (c.type !== "tool_result") continue;
      const body = Array.isArray(c.content)
        ? c.content.map((x: any) => (typeof x?.text === "string" ? x.text : "")).join(" ")
        : String(c.content ?? "");
      // a tool the allowlist does not cover comes back as text, not always with is_error
      const denied = /requested permissions to use .* but you haven.t granted it/.test(body);
      out.push({ t: now, type: "tool_result", summary: short(body, 300), isError: Boolean(c.is_error) || denied });
    }
  } else if (msg.type === "result") {
    out.push({
      t: now,
      type: "result",
      text: typeof msg.result === "string" ? msg.result : "",
      isError: Boolean(msg.is_error) || msg.subtype !== "success",
      costUsd: typeof msg.total_cost_usd === "number" ? msg.total_cost_usd : null,
      durationMs: typeof msg.duration_ms === "number" ? msg.duration_ms : null,
      turns: typeof msg.num_turns === "number" ? msg.num_turns : null,
    });
  }
  return out;
}


// ---------------------------------------------------------------- run store

class RunStore {
  runs: ClaudeRun[] = [];
  constructor() {
    if (!existsSync(RUNS_FILE)) return;
    try {
      this.runs = JSON.parse(readFileSync(RUNS_FILE, "utf-8"));
    } catch {
      this.runs = [];
    }
    // A server restart orphans runs that were in flight or waiting.
    for (const r of this.runs) {
      if (r.status === "running") r.status = "failed";
      if (r.status === "queued") {
        r.status = "cancelled";
        r.endedAt ??= new Date().toISOString();
      }
    }
  }
  save(): void {
    mkdirSync(DATA_DIR, { recursive: true });
    const tmp = `${RUNS_FILE}.tmp`;
    writeFileSync(tmp, JSON.stringify(this.runs, null, 2));
    renameSync(tmp, RUNS_FILE);
  }
}

/** A run that is not a tailor-cv edit (Apply): its own prompt, CLI flags, cwd and hooks. */
export interface CustomRun {
  mode: RunMode;
  /** Called when the run leaves the queue. */
  prompt: () => string;
  argv: (prompt: string) => string[];
  cwd: string;
  timeoutMs?: number;
  /** Runs right before spawning (e.g. start the browser); a message is logged as a system event. */
  beforeLaunch?: () => string | void;
  /** Awaited after the process exits, before the run is marked finished. */
  afterExit?: (run: ClaudeRun) => Promise<void> | void;
}

export interface StartRequest {
  jd: string;
  appDir: string;
  target: ClaudeTarget;
  instruction: string;
  custom?: CustomRun;
}

export interface RunnerOptions {
  maxConcurrent?: number;
  /** Finished runs kept on the board. */
  boardHistory?: number;
}

interface Active {
  run: ClaudeRun;
  appDir: string;
  child: ChildProcess;
  timer: NodeJS.Timeout;
  cancelled: boolean;
  timedOut: boolean;
  /** 03_cv.md of every application when this run started (for affectedJds / board-changed). */
  before: Map<string, { file: string; text: string }>;
}

const EDIT_TOOLS = new Set(["Edit", "Write", "MultiEdit", "NotebookEdit"]);

function boardLine(ev: RunEvent): string | null {
  switch (ev.type) {
    case "text":
      return short(ev.text, 200);
    case "tool":
      return short(`› ${ev.name} ${ev.summary}`, 200);
    case "tool_result":
      return ev.isError ? short(`↳ error: ${ev.summary}`, 200) : null;
    case "stderr":
      return short(`stderr: ${ev.text}`, 200);
    case "result":
      return short(`result: ${ev.text}`, 200);
    default:
      return null;
  }
}

function snapshotCvs(): Map<string, { file: string; text: string }> {
  const out = new Map<string, { file: string; text: string }>();
  try {
    for (const rec of listApplicationRecs()) {
      const file = path.join(appDir(rec), "03_cv.md");
      out.set(rec.jd, { file, text: readText(file) ?? "" });
    }
  } catch {
    /* DB unreadable: only the run's own application is compared */
  }
  return out;
}

export class ClaudeRunner extends EventEmitter {
  private store = new RunStore();
  private queue: { run: ClaudeRun; appDir: string; custom?: CustomRun }[] = [];
  private active = new Map<string, Active>();
  private lastLines = new Map<string, string[]>();
  private appDirs = new Map<string, string>();
  private boardTimer: NodeJS.Timeout | null = null;
  readonly maxConcurrent: number;
  private boardHistory: number;

  constructor(opts: RunnerOptions = {}) {
    super();
    this.setMaxListeners(0);
    this.maxConcurrent = Math.max(1, opts.maxConcurrent ?? MAX_CONCURRENT_RUNS);
    this.boardHistory = opts.boardHistory ?? 10;
    this.writeBoard();
  }

  list(): ClaudeRunSummary[] {
    return this.store.runs
      .map(({ events: _e, prompt: _p, argv: _a, ...rest }) => ({ ...rest, position: this.position(rest.id) }))
      .reverse();
  }

  get(id: string): ClaudeRun | undefined {
    return this.store.runs.find((r) => r.id === id);
  }

  /** Ids of the running runs, oldest first. */
  activeIds(): string[] {
    return [...this.active.keys()];
  }

  queuedIds(): string[] {
    return this.queue.map((q) => q.run.id);
  }

  /** 1-based queue position, or null when not queued. */
  position(id: string): number | null {
    const i = this.queue.findIndex((q) => q.run.id === id);
    return i < 0 ? null : i + 1;
  }

  /** Queue a run; it starts at once if a slot is free and its application is idle. */
  start(req: StartRequest): ClaudeRun {
    const run: ClaudeRun = {
      id: randomUUID(),
      jd: req.jd,
      mode: req.custom?.mode ?? "edit",
      target: req.target,
      instruction: req.instruction,
      prompt: "",
      argv: [],
      queuedAt: new Date().toISOString(),
      startedAt: null,
      endedAt: null,
      status: "queued",
      exitCode: null,
      resultText: null,
      costUsd: null,
      events: [],
      cvChanges: [],
      filesChanged: [],
    };
    this.store.runs.push(run);
    this.queue.push({ run, appDir: req.appDir, custom: req.custom });
    this.appDirs.set(run.id, relToRepo(req.appDir));
    this.pump();
    if (run.status === "queued") {
      const pos = this.position(run.id);
      const sameApp = [...this.active.values()].some((a) => a.run.jd === run.jd);
      this.push(run, {
        t: Date.now(),
        type: "system",
        text: `queued at position ${pos}${sameApp ? ` (waits for the other ${run.jd} run)` : ` (${this.active.size}/${this.maxConcurrent} running)`}`,
      });
    }
    this.store.save();
    this.changed(true);
    return run;
  }

  cancel(id: string): boolean {
    const qi = this.queue.findIndex((q) => q.run.id === id);
    if (qi >= 0) {
      const [{ run }] = this.queue.splice(qi, 1);
      run.status = "cancelled";
      run.endedAt = new Date().toISOString();
      this.push(run, { t: Date.now(), type: "exit", code: null, signal: null, status: "cancelled" });
      this.store.save();
      this.emit(`end:${run.id}`);
      this.changed(true);
      return true;
    }
    const a = this.active.get(id);
    if (!a) return false;
    a.cancelled = true;
    this.kill(a.child);
    return true;
  }

  board(): RunBoard {
    const live = this.store.runs.filter((r) => r.status === "running" || r.status === "queued");
    const done = this.store.runs
      .filter((r) => r.status !== "running" && r.status !== "queued" && this.appDirs.has(r.id))
      .slice(-this.boardHistory)
      .reverse();
    const row = (r: ClaudeRun): BoardRun => ({
      id: r.id,
      jd: r.jd,
      mode: r.mode ?? "edit",
      appDir: this.appDirs.get(r.id) ?? "",
      target: r.target,
      instruction: r.instruction,
      status: r.status,
      position: this.position(r.id),
      queuedAt: r.queuedAt ?? null,
      startedAt: r.startedAt,
      endedAt: r.endedAt,
      filesChanged: r.filesChanged ?? [],
      lastLines: this.lastLines.get(r.id) ?? [],
    });
    return {
      updatedAt: new Date().toISOString(),
      maxConcurrent: this.maxConcurrent,
      running: this.active.size,
      queued: this.queue.length,
      runs: [...live.map(row), ...done.map(row)],
    };
  }

  // ------------------------------------------------------------ scheduling

  /** Start queued runs, FIFO, skipping any whose application already has a run going. */
  private pump(): void {
    for (let i = 0; i < this.queue.length && this.active.size < this.maxConcurrent; ) {
      const busy = new Set([...this.active.values()].map((a) => a.run.jd));
      if (busy.has(this.queue[i].run.jd)) {
        i++;
        continue;
      }
      const [next] = this.queue.splice(i, 1);
      this.launch(next.run, next.appDir, next.custom);
    }
  }

  private launch(run: ClaudeRun, absAppDir: string, custom?: CustomRun): void {
    const peers: PeerRun[] = [
      ...[...this.active.values()].map((a) => a.run),
      ...this.queue.map((q) => q.run),
    ].map((r) => ({ jd: r.jd, target: r.target, instruction: r.instruction, status: r.status }));
    const boardPath = BOARD_FILE.startsWith(REPO_ROOT + path.sep) ? relToRepo(BOARD_FILE) : BOARD_FILE;
    let prompt: string;
    let argv: string[];
    if (custom) {
      const note = custom.beforeLaunch?.();
      if (note) this.push(run, { t: Date.now(), type: "system", text: note });
      prompt = custom.prompt();
      argv = custom.argv(prompt);
    } else {
      prompt =
        buildPrompt(run.jd, relToRepo(absAppDir), run.target, run.instruction, evidenceFor(run.target), conceptContextFor(run.target, absAppDir)) +
        coordinationPrompt(run.id, peers, boardPath, this.maxConcurrent);
      argv = claudeArgv(prompt);
    }
    run.prompt = prompt;
    run.argv = [CLAUDE_BIN, ...argv];
    run.status = "running";
    run.startedAt = new Date().toISOString();
    const cvFile = path.join(absAppDir, "03_cv.md");
    const before = snapshotCvs();
    if (!before.has(run.jd)) before.set(run.jd, { file: cvFile, text: readText(cvFile) ?? "" });

    // Strip the variables that mark a parent Claude Code session so the child starts clean.
    const env: NodeJS.ProcessEnv = { ...process.env, CV_DASHBOARD_RUN_ID: run.id };
    for (const k of PARENT_SESSION_VARS) delete env[k];

    // detached → own process group, so kill() can take down the build/library scripts claude spawned
    const child = spawn(CLAUDE_BIN, argv, { cwd: custom?.cwd ?? REPO_ROOT, env, stdio: ["ignore", "pipe", "pipe"], detached: true });
    const a: Active = {
      run,
      appDir: absAppDir,
      child,
      cancelled: false,
      timedOut: false,
      before,
      timer: setTimeout(() => {
        a.timedOut = true;
        this.kill(child);
      }, custom?.timeoutMs ?? CLAUDE_TIMEOUT_MS),
    };
    this.active.set(run.id, a);
    this.push(run, { t: Date.now(), type: "system", text: `started (${this.active.size}/${this.maxConcurrent} running)` });

    let buf = "";
    child.stdout!.setEncoding("utf-8").on("data", (chunk: string) => {
      buf += chunk;
      let nl: number;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        for (const ev of parseStreamLine(line)) this.push(run, ev);
      }
    });
    child.stderr!.setEncoding("utf-8").on("data", (chunk: string) => this.push(run, { t: Date.now(), type: "stderr", text: chunk }));
    child.on("error", (e) => this.push(run, { t: Date.now(), type: "stderr", text: `spawn failed: ${e.message}` }));
    child.on("close", async (code, signal) => {
      if (buf.trim()) for (const ev of parseStreamLine(buf)) this.push(run, ev);
      clearTimeout(a.timer);
      this.releaseLock(run.id);
      const status: RunStatus = a.cancelled ? "cancelled" : a.timedOut ? "timeout" : code === 0 ? "succeeded" : "failed";
      if (custom?.afterExit) {
        run.status = status;
        try {
          await custom.afterExit(run);
        } catch (e) {
          this.push(run, { t: Date.now(), type: "stderr", text: `after-run step failed: ${e instanceof Error ? e.message : String(e)}` });
        }
      }
      run.status = status;
      run.exitCode = code;
      run.endedAt = new Date().toISOString();
      const changedLines: Record<string, string[]> = {};
      for (const [jd, snap] of a.before) {
        const changes = lineChanges(snap.text, readText(snap.file) ?? "");
        if (jd === run.jd) run.cvChanges = changes;
        if (changes.length) changedLines[jd] = changes.map((c) => c.new).filter((x): x is string => x != null);
      }
      run.affectedJds = [...new Set([run.jd, ...Object.keys(changedLines)])];
      this.active.delete(run.id);
      this.push(run, { t: Date.now(), type: "exit", code, signal: signal ?? null, status });
      this.pump();
      this.store.save();
      this.emit(`end:${run.id}`);
      this.changed(true);
      this.emit("board-changed", { type: "board-changed", runId: run.id, jds: run.affectedJds, changedLines });
    });
  }

  /** A run that ends while it holds the shared lock (cancel, crash, forgot release) gives it back. */
  private releaseLock(runId: string): void {
    try {
      spawnSync(PYTHON, [SHARED_LOCK_PY, "release", "--run", runId, "--quiet"], { cwd: REPO_ROOT, timeout: 10_000, stdio: "ignore" });
    } catch {
      /* python missing: the holder still expires on its own */
    }
  }

  private kill(child: ChildProcess): void {
    // Signal the whole process group (negative pid); fall back to the child alone if the group is gone.
    const signalTree = (sig: NodeJS.Signals): void => {
      if (child.exitCode != null || child.signalCode != null) return;
      try {
        if (child.pid) process.kill(-child.pid, sig);
        else child.kill(sig);
      } catch {
        child.kill(sig);
      }
    };
    signalTree("SIGTERM");
    setTimeout(() => signalTree("SIGKILL"), 5000).unref();
  }

  private push(run: ClaudeRun, ev: RunEvent): void {
    run.events.push(ev);
    if (ev.type === "result") {
      run.resultText = ev.text;
      run.costUsd = ev.costUsd;
    }
    if (ev.type === "tool" && EDIT_TOOLS.has(ev.name) && ev.summary) {
      const rel = path.isAbsolute(ev.summary) ? relToRepo(ev.summary) : ev.summary;
      const files = (run.filesChanged ??= []);
      if (!files.includes(rel)) files.push(rel);
    }
    const line = boardLine(ev);
    if (line) this.lastLines.set(run.id, [...(this.lastLines.get(run.id) ?? []), line].slice(-3));
    this.emit(`event:${run.id}`, ev);
    if (line || ev.type === "tool") this.changed(false);
  }

  // ------------------------------------------------------------ board

  /** Status changes write the board at once; log lines at most every 500 ms. */
  private changed(now: boolean): void {
    if (now) {
      if (this.boardTimer) clearTimeout(this.boardTimer);
      this.boardTimer = null;
      this.writeBoard();
      return;
    }
    if (this.boardTimer) return;
    this.boardTimer = setTimeout(() => {
      this.boardTimer = null;
      this.writeBoard();
    }, 500);
    this.boardTimer.unref();
  }

  private writeBoard(): void {
    const board = this.board();
    try {
      mkdirSync(DATA_DIR, { recursive: true });
      const tmp = `${BOARD_FILE}.tmp`;
      writeFileSync(tmp, JSON.stringify(board, null, 2));
      renameSync(tmp, BOARD_FILE);
    } catch (e) {
      console.error("run board write failed:", e);
    }
    this.emit("board", { type: "board", board });
  }
}
