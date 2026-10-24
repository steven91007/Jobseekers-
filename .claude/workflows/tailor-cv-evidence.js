export const meta = {
  name: 'tailor-cv-evidence',
  description: 'tailor-cv Phase 2: coordinator Opus maps Notion projects, one Opus investigator per project reads its records itself, coordinator writes the evidence map + draft',
  whenToUse: 'Phase 2 of /tailor-cv. args: {dir: "cv/applications/<folder>", maxRecordsPerProject?: 25}',
  phases: [
    { title: 'Inventory', detail: 'coordinator Opus lists the candidate\'s Notion projects relevant to the JD', model: 'opus' },
    { title: 'Investigate', detail: 'per project: one Opus reads its records and synthesises (no delegation)', model: 'opus' },
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

const NOTION_HINT = 'Find Notion pages and databases with the /notion find skill (Skill tool, skill "Notion:find", ' +
  'args = title keywords), then read them with the Notion fetch tool (load it with ToolSearch, query "notion fetch"). ' +
  'Use the raw Notion search tool only if /notion find returns nothing, and say so. ' +
  'If Notion is unavailable, stop and say NOTION_UNAVAILABLE.'

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
// One Opus investigator per project; it reads its records itself (no delegation).
const findings = await pipeline(
  projects,
  (p) => agent(
    `You are the investigator for the project "${p.name}" (hub: ${p.hub_url}) in the tailor-cv pipeline. ` +
    `Do the research yourself — do not delegate to other agents. Read ${APP}/01_jd_analysis.md for the ` +
    `requirements, ${SKILL}/references/evidence_rules.md (claim statuses incl. Template, measurement-integrity ` +
    `rules, reader test, placement implies causation) and ${ROOT}/cv/profile/ (candidate confirmations = ` +
    `User-reported). ${NOTION_HINT} ` +
    `Explore the project hub and its child pages/databases (commits, topics, decisions/ADRs, releases, benchmarks, ` +
    `notes). Pick the records most likely to evidence these requirements: ${(p.requirement_ids || []).join(', ')} ` +
    `(${p.relevance}) — at most ${MAX_RECORDS}; note how many exist and what you left out. Read each selected record ` +
    `and, per fact, keep a verbatim quote (≤ 200 chars, original language), numbers with baseline / sample / ` +
    `conditions / environment, executed vs configured, and the environment. Then synthesise the project into ` +
    `conservative, CV-ready candidate claims with a status, source URLs and measurement scope; resolve conflicts; ` +
    `list gaps and questions only the candidate can answer. Never upgrade "configured" to "executed" or "staging" ` +
    `to "production" without a record or a candidate confirmation.`,
    { label: `investigator: ${p.name}`, phase: 'Investigate', model: 'opus', schema: PROJECT_FINDINGS, agentType: 'general-purpose' }),
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
