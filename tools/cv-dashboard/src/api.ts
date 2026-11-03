import type {
  ApplicationDetail,
  ApplicationRow,
  ClaudeRun,
  ClaudeRunSummary,
  ClaudeTarget,
  AlignResult,
  ApplyInfo,
  FormAnswersInfo,
  GapsResult,
  SotLeadsResult,
  PickSuggestion,
  RunBoard,
  RunStatus,
  MockApp,
  MockSessionView,
  MockStage,
} from "../shared/types";
import type { LiveInput, LiveTab } from "../shared/live";
import type { DailyRow, Decision, InterestAppendResult, InterestRow, PipelineList, SubmissionRow } from "../shared/pipeline";

export class ApiError extends Error {
  constructor(public status: number, message: string, public body: Record<string, unknown> = {}) {
    super(message);
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, (body as { error?: string }).error ?? res.statusText, body);
  return body as T;
}

const post = (body: unknown): RequestInit => ({ method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
const mockUrl = (jd: string, id?: string, action?: string) =>
  ["/api/mock", encodeURIComponent(jd), id && encodeURIComponent(id), action].filter(Boolean).join("/");

export const api = {
  mockApps: () => request<MockApp[]>("/api/mock"),
  mockStart: (jd: string, stage: MockStage, n: number, followups: number) => request<MockSessionView>(mockUrl(jd, undefined, "start"), post({ stage, n, followups })),
  mockSession: (jd: string, id: string) => request<MockSessionView>(mockUrl(jd, id)),
  mockAnswer: (jd: string, id: string, q: number, text: string, finish = false) =>
    request<MockSessionView>(mockUrl(jd, id, "answer"), post({ q, text, finish })),
  mockFinish: (jd: string, id: string, q: number) => request<MockSessionView>(mockUrl(jd, id, "finish"), post({ q })),
  mockSkip: (jd: string, id: string, q: number) => request<MockSessionView>(mockUrl(jd, id, "skip"), post({ q })),
  mockRetry: (jd: string, id: string, q: number) => request<MockSessionView>(mockUrl(jd, id, "retry"), post({ q })),
  mockReport: (jd: string, id: string) => request<MockSessionView>(mockUrl(jd, id, "report"), post({})),
  formAnswers: (jd: string) => request<FormAnswersInfo>(`/api/applications/${encodeURIComponent(jd)}/answers`),
  startFormAnswers: (jd: string, questions: string) =>
    request<{ runId: string; position: number; status: RunStatus }>(`/api/applications/${encodeURIComponent(jd)}/answers`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ questions }),
    }),
  applications: () => request<ApplicationRow[]>("/api/applications"),
  application: (jd: string) => request<ApplicationDetail>(`/api/applications/${encodeURIComponent(jd)}`),
  pick: (jd: string) => request<PickSuggestion>(`/api/applications/${encodeURIComponent(jd)}/pick`),
  align: (jd: string) => request<AlignResult>(`/api/applications/${encodeURIComponent(jd)}/align`),
  gaps: (jd: string) => request<GapsResult>(`/api/applications/${encodeURIComponent(jd)}/gaps`),
  sotLeads: (jd: string) => request<SotLeadsResult>(`/api/applications/${encodeURIComponent(jd)}/sot-leads`),
  applyInfo: (jd: string) =>
    request<ApplyInfo & { runId: string | null; runStatus: RunStatus | null }>(`/api/applications/${encodeURIComponent(jd)}/apply`),
  startApply: (jd: string, url: string) =>
    request<{ runId: string; position: number; status: RunStatus; url: string }>(`/api/applications/${encodeURIComponent(jd)}/apply`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url }),
    }),
  applyScreenshotUrl: (jd: string, name: string, v: number) =>
    `/api/applications/${encodeURIComponent(jd)}/apply/screenshot/${encodeURIComponent(name)}?v=${Math.round(v)}`,
  applyCoverLetterUrl: (jd: string) => `/api/applications/${encodeURIComponent(jd)}/apply/cover-letter`,
  submitApply: (jd: string, url: string, edits: { label: string; value: string }[] = []) =>
    request<{ state: string; submitted: boolean; error: string | null; confirmationText: string | null }>(
      `/api/applications/${encodeURIComponent(jd)}/apply/submit`,
      { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ confirm: "SUBMIT", url, edits }) },
    ),
  liveTabs: () => request<LiveTab[]>("/api/apply/live/tabs"),
  liveStreamUrl: (tab: string) => `/api/apply/live/${encodeURIComponent(tab)}/stream`,
  liveInput: (tab: string, input: LiveInput) =>
    request<{ ok: boolean; control?: string | null; submitControl?: boolean }>(`/api/apply/live/${encodeURIComponent(tab)}/input`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    }),
  liveDialog: (tab: string, accept: boolean, promptText?: string) =>
    request<{ ok: boolean; guard: boolean; accepted: boolean }>(`/api/apply/live/${encodeURIComponent(tab)}/dialog`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ accept, promptText }),
    }),
  pdfUrl: (jd: string) => `/api/applications/${encodeURIComponent(jd)}/pdf`,
  startRun: (jd: string, target: ClaudeTarget, instruction: string) =>
    request<{ runId: string; position: number; status: RunStatus }>("/api/claude", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jd, target, instruction }),
    }),
  run: (id: string) => request<ClaudeRun>(`/api/claude/${id}`),
  cancelRun: (id: string) => request<{ cancelled: string }>(`/api/claude/${id}`, { method: "DELETE" }),
  runs: () => request<{ running: string[]; queued: string[]; runs: ClaudeRunSummary[] }>("/api/claude/runs"),
  board: () => request<RunBoard>("/api/claude/board"),
  streamUrl: (id: string) => `/api/claude/${id}/stream`,
  /** Board updates and "board-changed" after each run (SSE). */
  eventsUrl: "/api/claude/events",
  pipelineDaily: () => request<PipelineList<DailyRow>>("/api/pipeline/daily"),
  pipelineInterest: () => request<PipelineList<InterestRow>>("/api/pipeline/interest"),
  pipelineSubmissions: () => request<PipelineList<SubmissionRow>>("/api/pipeline/submissions"),
  pipelineDecision: (url: string, decision: Decision) =>
    request<{ url: string; row: number }>("/api/pipeline/daily/decision", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url, decision }),
    }),
  pipelineNote: (url: string, note: string) =>
    request<{ url: string; row: number }>("/api/pipeline/daily/note", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url, note }),
    }),
  pipelineReferralSent: (url: string, name: string) =>
    request<{ url: string; row: number }>("/api/pipeline/referral-sent", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url, name }),
    }),
  pipelineAddInterest: (url: string, note: string) =>
    request<InterestAppendResult>("/api/pipeline/interest", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url, note }),
    }),
};
