// Types shared by the API server and the React client.

export type ItemKind =
  | "name"
  | "headline"
  | "contact"
  | "section"
  | "summary"
  | "skill_row"
  | "role"
  | "scope"
  | "card"
  | "bullet"
  | "other_bullet"
  | "other";

export interface PickRef {
  /** Library code: bullet (VI1), project (PS), scope (SCOPE-ATS) or skill-row label. */
  code: string;
  /** Lead / title / context / row-variant ordinal used on this CV. */
  variant: number | null;
  variantLabel: string | null;
  contextOrd?: number | null;
  status: string | null;
  conf: string | null;
}

export interface CvItem {
  /** Stable within one CV text: "L<line index>". */
  id: string;
  line: number;
  kind: ItemKind;
  /** Section heading this item sits under (e.g. "PROFESSIONAL EXPERIENCE"). */
  section: string;
  /** Role heading for experience items ("Senior AI Engineer | Delta Electronics…"). */
  role?: string;
  raw: string;
  /** Plain text (bold markers removed). */
  text: string;
  label?: string;
  items?: string[];
  lead?: string;
  body?: string;
  title?: string;
  context?: string;
  years?: string;
  pick?: PickRef;
  /** Whether the ATS keyword scan reads this line (headline, contact, row labels are not scanned). */
  scanned: boolean;
  /** JD terms this item literally contains (same rule as ats_check.py). */
  hits: string[];
  /** Library rows behind the pick: Notion links and the stored alternatives. Only on items with a pick. */
  library?: ItemLibrary;
}

export type VariantKind = "lead" | "title" | "context" | "scope" | "label" | "spelling";

export interface LibraryVariant {
  kind: VariantKind;
  /** Ordinal as library_db.py uses it (lead / title / context / label / spelling ord; scope = row index). */
  ord: number;
  text: string;
  /** The variant this CV uses now. */
  current: boolean;
  conf?: string | null;
  note?: string | null;
  /** Scope line code (kind "scope"). */
  code?: string;
  /** Skills item the spelling belongs to (kind "spelling"). */
  item?: string;
}

export interface SkillItemLink {
  item: string;
  ord: number;
  /** skills.notion_id of the matching skill, null when none matches or it has no page yet. */
  notionUrl: string | null;
}

export interface ItemLibrary {
  /** Notion page of the item itself (bullets / projects / skills row), null when not mirrored yet. */
  notionUrl: string | null;
  projectCode?: string;
  projectName?: string;
  projectNotionUrl?: string | null;
  variants: LibraryVariant[];
  /** Bullet body as stored in the library. */
  bodyPreview?: string;
  /** Skill rows: each item with its Notion page. */
  skillItems?: SkillItemLink[];
}

export interface BulletRef {
  code: string;
  notionUrl: string | null;
  projectCode: string;
  projectNotionUrl: string | null;
  leads: { ord: number; text: string }[];
}

export type KeywordType = "required" | "preferred" | "keyword";

export type CoverageGroup =
  | "found"
  | "covered elsewhere"
  | "SoT-supported wording"
  | "gap"
  | "do not claim"
  | "unclassified";

export interface LibraryCoverage {
  /** Raw `keywords.coverage` value, or null when the term is not in the keyword map. */
  coverage: string | null;
  /** Grouping per ats_check.library_status (for missing terms) — "found" never appears here. */
  group: Exclude<CoverageGroup, "found">;
  notes: string;
  bullets: string[];
  skills: string[];
  /** Other applications whose JD carries this term. */
  otherJds: string[];
  /** keywords.notion_id as a URL; null when the term is not mirrored to the JD keyword map. */
  notionUrl: string | null;
  /** The covering bullets with Notion links and lead variants. */
  bulletRefs: BulletRef[];
  skillRefs: { name: string; notionUrl: string | null }[];
}

export interface KeywordRow {
  term: string;
  type: KeywordType;
  found: boolean;
  jdCount: number;
  library: LibraryCoverage;
  /** CV item ids that contain the term. */
  items: string[];
  /** Concept the term resolves to (keywords.concept_code, else concept_aliases); null when unresolved. */
  concept: ConceptRef | null;
}

// ---------------------------------------------------------------- concept layer (library_db_spec_v3)

export type ConceptClaim = "covered" | "SoT-supported wording" | "gap" | "do not claim" | string;

export interface ConceptRef {
  code: string;
  name: string;
  claim: ConceptClaim;
  kind: string;
}

export interface ConceptEvidence {
  kind: "bullet" | "skill" | "lead" | "scope" | "context" | string;
  code: string;
  strength: "direct" | "implied" | string;
  note?: string;
  /** Bullet / skill / project page; null when not mirrored. */
  notionUrl: string | null;
  /** Project of a bullet or lead. */
  projectCode?: string;
}

export interface ConceptJdTerm {
  term: string;
  type: KeywordType;
  found: boolean;
}

export interface ConceptGroup extends ConceptRef {
  /** 3 required / 2 preferred / 1 keyword: max over the JD terms that resolve to it. */
  weight: number;
  definition: string;
  notes: string;
  notionUrl: string | null;
  jdTerms: ConceptJdTerm[];
  evidence: ConceptEvidence[];
  aliases: string[];
  relations: { src: string; rel: string; dst: string }[];
}

export type AlignActionKind = "keep" | "use_lead" | "new_lead" | "spelling" | "summary";

export interface AlignRow {
  /** Bullet code, "skills: <row label>" or "Summary". */
  line: string;
  concept: string;
  weight: number | null;
  jdPhrase: string;
  current: string;
  action: string;
  actionKind: AlignActionKind;
  leadOrd?: number;
  /** new_lead: the proposed lead text. */
  proposal?: string;
  /** spelling: the variant to write. */
  variant?: string;
  /** spelling on a Skills row: the row label. */
  skillRow?: string;
}

export interface AlignResult {
  rows: AlignRow[];
  notes: string[];
  summary: string;
  picks: string;
  picksUnchanged: boolean;
  raw: string;
  stderr: string;
  exitCode: number | null;
}

export type GapGroupId = "sotSupported" | "gap" | "doNotClaim" | "coveredNotClaimable";

export interface GapConcept {
  code: string;
  name: string;
  weight: number | null;
  /** type for the first three groups; for coveredNotClaimable the term's own coverage (gap / do not claim). */
  jdTerms: { term: string; type: KeywordType | null; status?: string }[];
  evidence: ConceptEvidence[];
  notes: string;
}

export interface GapsResult {
  groups: Record<GapGroupId, GapConcept[]>;
  /** This JD's terms with no concept (keywords.concept_code IS NULL or not in the keyword map). */
  unresolved: { term: string; type: KeywordType; inKeywordMap: boolean }[];
  raw: string;
  stderr: string;
  exitCode: number | null;
}

/** library_db.py sot-leads --jd <Co> --json: per picked bullet, the JD's missing SoT-supported terms. */
export interface SotLeadsResult {
  /** `draft` is an auto term list, never to be used verbatim. */
  bullets: Record<string, { terms: string[]; draft: string; body: string }>;
  raw: string;
  stderr: string;
  exitCode: number | null;
}

/** Per-application concept counts for the JD's concepts (ApplicationRow.concepts). */
export interface ConceptCounts {
  covered: number;
  sot: number;
  gap: number;
  doNotClaim: number;
  unresolved: number;
}

export interface AtsSummary {
  overall: number | null;
  subScores: Record<string, number>;
  keywordPercent: number | null;
  found: number;
  nTerms: number;
  parseChecks: { file: string; extractionLayout: number; content: number; flagged: string[] }[];
  repeated: { term: string; jd_count: number; in_summary: boolean; in_skills: boolean }[];
  recommendations: string[];
}

export interface LayoutSummary {
  status: string | null;
  utilisation: string | null;
  checks: { check: string; status: string; detail: string }[];
}

export interface ReviewSummary {
  hr: number | null;
  hm: number | null;
  verdict: string | null;
  headline: string | null;
  edits: { cells: string[] }[];
  editsHeader: string[];
}

export interface ApplicationRow {
  jd: string;
  company: string;
  title: string;
  dir: string;
  headline: string;
  country: string;
  atsOverall: number | null;
  keywordsFound: number | null;
  keywordsTotal: number | null;
  layoutStatus: string | null;
  utilisation: string | null;
  hr: number | null;
  hm: number | null;
  verdict: string | null;
  date: string;
  /** JD concepts by claim (SQL over keyword_jds → keywords.concept_code → concepts.claim). */
  concepts: ConceptCounts | null;
}

export interface ApplicationDetail {
  row: ApplicationRow;
  cv: { lines: CvItem[]; text: string; mtime: number };
  keywords: KeywordRow[];
  /** Concepts this JD's terms resolve to, heaviest first. */
  concepts: ConceptGroup[];
  hits: { byItem: Record<string, string[]>; byTerm: Record<string, string[]> };
  matcherFound: string[];
  ats: AtsSummary;
  layout: LayoutSummary;
  review: ReviewSummary;
  jdBrief: string;
  pdfUrl: string | null;
  picks: string;
  notion: {
    /** applications.notion_id as a URL, null when the application has no Notion page. */
    applicationUrl: string | null;
    /** The "Personal experiences" root page. */
    rootUrl: string;
  };
}

export interface PickDiffRow {
  key: string;
  current: string | null;
  suggested: string | null;
  state: "same" | "changed" | "current only" | "suggested only";
}

export interface PickSuggestion {
  header: string;
  suggested: string;
  current: string;
  diff: PickDiffRow[];
  rationale: string[];
  fit: string[];
  fitVerdict: string | null;
  raw: string;
  stderr: string;
  exitCode: number | null;
}

export type TargetKind = ItemKind | "term";

/** What the Ask Claude drawer shows in its Library panel (client only; never sent to the API). */
export interface TargetLibrary {
  item?: ItemLibrary;
  keyword?: { term: string; notionUrl: string | null; bulletRefs: BulletRef[]; skillRefs: { name: string; notionUrl: string | null }[] };
}

export interface ClaudeTarget {
  kind: TargetKind;
  code?: string;
  text?: string;
  term?: string;
}

export type RunEvent =
  | { t: number; type: "system"; text: string }
  | { t: number; type: "text"; text: string }
  | { t: number; type: "tool"; name: string; summary: string }
  | { t: number; type: "tool_result"; summary: string; isError: boolean }
  | { t: number; type: "result"; text: string; isError: boolean; costUsd: number | null; durationMs: number | null; turns: number | null }
  | { t: number; type: "stderr"; text: string }
  | { t: number; type: "exit"; code: number | null; signal: string | null; status: RunStatus };

export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "timeout";

export interface CvLineChange {
  old: string | null;
  new: string | null;
}

export type RunMode = "edit" | "apply" | "answers";

export interface ClaudeRun {
  id: string;
  jd: string;
  /** "apply" = job-apply form fill (stops at review); missing on older runs = "edit". */
  mode?: RunMode;
  target: ClaudeTarget;
  instruction: string;
  /** Empty until the run leaves the queue (the prompt lists the runs active at that moment). */
  prompt: string;
  argv: string[];
  /** When it was POSTed; older history entries lack it. */
  queuedAt?: string;
  /** null while queued. */
  startedAt: string | null;
  endedAt: string | null;
  status: RunStatus;
  exitCode: number | null;
  resultText: string | null;
  costUsd: number | null;
  events: RunEvent[];
  cvChanges: CvLineChange[];
  /** Repo-relative paths the run edited or wrote (from its Edit/Write tool calls). */
  filesChanged?: string[];
  /** Applications whose 03_cv.md changed while this run was going (own one included). */
  affectedJds?: string[];
}

export type ClaudeRunSummary = Omit<ClaudeRun, "events" | "prompt" | "argv"> & {
  /** 1-based place in the queue; null unless queued. */
  position: number | null;
};

export interface BoardRun {
  id: string;
  jd: string;
  mode: RunMode;
  appDir: string;
  target: ClaudeTarget;
  instruction: string;
  status: RunStatus;
  position: number | null;
  queuedAt: string | null;
  startedAt: string | null;
  endedAt: string | null;
  filesChanged: string[];
  lastLines: string[];
}

/** tools/cv-dashboard/data/run_board.json, rewritten on every change. */
export interface RunBoard {
  updatedAt: string;
  maxConcurrent: number;
  running: number;
  queued: number;
  runs: BoardRun[];
}

/** GET /api/events (SSE). */
export type BoardEvent =
  | { type: "board"; board: RunBoard }
  | { type: "board-changed"; runId: string; jds: string[]; changedLines: Record<string, string[]> };

export type ApplyState =
  | "filled_awaiting_confirm"
  | "blocked_login"
  | "blocked_captcha"
  | "blocked_mfa"
  | "failed"
  | "submitting"
  | "submitted"
  | "submit_uncertain"
  | "submit_failed";

/** data/apply/<jd>/status.json: written by the fill run, normalised by the server, then updated by
 *  scripts/apply-submit.mjs (the only code that submits). */
export interface ApplyStatusFile {
  state: ApplyState;
  reachedReview: boolean;
  submitted: boolean;
  /** URL of the tab holding the filled form. */
  url: string;
  formType?: string;
  /** Exact text of the final submit button, recorded by the fill run for the submit step. */
  submitControl?: { text: string } | null;
  filledCount?: number | null;
  pendingCount: number | null;
  requiredBlank?: number | null;
  blocker: string | null;
  notes?: string | null;
  startedAt?: string;
  finishedAt: string;
  runId?: string;
  /** Kept alive between fill and submit: the persistent browser's CDP endpoint and the tab id. */
  cdpEndpoint?: string;
  targetId?: string | null;
  submitRequestedAt?: string;
  submitStartedAt?: string;
  submitFinishedAt?: string;
  submittedAt?: string;
  confirmationText?: string;
  submitError?: string;
  pageLost?: boolean;
  /** Reported by the fill run. */
  coverLetter?: { source: "existing" | "generated" | "none"; dir?: string; scored?: boolean; verdict?: string | null; uploaded?: boolean; pasted?: boolean; error?: string };
  afterUrl?: string;
  /** Guard confirm() dialogs accepted during the submit. */
  guardAccepted?: number;
  /** "live" = the candidate submitted in the Live browser view; otherwise Confirm & submit. */
  submittedVia?: "confirm" | "live";
  editsApplied?: number;
}

export interface ApplyField {
  label: string;
  value: string;
  source: "profile" | "answers" | "generated" | "inferred" | "blank";
  required?: boolean;
  /** Changed by the candidate in the Apply status panel; re-written into the page before Submit. */
  edited?: boolean;
  step?: number;
  note?: string;
}

/** GET /api/applications/:jd/apply */
export interface ApplyInfo {
  jd: string;
  /** Job URL the next Auto apply will open (saved override, else the first URL in the JD files). */
  url: string | null;
  urlSource: string | null;
  cvPdf: string | null;
  /** The letter for this company in cv/cover_letters (existing, or generated by the last fill run). */
  coverLetter: {
    source: "existing" | "generated" | "none";
    dir?: string;
    pdf: string | null;
    scored?: boolean;
    uploaded?: boolean | null;
    pasted?: boolean | null;
    available: boolean;
  } | null;
  /** job-apply plugin install path; null = plugin not installed. */
  pluginRoot: string | null;
  status: ApplyStatusFile | null;
  /** What to do at the desk for a blocker. */
  help: string | null;
  /** Confirm & submit is allowed (state filled_awaiting_confirm, no submit in progress). */
  canSubmit: boolean;
  submitting: boolean;
  fields: ApplyField[] | null;
  summary: string | null;
  /** step-NN.png, review.png, pre-submit.png, confirmation.png with mtimes (cache-busters). */
  screenshots: { name: string; mtime: number }[];
  /** Result of the Google Sheet tracker append (after a submit). */
  sheet: { ok: boolean; updated?: number | null; added: number | null; skipped: number | null; output: string; at: string } | null;
}

/** One answer from the form-answers skill ($D/08_form_answers.json). */
export interface FormAnswer {
  question: string;
  answer: string;
  kind: "standing" | "generated" | "needs_you";
  words?: number;
  sources?: string[];
  note?: string;
}

export interface FormAnswersInfo {
  jd: string;
  /** The questions sent with the last run (questions.txt), for re-running. */
  questions: string | null;
  answers: FormAnswer[] | null;
  skipped: string[];
  generatedAt: string | null;
  runId: string | null;
  runStatus: RunStatus | null;
}

// ---------------------------------------------------------------- mock interview (interview-prep mock.py)
export type MockStage = "mixed" | "hr" | "tech" | "hm";

export interface MockApp {
  jd: string;
  company: string;
  title: string;
  /** Prepared questions in 10_interview.json (asks excluded). */
  questions: number;
  sessions: MockSessionSummary[];
}

export interface MockSessionSummary {
  id: string;
  started: string;
  stage: MockStage;
  scored: number;
  total: number;
  average: number | null;
}

export interface MockChatMsg {
  role: "interviewer" | "candidate";
  text: string;
}

export interface MockAuto {
  words: number;
  seconds: number;
  concepts_hit: string[];
  concepts_missed: string[];
  unverified_numbers: string[];
  library_only_numbers: string[];
  gap_claims: string[];
  traps: string[];
  flags: string[];
  jargon?: string[];
}

export interface MockFramework {
  key: string;
  name: string;
  chunks: { label: string; goal: string }[];
}

export interface MockChunk {
  label: string;
  /** What the candidate said for this chunk (繁中), or that it was missing. */
  yours: string;
  /** One concrete fix (繁中). */
  tip: string;
  /** This chunk of the stronger answer (English). */
  better: string;
}

export type MockRoleKey = "hr" | "hm" | "tech";

export interface MockRole {
  key: MockRoleKey;
  name: string;
  zh: string;
  goal: string;
  listens_for: string;
  followups: string;
  rubric: Record<string, string>;
  feedback: string;
  better: string;
}

export interface MockItemView {
  n: number;
  /** Who asks and scores this question (HR / hiring manager / engineer). */
  role: { key: MockRoleKey; zh: string; name: string } | null;
  /** One-line tips for the same answer in front of the other two roles (after scoring). */
  otherRoles: { role: MockRoleKey; tip: string }[];
  /** Suggested answer frameworks (default first); a skeleton only, safe to show before answering. */
  frameworks: MockFramework[];
  /** Framework the scorer used, and the answer mapped onto its chunks (after scoring). */
  framework: string | null;
  chunks: MockChunk[] | null;
  question: string;
  kind: string;
  reported: boolean;
  status: "open" | "answering" | "scored" | "skipped";
  chat: MockChatMsg[];
  followupsAsked: number;
  auto: MockAuto | null;
  scores: Record<string, number> | null;
  overall: number | null;
  feedback: string;
  better: string;
  /** Only after the question is scored or skipped. */
  reference: string | null;
  scoreWarnings: string[];
}

export interface MockSessionView {
  id: string;
  jd: string;
  company: string;
  title: string;
  stage: MockStage;
  maxFollowups: number;
  started: string;
  rubric: Record<string, string>;
  items: MockItemView[];
  reportMd: string | null;
}
