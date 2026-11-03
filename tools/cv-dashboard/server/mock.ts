// 模擬面試 (mock interview): the browser shows one prepared question at a time and takes a typed or spoken answer;
// the server keeps the transcript, asks a fast model for at most N follow-ups, then runs interview-prep's mock.py
// (auto checks: unverified numbers, gap claims, traps) and a scoring model whose JSON goes through `mock.py score`
// (truth check of the "better" answer). Reference answers never reach the browser before a question is scored.
import { spawn } from "node:child_process";
import { existsSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { MockApp, MockAuto, MockChatMsg, MockFramework, MockItemView, MockSessionSummary, MockSessionView, MockStage } from "../shared/types.js";
import type { AppRec } from "./db.js";
import { CLAUDE_BIN, PYTHON, REPO_ROOT } from "./config.js";
import { appDir, listApplicationRecs } from "./application.js";
import { readJson, readText } from "./artifacts.js";
import { PARENT_SESSION_VARS } from "./claude.js";

export const MOCK_PY = ".claude/skills/interview-prep/scripts/mock.py";
const STAGES: MockStage[] = ["mixed", "hr", "tech", "hm"];
const ID_RE = /^mock_\d{8}_\d{6}(?:_1)*$/;
export const MAX_ANSWER_CHARS = 6000;
const FOLLOWUP_MODEL = () => process.env.MOCK_FOLLOWUP_MODEL ?? "sonnet";
const SCORE_MODEL = () => process.env.MOCK_SCORE_MODEL ?? "opus";
const LLM_TIMEOUT_MS = 120_000;

export class MockError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// ---------------------------------------------------------------- files
/** $D/12_mock; CV_MOCK_DIR (tests) puts every application's sessions under <CV_MOCK_DIR>/<jd> instead. */
const mockDir = (rec: AppRec) => (process.env.CV_MOCK_DIR ? path.join(process.env.CV_MOCK_DIR, rec.jd) : path.join(appDir(rec), "12_mock"));
export function validId(id: string): string {
  if (!ID_RE.test(id)) throw new MockError(400, `bad session id: ${id}`);
  return id;
}
const sessionFile = (rec: AppRec, id: string) => path.join(mockDir(rec), `${validId(id)}.json`);
const chatFile = (rec: AppRec, id: string) => path.join(mockDir(rec), `${validId(id)}.chat.json`);

interface ChatStore {
  /** question number → transcript (interviewer question first). */
  chats: Record<string, MockChatMsg[]>;
  scoreWarnings: Record<string, string[]>;
  /** questions being re-answered: the old score stays in the session file until mock.py archives it */
  retry?: Record<string, boolean>;
  briefCache?: Brief;
}

interface BriefItem {
  n: number;
  question: string;
  kind: string;
  targets: string[];
  evidence: string[];
  reported: string[];
  seeds: string[];
  reference: string;
  notes: string;
  frameworks: MockFramework[];
}
interface Brief {
  jd: string;
  company: string;
  title: string;
  stage: MockStage;
  followups: number;
  rubric: Record<string, string>;
  process: string[];
  gap_terms: string[];
  items: BriefItem[];
}

function loadSession(rec: AppRec, id: string): any {
  const s = readJson<any>(sessionFile(rec, id));
  if (!s) throw new MockError(404, `no mock session ${id}`);
  return s;
}
function loadChat(rec: AppRec, id: string): ChatStore {
  return readJson<ChatStore>(chatFile(rec, id)) ?? { chats: {}, scoreWarnings: {} };
}
function saveChat(rec: AppRec, id: string, c: ChatStore): void {
  writeFileSync(chatFile(rec, id), JSON.stringify(c, null, 2));
}

// ---------------------------------------------------------------- python
function py(args: string[], timeoutMs = 120_000): Promise<{ code: number; out: string; err: string }> {
  return new Promise((resolve) => {
    const child = spawn(PYTHON, [MOCK_PY, ...args], { cwd: REPO_ROOT, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    let err = "";
    child.stdout.setEncoding("utf-8").on("data", (d: string) => (out += d));
    child.stderr.setEncoding("utf-8").on("data", (d: string) => (err += d));
    const t = setTimeout(() => child.kill("SIGKILL"), timeoutMs);
    child.on("close", (code) => {
      clearTimeout(t);
      resolve({ code: code ?? 1, out, err });
    });
  });
}

async function brief(rec: AppRec, id: string, store: ChatStore): Promise<Brief> {
  if (store.briefCache) return store.briefCache;
  const r = await py(["brief", sessionFile(rec, id), "--json"]);
  if (r.code !== 0) throw new MockError(500, `mock.py brief failed: ${(r.err || r.out).slice(0, 400)}`);
  store.briefCache = JSON.parse(r.out) as Brief;
  return store.briefCache;
}

// ---------------------------------------------------------------- headless model calls (no tools, no MCP, no settings)
export function llmArgv(system: string, prompt: string, schema: object, model: string): string[] {
  return [
    "-p", prompt,
    "--output-format", "json",
    "--model", model,
    "--tools", "",
    "--no-session-persistence",
    "--strict-mcp-config",
    "--setting-sources", "",
    "--system-prompt", system,
    "--json-schema", JSON.stringify(schema),
  ];
}

type Llm = (system: string, prompt: string, schema: object, model: string) => Promise<any>;

export const callLlm: Llm = (system, prompt, schema, model) =>
  new Promise((resolve, reject) => {
    const env: NodeJS.ProcessEnv = { ...process.env };
    for (const k of PARENT_SESSION_VARS) delete env[k];
    const child = spawn(CLAUDE_BIN, llmArgv(system, prompt, schema, model), { cwd: REPO_ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    let err = "";
    child.stdout.setEncoding("utf-8").on("data", (d: string) => (out += d));
    child.stderr.setEncoding("utf-8").on("data", (d: string) => (err += d));
    const t = setTimeout(() => child.kill("SIGKILL"), LLM_TIMEOUT_MS);
    child.on("close", (code) => {
      clearTimeout(t);
      try {
        const d = JSON.parse(out);
        if (d.is_error || !d.structured_output) throw new Error(String(d.result ?? "no structured output").slice(0, 300));
        resolve(d.structured_output);
      } catch (e) {
        reject(new MockError(502, `model call failed (exit ${code}): ${e instanceof Error ? e.message : String(e)} ${err.slice(0, 200)}`));
      }
    });
  });

let llm: Llm = callLlm;
/** Tests swap the model call. */
export function setLlm(f: Llm | null): void {
  llm = f ?? callLlm;
}

const FOLLOWUP_SCHEMA = {
  type: "object",
  properties: { action: { type: "string", enum: ["followup", "done"] }, say: { type: "string" } },
  required: ["action"],
  additionalProperties: false,
};
export function scoreSchema(frameworks: MockFramework[]) {
  const keys = frameworks.map((f) => f.key);
  return {
    ...SCORE_SCHEMA,
    properties: {
      ...SCORE_SCHEMA.properties,
      framework: { type: "string", enum: keys.length ? keys : ["PREP"] },
      chunks: {
        type: "array",
        items: {
          type: "object",
          properties: { label: { type: "string" }, yours: { type: "string" }, tip: { type: "string" }, better: { type: "string" } },
          required: ["label", "yours", "tip", "better"],
          additionalProperties: false,
        },
      },
    },
    required: ["scores", "feedback", "framework", "chunks"],
  };
}

const SCORE_SCHEMA = {
  type: "object",
  properties: {
    scores: {
      type: "object",
      properties: Object.fromEntries(["structure", "evidence", "relevance", "honesty", "clarity", "followups"].map((k) => [k, { type: "integer", minimum: 1, maximum: 5 }])),
      required: ["structure", "evidence", "relevance", "honesty", "clarity"],
      additionalProperties: false,
    },
    feedback: { type: "string" },
    better: { type: "string" },
    better_bullets: { type: "array", items: { type: "string" } },
    notes: { type: "string" },
    honesty_note: { type: "string" },
  },
  required: ["scores", "feedback", "better"],
  additionalProperties: false,
};

function transcript(chat: MockChatMsg[]): string {
  return chat.map((m) => `${m.role === "interviewer" ? "INTERVIEWER" : "CANDIDATE"}: ${m.text}`).join("\n");
}

function itemBrief(b: Brief, it: BriefItem, withReference: boolean): string {
  return [
    `Role: ${b.title} at ${b.company}. Interview stage: ${b.stage}.`,
    `Question ${it.n} [${it.kind}]: ${it.question}`,
    it.targets.length ? `Targets: ${it.targets.join("; ")}` : "",
    it.evidence.length ? `Candidate's evidence (library bullets):\n- ${it.evidence.join("\n- ")}` : "",
    b.gap_terms.length ? `Gap / do-not-claim terms (the candidate has NOT done these): ${b.gap_terms.join(", ")}` : "",
    `Follow-up ideas: ${it.seeds.join(" / ")}`,
    withReference ? `Reference answer (library facts only): ${it.reference}` : "",
    withReference && it.notes ? `Reference notes: ${it.notes}` : "",
    withReference && it.frameworks?.length
      ? "Answer frameworks allowed (default first):\n" +
        it.frameworks.map((f) => `- ${f.key} = ${f.name}: ` + f.chunks.map((c) => `${c.label} (${c.goal})`).join(" → ")).join("\n")
      : "",
  ].filter(Boolean).join("\n");
}

const FOLLOWUP_SYSTEM =
  "You are the interviewer in a realistic mock job interview. Speak English. Decide whether to ask ONE follow-up. " +
  "Ask one only when it adds value: aim at the weakest spot of the candidate's last answer — a vague 'we', a number " +
  "without a definition or baseline, a claim beyond the evidence, a gap term said as if done, a missing result, a " +
  "trade-off not explained. One sentence, at most 30 words, no praise, no hints, never reveal or paraphrase the " +
  "reference answer, never teach. For a gap the candidate admitted honestly, ask how they would close it, not to " +
  "prove the gap. If the answer was off-topic or empty, ask them to answer the question. If the answer is complete " +
  "and specific, or the follow-up budget is used, return action 'done'.";

const SCORE_SYSTEM =
  "You score one answer in a mock job interview for the candidate's practice. Rubric 1-5 per dimension (5 = an " +
  "answer a hiring manager would remember positively). Omit 'followups' when no follow-up was asked. If the auto " +
  "checks list unverified numbers, gap claims or traps, honesty is at most 3 unless honesty_note explains why. " +
  "feedback: Traditional Chinese (繁體中文), 2-3 overall fixes as lines starting with '• ', quoting the candidate's " +
  "words where useful. Teach the STRUCTURE: pick one allowed framework (the default unless another fits the question " +
  "clearly better) and map the answer onto its chunks, in order, with the exact chunk labels (a label may repeat, as " +
  "PREP's Point). For each chunk: yours = 繁中, what the candidate actually said for this part (short quote) or " +
  "'沒有講到'; tip = 繁中, one concrete fix for this part; better = this part of the stronger answer in spoken " +
  "English, first person. All chunk 'better' texts joined form the full stronger answer: 60-170 words, using ONLY " +
  "facts from the evidence bullets and the reference answer — never add a number, tool, team size, cause or result " +
  "they do not hold; keep 'TODO candidate:' where a fact is missing; keep what the candidate did well. " +
  "better_bullets: extra bullet codes from the evidence you used (optional). notes: copy CONF references from the " +
  "reference notes that your better answer relies on (e.g. 'CONF-an CONF-o1').";

// ---------------------------------------------------------------- views
function overall(scores: Record<string, number> | null | undefined): number | null {
  if (!scores) return null;
  const v = Object.values(scores).filter((x) => typeof x === "number");
  return v.length ? Math.round((v.reduce((a, b) => a + b, 0) / v.length) * 100) / 100 : null;
}

export function sessionView(rec: AppRec, id: string): MockSessionView {
  const s = loadSession(rec, id);
  const c = loadChat(rec, id);
  const items: MockItemView[] = (s.items as any[]).map((it) => {
    const chat = c.chats[String(it.n)] ?? [{ role: "interviewer", text: it.question }];
    const scored = !!it.scores && !c.retry?.[String(it.n)];
    const status: MockItemView["status"] = scored ? "scored" : it.skipped ? "skipped" : chat.length > 1 ? "answering" : "open";
    return {
      n: it.n,
      frameworks: (it.frameworks as MockFramework[]) ?? [],
      framework: scored ? it.framework ?? null : null,
      chunks: scored ? it.chunks ?? null : null,
      question: it.question,
      kind: it.kind,
      reported: (it.reported ?? []).length > 0,
      status,
      chat,
      followupsAsked: chat.filter((m) => m.role === "interviewer").length - 1,
      auto: scored || status === "answering" ? ((it.auto as MockAuto) ?? null) : null,
      scores: scored ? it.scores : null,
      overall: scored ? overall(it.scores) : null,
      feedback: scored ? it.feedback ?? "" : "",
      better: scored ? it.better ?? "" : "",
      reference: scored || it.skipped ? it.reference : null,
      scoreWarnings: c.scoreWarnings[String(it.n)] ?? [],
    };
  });
  const md = path.join(mockDir(rec), `${id}.md`);
  return {
    id, jd: s.jd, company: s.company, title: s.title, stage: s.stage, maxFollowups: s.followups, started: s.started,
    rubric: s.rubric, items, reportMd: existsSync(md) ? readText(md) : null,
  };
}

function summary(rec: AppRec, id: string): MockSessionSummary | null {
  try {
    const s = loadSession(rec, id);
    const done = (s.items as any[]).filter((x) => x.scores);
    const avg = done.length ? done.reduce((a, x) => a + (overall(x.scores) ?? 0), 0) / done.length : null;
    return { id, started: s.started, stage: s.stage, scored: done.length, total: s.items.length, average: avg === null ? null : Math.round(avg * 100) / 100 };
  } catch {
    return null;
  }
}

export function mockApps(): MockApp[] {
  const out: MockApp[] = [];
  for (const rec of listApplicationRecs()) {
    const prep = readJson<any[]>(path.join(appDir(rec), "10_interview.json"));
    if (!Array.isArray(prep)) continue;
    const dir = mockDir(rec);
    const ids = existsSync(dir) ? readdirSync(dir).filter((f) => /^mock_.*\.json$/.test(f) && !f.endsWith(".chat.json")).map((f) => f.slice(0, -5)).filter((x) => ID_RE.test(x)) : [];
    out.push({
      jd: rec.jd, company: rec.company, title: rec.title,
      questions: prep.filter((q) => q && q.kind !== "ask").length,
      sessions: ids.sort().reverse().map((x) => summary(rec, x)).filter((x): x is MockSessionSummary => !!x),
    });
  }
  return out;
}

// ---------------------------------------------------------------- actions (serialised per session)
const locks = new Map<string, Promise<unknown>>();
function serial<T>(key: string, f: () => Promise<T>): Promise<T> {
  const prev = locks.get(key) ?? Promise.resolve();
  const next = prev.catch(() => undefined).then(f);
  locks.set(key, next.finally(() => locks.get(key) === next && locks.delete(key)));
  return next;
}

export async function startMock(rec: AppRec, body: Record<string, unknown>): Promise<MockSessionView> {
  const stage = (STAGES.includes(body.stage as MockStage) ? body.stage : "mixed") as MockStage;
  const n = Math.min(10, Math.max(1, Math.round(Number(body.n ?? 5)) || 5));
  const fu = Math.min(3, Math.max(0, Math.round(Number(body.followups ?? 2)) || 0));
  if (!existsSync(path.join(appDir(rec), "10_interview.json"))) throw new MockError(400, "這份申請還沒有面試準備 (10_interview.json),請先跑 /interview-prep");
  const r = await py(["start", "--jd", rec.jd, "--stage", stage, "--n", String(n), "--followups", String(fu), "--out-dir", mockDir(rec)]);
  const m = /^session: (.+\.json)$/m.exec(r.out);
  if (r.code !== 0 || !m) throw new MockError(500, `mock.py start failed: ${(r.err || r.out).slice(0, 400)}`);
  return sessionView(rec, path.basename(m[1].trim(), ".json"));
}

export function answerMock(rec: AppRec, id: string, body: Record<string, unknown>): Promise<MockSessionView> {
  return serial(id, async () => {
    const q = Number(body.q);
    const text = typeof body.text === "string" ? body.text.trim() : "";
    if (!text) throw new MockError(400, "請先回答(文字或語音)");
    if (text.length > MAX_ANSWER_CHARS) throw new MockError(400, `回答太長(最多 ${MAX_ANSWER_CHARS} 字元)`);
    const s = loadSession(rec, id);
    const it = (s.items as any[]).find((x) => x.n === q);
    if (!it) throw new MockError(404, `no Q${q}`);
    const store = loadChat(rec, id);
    if (it.scores && !store.retry?.[String(q)]) throw new MockError(409, `Q${q} 已評分;要重答請按「重答」`);
    const chat = (store.chats[String(q)] ??= [{ role: "interviewer", text: it.question }]);
    chat.push({ role: "candidate", text });
    saveChat(rec, id, store);
    const asked = chat.filter((m) => m.role === "interviewer").length - 1;
    if (asked < s.followups && body.finish !== true) {
      const b = await brief(rec, id, store);
      const bi = b.items.find((x) => x.n === q)!;
      const d = await llm(
        FOLLOWUP_SYSTEM,
        `${itemBrief(b, bi, true)}\n\nFollow-ups asked so far: ${asked} of at most ${s.followups}.\n\nTranscript:\n${transcript(chat)}`,
        FOLLOWUP_SCHEMA,
        FOLLOWUP_MODEL(),
      );
      if (d.action === "followup" && typeof d.say === "string" && d.say.trim()) {
        chat.push({ role: "interviewer", text: d.say.trim() });
        store.briefCache = b;
        saveChat(rec, id, store);
        return sessionView(rec, id);
      }
    }
    await finishItem(rec, id, q, store);
    return sessionView(rec, id);
  });
}

/** Score now (the candidate pressed 結束這題, or the follow-up budget is used). */
export function finishMock(rec: AppRec, id: string, body: Record<string, unknown>): Promise<MockSessionView> {
  return serial(id, async () => {
    const q = Number(body.q);
    const store = loadChat(rec, id);
    const chat = store.chats[String(q)] ?? [];
    if (!chat.some((m) => m.role === "candidate")) throw new MockError(400, "這題還沒有回答");
    await finishItem(rec, id, q, store);
    return sessionView(rec, id);
  });
}

/** transcript → {answer, followups[{q,a}]}: candidate lines before the first follow-up form the answer. */
export function turnFromChat(chat: MockChatMsg[]): { answer: string; followups: { q: string; a: string }[] } {
  const main: string[] = [];
  const fus: { q: string; a: string }[] = [];
  for (const m of chat.slice(1)) {
    if (m.role === "interviewer") fus.push({ q: m.text, a: "" });
    else if (fus.length) fus[fus.length - 1].a = [fus[fus.length - 1].a, m.text].filter(Boolean).join(" ");
    else main.push(m.text);
  }
  return { answer: main.join(" "), followups: fus.filter((f) => f.a) };
}

async function finishItem(rec: AppRec, id: string, q: number, store: ChatStore): Promise<void> {
  const chat = store.chats[String(q)] ?? [];
  const turn = turnFromChat(chat);
  const dir = mockDir(rec);
  const turnPath = path.join(dir, `${id}.turn.json`);
  writeFileSync(turnPath, JSON.stringify(turn));
  const a = await py(["answer", sessionFile(rec, id), "--q", String(q), "--file", turnPath]);
  if (a.code !== 0) throw new MockError(500, `mock.py answer failed: ${(a.err || a.out).slice(0, 400)}`);
  const s = loadSession(rec, id);
  const it = (s.items as any[]).find((x) => x.n === q);
  const b = await brief(rec, id, store);
  const bi = b.items.find((x) => x.n === q)!;
  const rubric = Object.entries(b.rubric).map(([k, v]) => `- ${k}: ${v}`).join("\n");
  let prompt =
    `${itemBrief(b, bi, true)}\n\nRubric:\n${rubric}\n\nTranscript:\n${transcript(chat)}\n\n` +
    `Auto checks (script): ${JSON.stringify(it.auto)}`;
  const scorePath = path.join(dir, `${id}.score.json`);
  let last = "";
  for (let attempt = 0; attempt < 3; attempt++) {
    const sc = await llm(SCORE_SYSTEM, prompt, scoreSchema(bi.frameworks ?? []), SCORE_MODEL());
    if (!turn.followups.length && sc.scores) delete sc.scores.followups;
    writeFileSync(scorePath, JSON.stringify(sc));
    const r = await py(["score", sessionFile(rec, id), "--q", String(q), "--file", scorePath]);
    const warns = r.out.split("\n").filter((l) => l.startsWith("WARN")).map((l) => l.slice(5).trim());
    if (r.code === 0) {
      store.scoreWarnings[String(q)] = warns;
      if (store.retry) delete store.retry[String(q)];
      store.briefCache = b;
      saveChat(rec, id, store);
      return;
    }
    last = r.out.split("\n").filter((l) => l.startsWith("ERROR")).join("\n") || r.err;
    prompt += `\n\nYour previous scoring was REFUSED by the checker:\n${last}\nFix exactly these problems (remove or ` +
      "replace any number or claim not in the evidence; lower honesty or explain in honesty_note) and score again.";
  }
  throw new MockError(502, `評分三次都沒通過檢查:${last.slice(0, 300)}`);
}

export function retryMock(rec: AppRec, id: string, body: Record<string, unknown>): Promise<MockSessionView> {
  return serial(id, async () => {
    const q = Number(body.q);
    const s = loadSession(rec, id);
    const it = (s.items as any[]).find((x) => x.n === q);
    if (!it) throw new MockError(404, `no Q${q}`);
    const store = loadChat(rec, id);
    // the old attempt stays in the session file; mock.py moves it to `past` when the new answer is recorded
    store.chats[String(q)] = [{ role: "interviewer", text: it.question }];
    delete store.scoreWarnings[String(q)];
    (store.retry ??= {})[String(q)] = true;
    saveChat(rec, id, store);
    return sessionView(rec, id);
  });
}

export function skipMock(rec: AppRec, id: string, body: Record<string, unknown>): Promise<MockSessionView> {
  return serial(id, async () => {
    const r = await py(["skip", sessionFile(rec, id), "--q", String(Number(body.q))]);
    if (r.code !== 0) throw new MockError(500, `mock.py skip failed: ${(r.err || r.out).slice(0, 300)}`);
    return sessionView(rec, id);
  });
}

export function reportMock(rec: AppRec, id: string): Promise<MockSessionView> {
  return serial(id, async () => {
    const r = await py(["report", sessionFile(rec, id)]);
    if (r.code !== 0) throw new MockError(500, `mock.py report failed: ${(r.err || r.out).slice(0, 300)}`);
    return sessionView(rec, id);
  });
}
