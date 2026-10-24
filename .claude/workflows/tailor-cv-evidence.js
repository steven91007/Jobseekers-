export const meta = {
  name: 'tailor-cv-evidence',
  description: 'tailor-cv Phase 2: coordinator Opus maps Notion projects, one Opus investigator per project, one Haiku reader per record, coordinator writes the evidence map + draft',
  whenToUse: 'Phase 2 of /tailor-cv. args: {dir: "cv/applications/<folder>", maxRecordsPerProject?: 25}',
  phases: [
    { title: 'Inventory', detail: 'coordinator Opus lists the candidate\'s Notion projects relevant to the JD', model: 'opus' },
    { title: 'Investigate', detail: 'per project: Opus picks records → one Haiku per record → Opus synthesises', model: 'opus' },
    { title: 'Synthesize', detail: 'coordinator Opus writes 02_evidence_map.md, 03_cv.md draft, 07_improvement_log.md', model: 'opus' },
  ],
}

// ---------------------------------------------------------------- inputs ---
const DIR = (args && args.dir) || ''
if (!DIR) throw new Error('args.dir is required, e.g. {dir: "cv/applications/20261024_Scout24_AI-Automation-Engineer"}')
const MAX_RECORDS = (args && args.maxRecordsPerProject) || 25
const ROOT = '/mnt/e/PythonProject/LinkedinJobSearcher'
const SKILL = `${ROOT}/.claude/skills/tailor-cv`
const APP = `${ROOT}/${DIR}`

const NOTION_HINT = 'Use the Notion MCP tools (load them with ToolSearch, e.g. query "notion search fetch"). ' +
  'If no Notion tool is available, stop and say NOTION_UNAVAILABLE.'

// --------------------------------------------------------------- schemas ---
const PROJECTS = {
  type: 'object',
  properties: {
    notion_available: { type: 'boolean' },
    projects: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string', description: 'short project name, e.g. "inferenceapi"' },
          hub_url: { type: 'string', description: 'Notion URL of the project KB hub or main page' },
          employer: { type: 'string', description: 'employer the work belongs to, if known (e.g. Delta Electronics)' },
          relevance: { type: 'string', description: 'one line: why this project matters for this JD' },
          requirement_ids: { type: 'array', items: { type: 'string' }, description: 'R# from 01_jd_analysis.md it can evidence' },
        },
        required: ['name', 'hub_url', 'relevance', 'requirement_ids'],
      },
    },
    skipped: { type: 'array', items: { type: 'string' }, description: 'projects seen but judged irrelevant, with reason' },
  },
  required: ['notion_available', 'projects'],
}

const RECORD_PLAN = {
  type: 'object',
  properties: {
    records: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          url: { type: 'string' },
          title: { type: 'string' },
          why: { type: 'string', description: 'what this record should establish' },
          requirement_ids: { type: 'array', items: { type: 'string' } },
        },
        required: ['url', 'title', 'why'],
      },
    },
    total_records_seen: { type: 'number', description: 'how many records the project has in total (approx.)' },
    not_selected_note: { type: 'string', description: 'what kinds of records were left out and why' },
  },
  required: ['records', 'total_records_seen'],
}

const RECORD_NOTE = {
  type: 'object',
  properties: {
    url: { type: 'string' },
    title: { type: 'string' },
    date: { type: 'string', description: 'date of the record if shown, else "unknown"' },
    author: { type: 'string', description: 'author / committer if shown, else "unknown"' },
    facts: {
      type: 'array',
      description: 'concrete facts relevant to the requirements; empty if none',
      items: {
        type: 'object',
        properties: {
          fact: { type: 'string', description: 'one sentence, English' },
          quote: { type: 'string', description: 'verbatim excerpt ≤ 200 chars in the original language' },
          numbers: { type: 'string', description: 'any number with its baseline, sample size, conditions, environment; "" if none' },
          execution: { type: 'string', enum: ['executed', 'configured-only', 'planned', 'n/a', 'unclear'], description: 'did it actually run / ship, or is it only configured/planned?' },
          environment: { type: 'string', enum: ['production', 'staging', 'dev/local', 'n/a', 'unclear'] },
          requirement_ids: { type: 'array', items: { type: 'string' } },
        },
        required: ['fact', 'quote', 'execution', 'environment'],
      },
    },
    caveats: { type: 'string', description: 'anything that limits the facts (unfinished, reverted, someone else did it, small sample)' },
  },
  required: ['url', 'title', 'facts'],
}

const PROJECT_FINDINGS = {
  type: 'object',
  properties: {
    project: { type: 'string' },
    summary: { type: 'string', description: '3–5 lines: what the project is, stack, the candidate\'s role, outcomes' },
    candidate_claims: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          claim: { type: 'string', description: 'CV-ready claim wording, conservative' },
          status: { type: 'string', enum: ['Verified', 'User-reported', 'Inferred', 'Missing Evidence', 'Contradicted'] },
          source_urls: { type: 'array', items: { type: 'string' } },
          measurement_scope: { type: 'string', description: 'baseline · sample · conditions · environment; "unknown" where unknown' },
          requirement_ids: { type: 'array', items: { type: 'string' } },
          integrity_notes: { type: 'string', description: 'component≠pipeline, similar≠identical, configured≠executed, staging≠production, small sample, % vs points, causation' },
        },
        required: ['claim', 'status', 'source_urls', 'measurement_scope'],
      },
    },
    conflicts: { type: 'array', items: { type: 'string' }, description: 'records that disagree, and how resolved' },
    gaps: { type: 'array', items: { type: 'string' }, description: 'requirements this project cannot evidence' },
    questions_for_candidate: { type: 'array', items: { type: 'string' } },
  },
  required: ['project', 'summary', 'candidate_claims'],
}

const SYNTHESIS = {
  type: 'object',
  properties: {
    files_written: { type: 'array', items: { type: 'string' } },
    bullet_leads: { type: 'array', items: { type: 'string' } },
    must_haves_covered: { type: 'array', items: { type: 'string' } },
    honest_gaps: { type: 'array', items: { type: 'string' } },
    weak_claims: { type: 'array', items: { type: 'string' }, description: 'claims not better than Inferred / Missing Evidence' },
    questions_for_candidate: { type: 'array', items: { type: 'string' } },
    ledger_check: { type: 'string', description: 'output summary of review_tools.py ledger --cv' },
  },
  required: ['files_written', 'bullet_leads', 'ledger_check'],
}

// ------------------------------------------------------------- Inventory ---
phase('Inventory')
const inventory = await agent(
  `You are the coordinator (Phase 2) of the tailor-cv pipeline. Read ${APP}/01_jd_analysis.md ` +
  `(requirements R#, top-5 selection criteria, archetype), ${ROOT}/cv/template/CV_template.md and ` +
  `everything in ${ROOT}/cv/profile/. ${NOTION_HINT} ` +
  `Map the candidate's projects in Notion: search for project knowledge bases / hubs (KB pages with Commits / ` +
  `Topics / Decisions children, project pages) — use several searches (project names, "KB", employer names, ` +
  `技術, 專案, the JD's key technologies). Return every distinct project that can evidence at least one R#, with ` +
  `its hub URL and the R# it can support. Do not read individual commits yet — investigators do that.`,
  { label: 'coordinator: inventory', phase: 'Inventory', model: 'opus', schema: PROJECTS, agentType: 'general-purpose' })

if (!inventory || !inventory.notion_available) {
  throw new Error('Notion is not reachable from workflow agents — reconnect Notion (/mcp) and re-run.')
}
const projects = inventory.projects
log(`Inventory: ${projects.length} project(s) — ${projects.map(p => p.name).join(', ')}` +
  (inventory.skipped && inventory.skipped.length ? ` · skipped: ${inventory.skipped.length}` : ''))

// ----------------------------------------------------------- Investigate ---
// One Opus investigator per project. The investigator picks the records; the script
// dispatches one Haiku reader per record (subagents cannot spawn subagents, so the
// script performs the dispatch the investigator asks for); the investigator then
// synthesises its project from the readers' notes.
const findings = await pipeline(
  projects,

  // stage 1 — investigator plans which records to read
  (p) => agent(
    `You are the investigator for the project "${p.name}" (hub: ${p.hub_url}) in the tailor-cv pipeline. ` +
    `Read ${APP}/01_jd_analysis.md for the requirements. ${NOTION_HINT} ` +
    `Explore the project hub and its child pages/databases (commits, topics, decisions/ADRs, releases, benchmarks, ` +
    `notes). Choose the records most likely to evidence these requirements: ${(p.requirement_ids || []).join(', ')} ` +
    `(${p.relevance}). Prefer records with numbers, decisions, releases, evaluations, deployments, incidents and ` +
    `authorship. Select at most ${MAX_RECORDS} records; say what you left out. Return their URLs — do not read ` +
    `them in depth yourself; one reader per record will do that.`,
    { label: `investigator: ${p.name} (plan)`, phase: 'Investigate', model: 'opus', schema: RECORD_PLAN, agentType: 'general-purpose' }),

  // stage 2 — one Haiku reader per record, each reads exactly one record
  async (plan, p) => {
    if (!plan || !plan.records) return { plan, notes: [] }
    if (plan.total_records_seen > plan.records.length) {
      log(`${p.name}: reading ${plan.records.length} of ~${plan.total_records_seen} records — ${plan.not_selected_note || 'rest judged less relevant'}`)
    }
    const notes = await parallel(plan.records.map((r, i) => () => agent(
      `Read exactly ONE Notion record and nothing else: ${r.url} ("${r.title}"). ${NOTION_HINT} ` +
      `Fetch only this page (do not follow links, do not search). Extract the facts relevant to: ${r.why} ` +
      `(requirements ${(r.requirement_ids || []).join(', ') || 'see why'}). For every number keep its baseline, ` +
      `sample size, conditions and environment. Mark whether each thing was actually executed / shipped or only ` +
      `configured / planned, and in which environment. Quote the original text (≤ 200 chars, original language). ` +
      `Do not infer beyond the page; if the page does not show something, say "unknown".`,
      { label: `reader: ${p.name} #${i + 1}`, phase: 'Investigate', model: 'haiku', effort: 'low', schema: RECORD_NOTE })))
    const ok = notes.filter(Boolean)
    log(`${p.name}: ${ok.length}/${plan.records.length} record(s) read by Haiku readers`)
    return { plan, notes: ok }
  },

  // stage 3 — investigator synthesises the project from the readers' notes
  ({ plan, notes }, p) => agent(
    `You are the investigator for the project "${p.name}". Your readers each read one Notion record and returned ` +
    `these notes (JSON):\n${JSON.stringify(notes)}\n\n` +
    `Read ${SKILL}/references/evidence_rules.md (claim statuses incl. Template, measurement-integrity rules, reader ` +
    `test, placement implies causation) and ${ROOT}/cv/profile/ (candidate confirmations = User-reported). ` +
    `Synthesise the project: turn the notes into conservative, CV-ready candidate claims with a status, source URLs ` +
    `and measurement scope; resolve conflicts between records (you may fetch a page yourself to settle a conflict — ` +
    `${NOTION_HINT}); list gaps and questions only the candidate can answer. Never upgrade "configured" to ` +
    `"executed" or "staging" to "production" without a record or a candidate confirmation.`,
    { label: `investigator: ${p.name} (synthesis)`, phase: 'Investigate', model: 'opus', schema: PROJECT_FINDINGS, agentType: 'general-purpose' }),
)

const done = findings.filter(Boolean)
const failed = projects.filter((p, i) => !findings[i]).map(p => p.name)
if (failed.length) log(`Investigation failed for: ${failed.join(', ')} — the coordinator is told so`)

// ------------------------------------------------------------ Synthesize ---
phase('Synthesize')
const synthesis = await agent(
  `You are the coordinator (Phase 2) of the tailor-cv pipeline. Your investigators each covered one Notion ` +
  `project; their findings (JSON):\n${JSON.stringify(done)}\n` +
  (failed.length ? `Projects whose investigation failed (cover them yourself if they matter): ${failed.join(', ')}.\n` : '') +
  `\nNow do Phase 2 Steps A–C exactly as "Phase 2 — Evidence ledger + draft" in ${SKILL}/SKILL.md describes, using ` +
  `these findings as your evidence (you may fetch a cited Notion page to confirm a high-severity claim). Read ` +
  `${APP}/01_jd_analysis.md, ${ROOT}/cv/template/CV_template.md, ${ROOT}/cv/profile/ (copy candidate ` +
  `confirmations verbatim into the evidence map), ${SKILL}/references/evidence_rules.md and ` +
  `${SKILL}/references/cv_writing_rules.md.\n` +
  `- Write ${APP}/02_evidence_map.md (Requirement map, Claim ledger with Measurement scope, Candidate confirmations, ` +
  `Honest gaps, Honest gaps / suggestions, Reusable metrics, Project fact sheet — one entry per investigated project).\n` +
  `- Write ${APP}/03_cv.md from a byte-for-byte copy of the template (overwrite any existing draft; keep CRLF if the ` +
  `template has it): rewrite only the Delta bullets (max 5, ordered by the JD's top-5 selection criteria) and the 6 ` +
  `Skills rows; leave the Profile as template text.\n` +
  `- Write ${APP}/07_improvement_log.md.\n` +
  `- Run: python3 -I ${SKILL}/scripts/review_tools.py ledger --evidence ${APP}/02_evidence_map.md --cv ${APP}/03_cv.md ` +
  `and fix the ledger until it reports no errors.`,
  { label: 'coordinator: synthesis', phase: 'Synthesize', model: 'opus', schema: SYNTHESIS, agentType: 'general-purpose' })

return {
  projects: projects.map(p => p.name),
  investigations: done.map(f => ({ project: f.project, claims: (f.candidate_claims || []).length, gaps: (f.gaps || []).length })),
  failed,
  synthesis,
}
