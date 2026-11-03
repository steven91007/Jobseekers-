// Assemble the API payloads from the library DB and the application folder.
import { existsSync } from "node:fs";
import path from "node:path";
import type {
  ApplicationDetail,
  ApplicationRow,
  ConceptCounts,
  CvItem,
  KeywordRow,
  LibraryCoverage,
  PickRef,
} from "../shared/types.js";
import { parseCv } from "../shared/cvparse.js";
import { calculateKeywordMatch, countTerm, jobKeywordTerms, stripMd, type JobKeywords } from "../shared/match.js";
import { all, get, type AppRec, type KeywordRec, type PickRec } from "./db.js";
import { relToRepo, resolveAppDir } from "./config.js";
import { conceptCodeOf, conceptCountsByJd, conceptGroups, conceptRef, loadConceptTables } from "./concepts.js";
import { NOTION_ROOT_URL, applicationNotionUrl, attachLibrary, keywordLinks } from "./library.js";
import {
  atsSummary,
  findPdf,
  layoutSummary,
  mtime,
  readJson,
  readText,
  reviewSummary,
  type AtsRaw,
} from "./artifacts.js";

export function listApplicationRecs(): AppRec[] {
  return all<AppRec>("SELECT jd, company, title, dir, created, headline, country FROM applications");
}

export function getApplicationRec(jd: string): AppRec | undefined {
  return get<AppRec>("SELECT jd, company, title, dir, created, headline, country FROM applications WHERE jd = ?", jd);
}

export function appDir(rec: AppRec): string {
  return resolveAppDir(rec.dir);
}

export function applicationRow(rec: AppRec, counts?: Map<string, ConceptCounts>): ApplicationRow {
  const dir = appDir(rec);
  const ats = readJson<AtsRaw>(path.join(dir, "07_external_ats.json"));
  const layout = layoutSummary(readJson(path.join(dir, "06_layout.json")));
  const review = reviewSummary(readText(path.join(dir, "04_review.md")));
  return {
    jd: rec.jd,
    company: rec.company ?? rec.jd,
    title: rec.title ?? "",
    dir: relToRepo(dir),
    headline: rec.headline ?? "",
    country: rec.country ?? "",
    atsOverall: ats?.ats_score?.overall_score ?? null,
    keywordsFound: ats?.keyword_match ? ats.keyword_match.found.length : null,
    keywordsTotal: ats?.keyword_match?.n_terms ?? null,
    layoutStatus: layout.status,
    utilisation: layout.utilisation,
    hr: review.hr,
    hm: review.hm,
    verdict: review.verdict,
    date: rec.created ?? "",
    concepts: (counts ?? safeCounts()).get(rec.jd) ?? null,
  };
}

function safeCounts(): Map<string, ConceptCounts> {
  try {
    return conceptCountsByJd();
  } catch {
    return new Map(); // DB without the concept tables (pre-v3)
  }
}

export function listApplications(): ApplicationRow[] {
  const counts = safeCounts();
  return listApplicationRecs()
    .map((r) => applicationRow(r, counts))
    .sort((a, b) => (b.atsOverall ?? -1) - (a.atsOverall ?? -1));
}

// ---------------------------------------------------------------- library codes for CV items

const norm = (s: string | null | undefined) =>
  stripMd(s ?? "")
    .replace(/\s+/g, " ")
    .replace(/\s*—\s*$/, "")
    .trim()
    .toLowerCase();

interface BulletRec {
  code: string;
  project_code: string;
  status: string | null;
  body: string;
  conf: string | null;
}

/** Picks as the pick string `library_db.py picks` prints (scope=…;card=VI:0:0;bullet=VI1:0;…). */
export function pickString(picks: PickRec[]): string {
  return picks
    .map((p) => {
      if (p.kind === "card") return `card=${p.code}:${p.variant_ord ?? 0}:${p.context_ord ?? 0}`;
      if (p.kind === "scope") return `scope=${p.code}`;
      if (p.kind === "skill_row") return `skill_row=${p.code}:${p.variant_ord ?? 0}${p.items ? `=${p.items}` : ""}`;
      return `${p.kind}=${p.code}${p.variant_ord != null ? `:${p.variant_ord}` : ""}`;
    })
    .join(";");
}

export function attachPicks(items: CvItem[], picks: PickRec[]): void {
  const bullets = all<BulletRec>("SELECT code, project_code, status, body, conf FROM bullets");
  const leads = all<{ bullet_code: string; lead: string; ord: number }>("SELECT bullet_code, lead, ord FROM bullet_leads");
  const titles = all<{ project_code: string; title: string; ord: number }>("SELECT project_code, title, ord FROM project_titles");
  const contexts = all<{ project_code: string; text: string; conf: string | null; ord: number }>(
    "SELECT project_code, text, conf, ord FROM project_contexts",
  );
  const projects = new Map(all<{ code: string; status: string | null }>("SELECT code, status FROM projects").map((p) => [p.code, p]));
  const scopes = all<{ code: string; text: string; conf: string | null }>("SELECT code, text, conf FROM scope_lines");
  const rows = all<{ label: string; variants: string | null }>("SELECT label, variants FROM skill_rows");

  const byKind = (k: string) => picks.filter((p) => p.kind === k);
  const bulletPicks = byKind("bullet");
  const cardPicks = byKind("card");
  const rowPicks = byKind("skill_row");
  const used = new Set<PickRec>();
  let cardIdx = -1;
  let currentCard: string | null = null;

  for (const it of items) {
    let ref: PickRef | undefined;
    if (it.kind === "scope") {
      const s = scopes.find((x) => norm(x.text) === norm(it.text));
      const p = byKind("scope")[0];
      const code = s?.code ?? p?.code;
      if (code) ref = { code, variant: null, variantLabel: null, status: null, conf: s?.conf ?? null };
    } else if (it.kind === "card") {
      cardIdx++;
      const t = titles.find((x) => norm(x.title) === norm(it.title));
      const p = (t && cardPicks.find((c) => c.code === t.project_code)) ?? cardPicks[cardIdx];
      const code = t?.project_code ?? p?.code ?? null;
      currentCard = code;
      if (code) {
        const c = contexts.find((x) => x.project_code === code && norm(x.text) === norm(it.context));
        const titleOrd = t?.ord ?? p?.variant_ord ?? null;
        const ctxOrd = c?.ord ?? p?.context_ord ?? null;
        ref = {
          code,
          variant: titleOrd,
          variantLabel: `title ${titleOrd ?? "?"} · context ${ctxOrd ?? "?"}`,
          contextOrd: ctxOrd,
          status: projects.get(code)?.status ?? null,
          conf: c?.conf ?? null,
        };
      }
    } else if (it.kind === "bullet" || it.kind === "other_bullet") {
      const b = bullets.find((x) => norm(x.body) === norm(it.body));
      let p = b ? bulletPicks.find((x) => x.code === b.code && !used.has(x)) : undefined;
      if (!p && it.kind === "bullet") {
        p = bulletPicks.find((x) => !used.has(x) && norm(x.lead) === norm(it.lead));
        p ??= bulletPicks.find((x) => !used.has(x) && (!currentCard || x.code.startsWith(currentCard)));
      }
      if (p) used.add(p);
      const code = b?.code ?? p?.code;
      if (code) {
        const rec = b ?? bullets.find((x) => x.code === code);
        const leadOrd = leads.find((l) => l.bullet_code === code && norm(l.lead) === norm(it.lead))?.ord ?? p?.variant_ord ?? null;
        ref = {
          code,
          variant: leadOrd,
          variantLabel: leadOrd != null ? `lead ${leadOrd}` : null,
          status: rec?.status ?? null,
          conf: rec?.conf ?? null,
        };
      }
    } else if (it.kind === "skill_row") {
      const label = norm(it.label);
      const p =
        rowPicks.find((x) => norm(x.lead) === label) ??
        rowPicks.find((x) => norm(x.code) === label) ??
        rowPicks.find((x) => {
          const r = rows.find((r) => r.label === x.code);
          return (r?.variants ?? "").split("|").some((v) => norm(v) === label);
        });
      const lib = rows.find((r) => norm(r.label) === label || (r.variants ?? "").split("|").some((v) => norm(v) === label));
      const code = p?.code ?? lib?.label;
      if (code) {
        ref = {
          code,
          variant: p?.variant_ord ?? null,
          variantLabel: p ? `label ${p.variant_ord ?? 0}${p.items ? ` · items ${p.items}` : ""}` : null,
          status: null,
          conf: null,
        };
      }
    }
    if (ref) it.pick = ref;
  }
}

// ---------------------------------------------------------------- keyword coverage

/** Same grouping as ats_check.library_status(), applied to every JD term. */
export function libraryCoverage(terms: string[], jd: string): Map<string, LibraryCoverage> {
  const rows = new Map(all<KeywordRec>("SELECT term, type, coverage, notes FROM keywords").map((r) => [r.term.toLowerCase(), r]));
  const kb = all<{ term: string; bullet_code: string }>("SELECT term, bullet_code FROM keyword_bullets");
  const ks = all<{ term: string; skill_name: string }>("SELECT term, skill_name FROM keyword_skills");
  const kj = all<{ term: string; jd: string }>("SELECT term, jd FROM keyword_jds");
  const links = keywordLinks();
  const out = new Map<string, LibraryCoverage>();
  for (const term of terms) {
    const k = rows.get(term.toLowerCase());
    const cov = (k?.coverage ?? "").toLowerCase();
    const notes = k?.notes ?? "";
    const bullets = k ? kb.filter((r) => r.term === k.term).map((r) => r.bullet_code) : [];
    const skills = k ? ks.filter((r) => r.term === k.term).map((r) => r.skill_name) : [];
    const otherJds = k ? [...new Set(kj.filter((r) => r.term === k.term && r.jd !== jd).map((r) => r.jd))] : [];
    let group: LibraryCoverage["group"];
    if (!k || notes.startsWith("auto: unclassified")) group = "unclassified";
    else if (cov === "covered") group = "covered elsewhere";
    else if (["sot-supported", "sot supported", "supported"].includes(cov)) group = "SoT-supported wording";
    else if (cov === "gap") group = "gap";
    else if (cov === "do not claim") group = "do not claim";
    else group = "unclassified";
    const l = links.get(term.toLowerCase());
    out.set(term, {
      coverage: k?.coverage ?? null,
      group,
      notes,
      bullets,
      skills,
      otherJds,
      notionUrl: l?.notionUrl ?? null,
      bulletRefs: l?.bulletRefs ?? [],
      skillRefs: l?.skillRefs ?? [],
    });
  }
  return out;
}

// ---------------------------------------------------------------- detail

export function applicationDetail(rec: AppRec): ApplicationDetail {
  const dir = appDir(rec);
  const cvFile = path.join(dir, "03_cv.md");
  const md = readText(cvFile) ?? "";
  const job = readJson<JobKeywords>(path.join(dir, "07_job_keywords.json")) ?? {};
  const atsRaw = readJson<AtsRaw>(path.join(dir, "07_external_ats.json"));
  const terms = jobKeywordTerms(job);
  const termNames = terms.map((t) => t.term);

  const lines = parseCv(md, termNames);
  const picks = all<PickRec>("SELECT * FROM application_picks WHERE jd = ? ORDER BY position", rec.jd);
  attachPicks(lines, picks);
  attachLibrary(lines);

  const byItem: Record<string, string[]> = {};
  const byTerm: Record<string, string[]> = Object.fromEntries(termNames.map((t) => [t, [] as string[]]));
  for (const it of lines) {
    if (!it.hits.length) continue;
    byItem[it.id] = it.hits;
    for (const t of it.hits) byTerm[t].push(it.id);
  }

  const match = calculateKeywordMatch(md, job);
  const recordedFound = new Set(atsRaw?.keyword_match?.found ?? match.found);
  const repeated = new Map((atsRaw?.repeated_terms ?? []).map((r) => [r.term, r.jd_count]));
  const jdText = readText(path.join(dir, "jd.md")) ?? "";
  const coverage = libraryCoverage(termNames, rec.jd);

  const ct = loadConceptTables();
  const keywords: KeywordRow[] = terms.map(({ term, type }) => ({
    term,
    type,
    found: recordedFound.has(term),
    jdCount: repeated.get(term) ?? (jdText ? countTerm(term, jdText) : 0),
    library: coverage.get(term)!,
    items: byTerm[term] ?? [],
    concept: conceptRef(ct, conceptCodeOf(ct, term)),
  }));
  const concepts = conceptGroups(ct, keywords.map((k) => ({ term: k.term, type: k.type, found: k.found })));

  const pdf = findPdf(dir);
  return {
    row: applicationRow(rec),
    cv: { lines, text: md, mtime: mtime(cvFile) },
    keywords,
    concepts,
    hits: { byItem, byTerm },
    matcherFound: match.found,
    ats: atsSummary(atsRaw),
    layout: layoutSummary(readJson(path.join(dir, "06_layout.json"))),
    review: reviewSummary(readText(path.join(dir, "04_review.md"))),
    jdBrief: readText(path.join(dir, "01_jd.md")) ?? "",
    pdfUrl: pdf && existsSync(pdf) ? `/api/applications/${encodeURIComponent(rec.jd)}/pdf` : null,
    picks: pickString(picks),
    notion: { applicationUrl: applicationNotionUrl(rec.jd), rootUrl: NOTION_ROOT_URL },
  };
}
