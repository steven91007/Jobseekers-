---
name: company-research
description: Research the company behind one application — what they do, size, HQ and the JD's office, the product and team for this role, tech stack (confirmed vs inferred), dated news from the last 12 months, risks/unknowns, 3–5 why-this-company hooks each tied to a library concept and project card, and questions to ask — every fact with a source URL and access date. Writes $D/02_company.json + 02_company.md, validated by a script (sources, news dates, hook codes, no candidate claim beyond the library). Feeds /interview-prep (motivation, ask), /outreach and the CV Summary. Use when the user says "company research", "research <company>", "公司研究", "查公司", or before /interview-prep or /outreach when 02_company.md is missing.
---

# /company-research

**Glossary** — `$D` the job's tailor-cv dir (`$DB status` → Dir; holds `jd.md`, `01_jd.md`, `03_cv.md`,
`07_job_keywords.json`) · `$DB` `python3 .claude/skills/tailor-cv/scripts/library_db.py` · `$CR`
`python3 SKILL_DIR/scripts/company_research.py` · `$CONF` `cv/profile/candidate_confirmations.md` · `$RULES`
`.claude/skills/tailor-cv/references/writing_rules.md`. `cv/` is gitignored — never commit. The DB is read-only here.

**Principles** — two kinds of fact, kept apart. **Company facts** come from the web and each carries a source id
(`S#` → URL + access date); no source, no fact. **Candidate facts** appear only in a hook's `candidate_angle` and come
only from the cited bullets (truth-checked like `/interview-prep`). A gap concept is never a hook; it goes in
`gap_note`, stated plainly. An unknown is written as an unknown (with where you looked), never filled with a guess.
Prefer the company's own words (press release, JD, results release) over trade press; trade press alone makes a tech
item `inferred`. Never collect private data on people: name only leaders quoted in public releases.

## Steps
| # | Step | Owner | Output |
|---|---|---|---|
| 0 | Resolve the application: `$DB status` → JD code, `$D`; or a company + JD file with no row | Opus | `$D` |
| 1 | `$CR init --jd <Co>` (or `--company "<Name>" --jd-file <f> --out <dir>`) | script | `$D/02_company.json` skeleton |
| 2 | Research (checklist in `references/research_checklist.md`) | main agent: WebSearch + `web-fetch` agent (curl fallback) | notes with URL + date |
| 3 | Fill the JSON: sources, snapshot, product_team, tech_stack, news, hooks, risks, questions | Opus | `02_company.json` |
| 4 | `$CR check $D/02_company.json` → fix until `RESULT: OK` | script | exit 0 |
| 5 | `$CR render $D/02_company.json` → report | script | `$D/02_company.md` |

**1 · Init** — writes the skeleton with `_help` (field formats), `jd_brief` (01_jd.md + jd.md meta), `jd_concepts`
(from `$DB match 07_job_keywords.json`: level req/pref/kw, coverage, terms, bullets) and `candidate_cards` (every card
with its bullets, their concept codes, and `picked` = on this CV). The JD itself is pre-filled as source `S1`
(accessed = the JD's capture date). Refuses to overwrite an existing file without `--force`.

**2 · Research** — the main agent fetches; the script never touches the web. Cover, in this order: the JD's team and
product (the posting, sibling postings at the same site) · company site (about, product pages, media center) · results
releases (last 2 quarters: revenue, division trend, cost programmes) · news and trade press of the last 12 months ·
tech-stack signals (JD, sibling postings, engineering blog, GitHub org via `api.github.com/orgs/<org>/repos`) · the
JD city's office (size, test centre, opening dates) · hiring-process signals (posting text, Glassdoor/kununu only as
`review-site`). Use `web-fetch` agents in parallel; if they return nothing, `curl -sL -A "<browser UA>"` into a fresh
scratchpad dir and strip HTML with a script kept outside that dir (`python3 -I`). Take dates from the page's
`datePublished` / `article:published_time`, not from search snippets. A figure seen only in a search snippet is not
a fact: verify it on the page or drop it (say so in `notes`).

**3 · Fill** — formats (also in `_help`):

| Section | Item | Rules |
|---|---|---|
| `sources` | `id S#, url, title, publisher, published, accessed, kind` | kind ∈ company-site, press-release, financial-report, news, trade-press, jd, job-post, blog, github, review-site, registry, other |
| `snapshot` | `field, text, src` | fields what, size, hq, office required (+ parent, revenue, other); `office` names `office_city` |
| `product_team` | `text, src` | the JD's team/product, named teams, sibling postings, products the role feeds |
| `tech_stack` | `tech, status, evidence, src` | `confirmed` = in use, per a company-owned source (site, press release, job post, blog, GitHub) · `jd-listed` = named in a JD/job post as a requirement or wish ("e.g.", "knowledge of") · `inferred` = anything else, with why |
| `news` | `date, headline, text, src` | date within 12 months of `researched`, newest first in render |
| `hooks` | `hook, use, company_fact, src, concepts, cards, bullets, candidate_angle, gap_note, notes` | 3–5. Concepts from `jd_concepts` (covered); bullets **on the submitted CV** (the recorded picks); `candidate_angle` = the cited bullets' facts, same numbers and units, `$RULES` qualifiers. `use: all` (default) or `interview-only`; a hook citing any bullet not on the CV must be `interview-only` and goes last |
| `risks` | `kind risk/unknown, text, src, searched` | division decline, cost programmes, stale posting text, missing stack details, language |
| `questions` | `question, why, concepts, src` | 3–6, end in `?`; `why` = what to listen for; scope/gap questions first |

Hook recipe: one company fact the hiring manager cares about (product, team split, platform direction) → the concept
it touches → the CV bullet that already proves it. One hook per picked card at least. A library bullet the CV does not
show (another card, or an unpicked bullet of a picked card) is allowed only with `use: interview-only`: the interviewer
may ask about it, but outreach, the CV and the letter must not lean on it.

**4 · Check** (exit 1 on error) — sources: id format, http(s) URL, access date YYYY-MM-DD not in the future (warn if
> 90 days before research), kind; every snapshot/product/tech/news/hook item cites existing source ids; risks of kind
`unknown` may have none but need `searched`; snapshot has what/size/hq/office; `confirmed` tech needs a company-owned
source, `jd-listed` a jd/job-post source; news dated and ≤ 365 days old, not after `researched` (warn when > 45 days from the source's published date);
hooks 3–5, concept/card/bullet codes exist, each bullet belongs to a cited card, `use` ∈ all/interview-only and a
bullet not among the application's recorded picks forces `interview-only` (error otherwise), no gap / do-not-claim concept, no gap or
do-not-claim term from `$DB gaps --jd` in `candidate_angle` (e.g. Jetson, TensorRT, cloud, React), MaterialCheck (card MC)
never production/live/deployed, **truth check** on every number in `candidate_angle` (shared `interview-prep/scripts/truth.py`:
same value and unit in the cited bullets, their card, or the CV line carrying the bullet; CONF items only if named in
`notes`); warns for a concept outside the JD's matched concepts, an unpicked card, a legacy bullet, a bullet that is not
library evidence for the cited concepts, a picked card with no hook, unused sources. Questions 3–6. Self-test: `$CR check --selftest`
(20 mutation cases, 4 of them on the TOMRA application).

**5 · Render + report** — `02_company.md` sections in fixed order: Snapshot · Product & team for this role · Tech stack
(confirmed, then jd-listed, then inferred) · Recent news · Why-this-company hooks (`use: all` first; concept names, cards, evidence codes, honest gap) · Risks /
unknowns · Questions to ask · Sources (published, accessed, URL). Report in ≤ 10 lines: counts per section, check result,
the hooks (concept → card/bullets), the top risk, file paths.

## Integrations (read-only contract)
Python: `sys.path.insert(0, ".claude/skills/company-research/scripts")`; both return `''` when no `02_company.json` exists.
- `company_text(jd)` — **the only company corpus other skills may use as evidence** (number/truth checks): snapshot,
  product_team, news and each hook's `company_fact`. It never contains `candidate_angle`, `gap_note` or other candidate text,
  so a company corpus can never launder a candidate claim.
- `excerpt(jd, n=4500)` / `$CR excerpt --jd <Co> [--max N]` — context-pack block: what/size/office, hooks (`use: all` first;
  each line shows `use:`, `on CV: yes/no`, concepts, bullets, `gap_note`, `notes`), questions, 3 newest news.
Other skills read these or `02_company.md`, never edit them. `use: interview-only` hooks are for /interview-prep only.

- **/interview-prep** — `motivation` answers ("why TOMRA", "why this team") use the hooks: the company fact from the hook,
  the candidate half from its cited bullets (list them in `bullets`). `ask` items start from `questions` (keep the `why` as
  "what to listen for"). Company figures (revenue, headcount, stake %) may appear in motivation/ask answers because
  `company_text(jd)` joins their truth corpus.
- **/outreach** — a connect note's "one concrete hook" and a referral letter's closing line may use one `use: all`
  hook's company fact; `company_text` joins the number check only, dangerous-upgrade checks stay on the CV/library corpus.
- **/tailor-cv Summary and /cover-letter** — the company/industry sentence may reuse one hook's company fact, cited from
  `02_company.md`; nothing here changes the CV picks.
- **/apply (step 1)** — run steps 1–5 before the JD brief; a missing `02_company.json` is not a blocker for later steps.
