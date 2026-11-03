// Concept layer (library_db_spec_v3): term → concept → evidence. Read-only SQL, plus the
// `align` / `gaps` CLI parsers and the "Concept context" prompt block.
import { existsSync, statSync } from "node:fs";
import path from "node:path";
import type {
  AlignResult,
  AlignRow,
  ClaudeTarget,
  ConceptCounts,
  ConceptEvidence,
  ConceptGroup,
  ConceptJdTerm,
  ConceptRef,
  GapConcept,
  GapGroupId,
  GapsResult,
  KeywordType,
  SotLeadsResult,
} from "../shared/types.js";
import { jobKeywordTerms, type JobKeywords } from "../shared/match.js";
import { readJson } from "./artifacts.js";
import { DB_PATH } from "./config.js";
import { all } from "./db.js";
import { libraryDb } from "./libcli.js";
import { notionUrl } from "./library.js";

export const WEIGHT: Record<KeywordType, number> = { required: 3, preferred: 2, keyword: 1 };

interface ConceptRec {
  code: string;
  name: string;
  kind: string;
  claim: string;
  definition: string | null;
  notes: string | null;
  notion_id: string | null;
}

interface EvidenceRec {
  concept_code: string;
  kind: string;
  code: string;
  strength: string;
  note: string | null;
}

export interface ConceptTables {
  concepts: Map<string, ConceptRec>;
  /** lower(term) → concept_code from the keyword map. */
  termConcept: Map<string, string | null>;
  /** lower(alias) → concept_code. */
  aliasConcept: Map<string, string>;
  aliases: Map<string, string[]>;
  evidence: Map<string, EvidenceRec[]>;
  relations: { src: string; rel: string; dst: string }[];
  bullets: Map<string, { project_code: string; notion_id: string | null }>;
  projects: Map<string, string | null>;
  skills: Map<string, string | null>;
}

function push<K, V>(m: Map<K, V[]>, k: K, v: V): void {
  const list = m.get(k);
  if (list) list.push(v);
  else m.set(k, [v]);
}

export function loadConceptTables(): ConceptTables {
  const aliases = new Map<string, string[]>();
  const aliasConcept = new Map<string, string>();
  for (const a of all<{ concept_code: string; alias: string }>("SELECT concept_code, alias FROM concept_aliases ORDER BY alias COLLATE NOCASE")) {
    push(aliases, a.concept_code, a.alias);
    aliasConcept.set(a.alias.toLowerCase(), a.concept_code);
  }
  const evidence = new Map<string, EvidenceRec[]>();
  for (const e of all<EvidenceRec>("SELECT concept_code, kind, code, strength, note FROM concept_evidence ORDER BY rowid")) push(evidence, e.concept_code, e);
  return {
    concepts: new Map(
      all<ConceptRec>("SELECT code, name, kind, claim, definition, notes, notion_id FROM concepts").map((c) => [c.code, c]),
    ),
    termConcept: new Map(
      all<{ term: string; concept_code: string | null }>("SELECT term, concept_code FROM keywords").map((k) => [k.term.toLowerCase(), k.concept_code]),
    ),
    aliasConcept,
    aliases,
    evidence,
    relations: all("SELECT src, rel, dst FROM concept_relations ORDER BY rowid"),
    bullets: new Map(
      all<{ code: string; project_code: string; notion_id: string | null }>("SELECT code, project_code, notion_id FROM bullets").map((b) => [b.code, b]),
    ),
    projects: new Map(all<{ code: string; notion_id: string | null }>("SELECT code, notion_id FROM projects").map((p) => [p.code, p.notion_id])),
    skills: new Map(all<{ name: string; notion_id: string | null }>("SELECT name, notion_id FROM skills").map((s) => [s.name.toLowerCase(), s.notion_id])),
  };
}

/** keywords.concept_code first, then an exact alias (NOCASE). */
export function conceptCodeOf(t: ConceptTables, term: string): string | null {
  const k = term.toLowerCase();
  const code = t.termConcept.get(k) ?? t.aliasConcept.get(k) ?? null;
  return code && t.concepts.has(code) ? code : null;
}

export function conceptRef(t: ConceptTables, code: string | null): ConceptRef | null {
  const c = code ? t.concepts.get(code) : undefined;
  return c ? { code: c.code, name: c.name, claim: c.claim, kind: c.kind } : null;
}

export function evidenceLink(t: ConceptTables, kind: string, code: string): Pick<ConceptEvidence, "notionUrl" | "projectCode"> {
  if (kind === "bullet" || kind === "lead") {
    const b = t.bullets.get(code);
    return { notionUrl: notionUrl(b?.notion_id) ?? notionUrl(b ? t.projects.get(b.project_code) : null), projectCode: b?.project_code };
  }
  if (kind === "context") return { notionUrl: notionUrl(t.projects.get(code)), projectCode: code };
  if (kind === "skill") return { notionUrl: notionUrl(t.skills.get(code.toLowerCase())) };
  return { notionUrl: null };
}

function evidenceOf(t: ConceptTables, code: string): ConceptEvidence[] {
  return (t.evidence.get(code) ?? []).map((e) => ({
    kind: e.kind,
    code: e.code,
    strength: e.strength,
    ...(e.note ? { note: e.note } : {}),
    ...evidenceLink(t, e.kind, e.code),
  }));
}

/** Concepts the JD's terms resolve to, heaviest first (weight, then name). */
export function conceptGroups(t: ConceptTables, terms: ConceptJdTerm[]): ConceptGroup[] {
  const byCode = new Map<string, ConceptJdTerm[]>();
  for (const term of terms) {
    const code = conceptCodeOf(t, term.term);
    if (code) push(byCode, code, term);
  }
  const out: ConceptGroup[] = [];
  for (const [code, jdTerms] of byCode) {
    const c = t.concepts.get(code)!;
    out.push({
      code,
      name: c.name,
      kind: c.kind,
      claim: c.claim,
      weight: Math.max(...jdTerms.map((j) => WEIGHT[j.type] ?? 1)),
      definition: c.definition ?? "",
      notes: c.notes ?? "",
      notionUrl: notionUrl(c.notion_id),
      jdTerms,
      evidence: evidenceOf(t, code),
      aliases: t.aliases.get(code) ?? [],
      relations: t.relations.filter((r) => r.src === code || r.dst === code),
    });
  }
  return out.sort((a, b) => b.weight - a.weight || a.name.localeCompare(b.name));
}

/** Per JD: how many of its concepts are covered / SoT-supported / gap / do not claim (SQL only). */
export function conceptCountsByJd(): Map<string, ConceptCounts> {
  const rows = all<{ jd: string; term: string; concept_code: string | null; claim: string | null }>(
    `SELECT kj.jd, kj.term, k.concept_code, c.claim
       FROM keyword_jds kj
       LEFT JOIN keywords k ON lower(k.term) = lower(kj.term)
       LEFT JOIN concepts c ON c.code = k.concept_code`,
  );
  const seen = new Map<string, Map<string, string>>();
  const unresolved = new Map<string, number>();
  for (const r of rows) {
    if (!r.concept_code || r.claim == null) {
      unresolved.set(r.jd, (unresolved.get(r.jd) ?? 0) + 1);
      continue;
    }
    if (!seen.has(r.jd)) seen.set(r.jd, new Map());
    seen.get(r.jd)!.set(r.concept_code, r.claim);
  }
  const out = new Map<string, ConceptCounts>();
  for (const jd of new Set([...seen.keys(), ...unresolved.keys()])) {
    const c: ConceptCounts = { covered: 0, sot: 0, gap: 0, doNotClaim: 0, unresolved: unresolved.get(jd) ?? 0 };
    for (const claim of seen.get(jd)?.values() ?? []) {
      const k = claim.toLowerCase();
      if (k === "covered") c.covered++;
      else if (k.startsWith("sot")) c.sot++;
      else if (k === "do not claim") c.doNotClaim++;
      else c.gap++;
    }
    out.set(jd, c);
  }
  return out;
}

/** JD terms with their type from the application's 07_job_keywords.json. */
export function jdTermTypes(appDir: string): { term: string; type: KeywordType }[] {
  return jobKeywordTerms(readJson<JobKeywords>(path.join(appDir, "07_job_keywords.json")) ?? {});
}

// ---------------------------------------------------------------- align / gaps (CLI) + cache

const TTL_MS = 10 * 60 * 1000;
const cache = new Map<string, { at: number; value: Promise<unknown> }>();

/** Drop cached align/gaps results (all applications: a library change can move every JD). */
export function invalidateConceptCache(): void {
  cache.clear();
}

function cached<T>(base: string, make: () => Promise<T>): Promise<T> {
  // A rebuilt DB (library_db.py init) also invalidates: the mtime is part of the key.
  const key = `${base}@${existsSync(DB_PATH) ? statSync(DB_PATH).mtimeMs : 0}`;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.value as Promise<T>;
  const value = make();
  cache.set(key, { at: Date.now(), value });
  value.catch(() => cache.delete(key));
  return value;
}

const TYPE_SHORT: Record<string, KeywordType> = { req: "required", pref: "preferred", kw: "keyword" };

function cells(line: string): string[] {
  return line.trim().replace(/^\|\s?/, "").replace(/\s?\|$/, "").split(/\s\|\s/).map((c) => c.trim());
}

function actionKind(action: string): Pick<AlignRow, "actionKind" | "leadOrd" | "proposal" | "variant"> {
  let m: RegExpExecArray | null;
  if ((m = /^keep lead_ord (\d+)/.exec(action))) return { actionKind: "keep", leadOrd: Number(m[1]) };
  if (/^keep\b/.test(action)) return { actionKind: "keep" };
  if ((m = /^use lead_ord (\d+)/.exec(action))) return { actionKind: "use_lead", leadOrd: Number(m[1]) };
  if ((m = /^NEW LEAD\s+"(.+?)"/.exec(action))) return { actionKind: "new_lead", proposal: m[1] };
  if (/^NEW LEAD/.test(action)) return { actionKind: "new_lead", proposal: action.replace(/^NEW LEAD\s*/, "") };
  if (/^spelling/.test(action)) {
    m = /'([^']+)'\s*$/.exec(action) ?? /"([^"]+)"\s*$/.exec(action);
    return { actionKind: "spelling", ...(m ? { variant: m[1] } : {}) };
  }
  return { actionKind: "summary" };
}

export function parseAlignOutput(raw: string): Omit<AlignResult, "stderr" | "exitCode"> {
  const lines = raw.split(/\r?\n/);
  const rows: AlignRow[] = [];
  const notes: string[] = [];
  let summary = "";
  let picks = "";
  let picksUnchanged = false;
  let tableSeen = false;
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i].trim();
    if (!l) continue;
    if (l.startsWith("|")) {
      tableSeen = true;
      const c = cells(l);
      if (c[0] === "line" || /^-+$/.test(c[0].replace(/[:\s]/g, ""))) continue;
      if (c.length < 5) continue;
      const [line, conceptCell, jdPhrase, current] = c;
      const action = c.slice(4).join(" | ");
      const cm = /^(\S+)\s*\(w(\d)\)/.exec(conceptCell);
      const row: AlignRow = {
        line,
        concept: cm ? cm[1] : conceptCell,
        weight: cm ? Number(cm[2]) : null,
        jdPhrase,
        current,
        action,
        ...actionKind(action),
      };
      if (line.startsWith("skills:")) row.skillRow = line.replace(/^skills:\s*/, "");
      rows.push(row);
    } else if (/^picks with lead ords applied/.test(l)) {
      picksUnchanged = /unchanged/.test(l);
      picks = (lines.slice(i + 1).find((x) => x.trim()) ?? "").trim();
      break;
    } else if (tableSeen) summary = summary ? `${summary}\n${l}` : l;
    else notes.push(l);
  }
  return { rows, notes, summary, picks, picksUnchanged, raw };
}

const GROUP_HEAD: [RegExp, GapGroupId][] = [
  [/^##\s*SoT/i, "sotSupported"],
  [/^##\s*gap\b/i, "gap"],
  [/^##\s*do not claim/i, "doNotClaim"],
  [/^##\s*covered concept/i, "coveredNotClaimable"],
];

function parseEvidence(t: ConceptTables | null, s: string): ConceptEvidence[] {
  if (!s || s === "none") return [];
  return s.split(/,\s+/).flatMap((part) => {
    const m = /^(bullet|skill|lead|scope|context)\s+(.+?)\s+\((direct|implied)\)$/.exec(part.trim());
    if (!m) return [];
    return [{ kind: m[1], code: m[2], strength: m[3], ...(t ? evidenceLink(t, m[1], m[2]) : { notionUrl: null }) }];
  });
}

export function parseGapsOutput(raw: string, t: ConceptTables | null = null): Pick<GapsResult, "groups" | "raw"> {
  const groups: GapsResult["groups"] = { sotSupported: [], gap: [], doNotClaim: [], coveredNotClaimable: [] };
  let cur: GapGroupId | null = null;
  let last: GapConcept | null = null;
  for (const line of raw.split(/\r?\n/)) {
    const l = line.trim();
    if (!l) continue;
    if (l.startsWith("##")) {
      cur = GROUP_HEAD.find(([re]) => re.test(l))?.[1] ?? null;
      last = null;
      continue;
    }
    if (!cur) continue;
    if (l.startsWith("- ")) {
      const body = l.slice(2);
      if (cur === "coveredNotClaimable") {
        const [head, ...rest] = body.split(" · ");
        const sp = head.indexOf(" ");
        last = {
          code: sp > 0 ? head.slice(0, sp) : head,
          name: sp > 0 ? head.slice(sp + 1) : "",
          weight: null,
          jdTerms: rest.map((r) => {
            const m = /^(.+?)\s+\[([^\]]+)\]$/.exec(r.trim());
            return m ? { term: m[1], type: null, status: m[2] } : { term: r.trim(), type: null };
          }),
          evidence: [],
          notes: "",
        };
      } else {
        const m = /^(\S+)\s+\(w(\d)\)\s+(.*?)(?:\s+·\s+JD:\s+(.*))?$/.exec(body);
        last = {
          code: m ? m[1] : body.split(" ")[0],
          name: m ? m[3] : body,
          weight: m ? Number(m[2]) : null,
          jdTerms: (m?.[4] ?? "")
            .split(" · ")
            .filter(Boolean)
            .map((r) => {
              const tm = /^(.+?)\s+\[(\w+)\]$/.exec(r.trim());
              return tm ? { term: tm[1], type: TYPE_SHORT[tm[2]] ?? null } : { term: r.trim(), type: null };
            }),
          evidence: [],
          notes: "",
        };
      }
      groups[cur].push(last);
    } else if (last && /^evidence:/.test(l)) {
      const [ev, ...notes] = l.replace(/^evidence:\s*/, "").split(" · notes: ");
      last.evidence = parseEvidence(t, ev.trim());
      last.notes = notes.join(" · notes: ").trim();
    } else if (last) last.notes = [last.notes, l].filter(Boolean).join(" ");
  }
  return { groups, raw };
}

export function alignFor(jd: string): Promise<AlignResult> {
  return cached(`align:${jd}`, async () => {
    const r = await libraryDb(["align", "--jd", jd]);
    return { ...parseAlignOutput(r.stdout), stderr: r.stderr, exitCode: r.code };
  });
}

export function sotLeadsFor(jd: string): Promise<SotLeadsResult> {
  return cached(`sot-leads:${jd}`, async () => {
    const r = await libraryDb(["sot-leads", "--jd", jd, "--json"]);
    let bullets: SotLeadsResult["bullets"] = {};
    try {
      const v = JSON.parse(r.stdout || "{}");
      if (v && typeof v === "object" && !Array.isArray(v)) bullets = v;
    } catch {
      /* not JSON: the UI shows raw */
    }
    return { bullets, raw: r.stdout, stderr: r.stderr, exitCode: r.code };
  });
}

/** Unresolved = this JD's terms with keywords.concept_code NULL, or not in the keyword map at all. */
export function unresolvedTerms(t: ConceptTables, terms: { term: string; type: KeywordType }[]): GapsResult["unresolved"] {
  return terms
    .filter((x) => !conceptCodeOf(t, x.term))
    .map((x) => ({ term: x.term, type: x.type, inKeywordMap: t.termConcept.has(x.term.toLowerCase()) }));
}

export function gapsFor(jd: string, appDir: string): Promise<GapsResult> {
  return cached(`gaps:${jd}`, async () => {
    const r = await libraryDb(["gaps", "--jd", jd]);
    const t = loadConceptTables();
    return { ...parseGapsOutput(r.stdout, t), unresolved: unresolvedTerms(t, jdTermTypes(appDir)), stderr: r.stderr, exitCode: r.code };
  });
}

// ---------------------------------------------------------------- prompt context

export const CONCEPT_RULE =
  "Terminology alignment goes in the lead (JD noun), the body keeps the tool name; never rewrite a body semantically — " +
  "that is a library-mode change with CONF. Useful commands: library_db.py resolve <term>, concept <CODE>, " +
  "align --jd <jd>, gaps --jd <jd>, set-concept <term> <CODE>.";

function proves(t: ConceptTables, kinds: string[], code: string): string[] {
  const out: string[] = [];
  for (const [concept, list] of t.evidence) {
    for (const e of list) {
      if (!kinds.includes(e.kind) || e.code !== code) continue;
      const c = t.concepts.get(concept);
      if (c) out.push(`${c.code} ${c.name} (claim: ${c.claim}; ${e.strength}${e.kind !== "bullet" ? ` via ${e.kind}` : ""})`);
    }
  }
  const leadConcepts = all<{ concept_code: string }>(
    "SELECT DISTINCT concept_code FROM bullet_leads WHERE bullet_code = ? AND concept_code IS NOT NULL",
    code,
  ).map((r) => r.concept_code);
  for (const lc of leadConcepts) {
    const c = t.concepts.get(lc);
    if (c && !out.some((o) => o.startsWith(`${c.code} `))) out.push(`${c.code} ${c.name} (claim: ${c.claim}; named by a lead variant)`);
  }
  return [...new Set(out)];
}

/**
 * "Concept context" block for bullet / card / term targets: what the target bullet proves, and for a term its
 * concept, this JD's phrases for that concept and the evidence bullets. Empty for other targets.
 */
export function conceptContext(target: ClaudeTarget, jdTerms: { term: string; type: KeywordType }[] = []): string {
  const t = loadConceptTables();
  const out: string[] = [];
  const code = target.code ?? "";
  if ((target.kind === "bullet" || target.kind === "other_bullet" || target.kind === "term") && code && t.bullets.has(code)) {
    const p = proves(t, ["bullet", "lead"], code);
    out.push(`- ${code} proves: ${p.length ? p.join("; ") : "no concept recorded"}`);
  } else if (target.kind === "card" && code && t.projects.has(code)) {
    const p = proves(t, ["context"], code);
    out.push(`- card ${code} context proves: ${p.length ? p.join("; ") : "no concept recorded"}`);
  }
  if (target.kind === "term" && target.term) {
    const cc = conceptCodeOf(t, target.term);
    const c = cc ? t.concepts.get(cc) : undefined;
    if (!c) out.push(`- '${target.term}' resolves to no concept yet: run library_db.py resolve "${target.term}", then set-concept.`);
    else {
      const phrases = jdTerms.filter((x) => conceptCodeOf(t, x.term) === c.code).map((x) => `${x.term} [${x.type}]`);
      const ev = (t.evidence.get(c.code) ?? []).map((e) => `${e.kind} ${e.code} (${e.strength})`);
      out.push(
        `- '${target.term}' → ${c.code} ${c.name} (claim: ${c.claim})` +
          `\n  JD phrases for it: ${phrases.length ? phrases.join(", ") : target.term}` +
          `\n  evidence: ${ev.length ? ev.join(", ") : "none"}`,
      );
    }
  }
  if (!out.length) return "";
  return `\nConcept context:\n${out.join("\n")}\n${CONCEPT_RULE}`;
}
