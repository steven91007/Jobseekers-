// Port of the ATS keyword rule used by .claude/skills/tailor-cv/scripts/ats_check.py:
//   - ext/resume_matcher/app/services/text_match.py  keyword_in_text()
//   - ats_check.py  resume_from_md(), job_keyword_terms(), _extract_all_text(), calculate_keyword_match()
// Case-insensitive, whole-word, no synonyms. Keep this file in lock-step with the Python source;
// test/match.test.ts checks it against every recorded 07_external_ats.json.

// Han (incl. supplementary planes), Kana, Hangul — copied from text_match._CJK_CHAR_CLASS.
const CJK_CLASS =
  "[\\u1100-\\u11ff\\u3040-\\u30ff\\u3130-\\u318f\\u31f0-\\u31ff\\u3400-\\u9fff\\ua960-\\ua97f" +
  "\\uac00-\\ud7ff\\uf900-\\ufaff\\uff66-\\uffdc\\u{1b000}-\\u{1b16f}\\u{20000}-\\u{3ffff}]";
const CJK_RE = new RegExp(CJK_CLASS, "u");
// Python's Unicode `\w` = str.isalnum() or "_" = letters (L*) + numbers (N*) + underscore.
const W = "[\\p{L}\\p{N}_]";

const patternCache = new Map<string, RegExp>();

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\\/]/g, "\\$&");
}

/** Python str.lower() and JS toLowerCase() agree for the scripts this CV uses. */
function lower(s: string): string {
  return s.toLowerCase();
}

/** Python str.strip() (Unicode whitespace). */
function pyStrip(s: string): string {
  return s.replace(/^[\s\x1c-\x1f\x85]+|[\s\x1c-\x1f\x85]+$/gu, "");
}

export function keywordInText(keyword: string, text: string): boolean {
  const kw = lower(pyStrip(keyword));
  if (!kw) return false;
  const t = lower(text);
  if (CJK_RE.test(kw)) return t.includes(kw);
  let re = patternCache.get(kw);
  if (!re) {
    re = new RegExp(`(?:(?<!${W})|(?<=${CJK_CLASS}))${escapeRegExp(kw)}(?:(?!${W})|(?=${CJK_CLASS}))`, "u");
    patternCache.set(kw, re);
  }
  return re.test(t);
}

/** Occurrences of a term in a text, as ats_check.repeated_terms counts them (no CJK rule). */
export function countTerm(term: string, text: string): number {
  const re = new RegExp(`(?<!${W})${escapeRegExp(lower(term))}(?!${W})`, "gu");
  return (lower(text).match(re) ?? []).length;
}

const BOLD = /\*\*(.+?)\*\*/g;

export function stripMd(s: string): string {
  return pyStrip(s.replace(BOLD, "$1"));
}

/** Python str.splitlines() boundaries. */
export function splitLines(text: string): string[] {
  const t = text.replace(/^\ufeff/, "");
  const parts = t.split(/\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/);
  if (parts.length && parts[parts.length - 1] === "") parts.pop();
  return parts;
}

export interface JobKeywords {
  required_skills?: unknown[];
  preferred_skills?: unknown[];
  keywords?: unknown[];
  [k: string]: unknown;
}

export type TermType = "required" | "preferred" | "keyword";

/** job_keyword_terms(): required + preferred + keywords, de-duplicated, first field wins. */
export function jobKeywordTerms(job: JobKeywords): { term: string; type: TermType }[] {
  const out = new Map<string, TermType>();
  const fields: [keyof JobKeywords, TermType][] = [
    ["required_skills", "required"],
    ["preferred_skills", "preferred"],
    ["keywords", "keyword"],
  ];
  for (const [field, type] of fields) {
    const list = job[field];
    if (!Array.isArray(list)) continue;
    for (const term of list) if (typeof term === "string" && !out.has(term)) out.set(term, type);
  }
  return [...out].map(([term, type]) => ({ term, type }));
}

/** One scanned piece of text, tagged with the 03_cv.md line it came from. */
export interface ScanPart {
  line: number;
  text: string;
}

export interface ScannedResume {
  summary: ScanPart | null;
  work: { title: ScanPart; company: ScanPart; description: ScanPart[] }[];
  education: { degree: ScanPart; institution: ScanPart }[];
  skills: ScanPart[];
}

/** resume_from_md(): the parts of 03_cv.md the keyword match reads, with their line numbers. */
export function scanResume(md: string): ScannedResume {
  const lines = splitLines(md).map((l) => l.replace(/\r+$/, ""));
  const res: ScannedResume = { summary: null, work: [], education: [], skills: [] };
  if (lines.length > 4) res.summary = { line: 4, text: stripMd(lines[4]) };

  const pe = lines.findIndex((l) => l.startsWith("**PROFESSIONAL EXPERIENCE**"));
  if (pe >= 0) {
    for (let i = pe + 1; i < lines.length && lines[i]; i++) {
      const l = lines[i];
      if (l.includes("\t") && l.startsWith("**")) {
        const head = l.split("\t")[0];
        const [t = "", company = ""] = head.split("  |  ").map(stripMd);
        res.work.push({ title: { line: i, text: t }, company: { line: i, text: company }, description: [] });
      } else if (res.work.length) {
        const body = stripMd(l.startsWith("- ") ? l.slice(2) : l);
        res.work[res.work.length - 1].description.push({ line: i, text: body });
      }
    }
  }

  // every `**Label:** items` row in the Skills section counts, including a single-item row (no " · ")
  const s0 = lines.findIndex((l) => l.startsWith("**TECHNICAL SKILLS**"));
  const s1 = lines.findIndex((l) => l.startsWith("**PROFESSIONAL EXPERIENCE**"));
  lines.forEach((l, i) => {
    if (s0 >= 0 && (i <= s0 || (s1 >= 0 && i >= s1))) return;
    const m = /^\*\*([^*]+):\*\*\s*(.+)$/.exec(l);
    if (m) for (const s of m[2].split("·")) if (s.trim()) res.skills.push({ line: i, text: pyStrip(s) });
  });

  const ed = lines.findIndex((l) => l.startsWith("**EDUCATION**"));
  if (ed >= 0) {
    for (let j = ed + 1; j + 1 < lines.length && lines[j].startsWith("**") && lines[j].includes("  ·  "); j += 2) {
      const [deg = ""] = lines[j].split("  ·  ").map(stripMd);
      res.education.push({ degree: { line: j, text: deg }, institution: { line: j + 1, text: pyStrip(lines[j + 1]) } });
    }
  }
  return res;
}

/** _extract_all_text() order: summary, work (title, company, description…), education, skills. */
export function scanParts(r: ScannedResume): ScanPart[] {
  const parts: ScanPart[] = [];
  if (r.summary && r.summary.text) parts.push(r.summary);
  for (const w of r.work) parts.push(w.title, w.company, ...w.description);
  for (const e of r.education) parts.push(e.degree, e.institution);
  parts.push(...r.skills);
  return parts;
}

export interface KeywordMatch {
  percent: number;
  found: string[];
  missing: string[];
}

/** calculate_keyword_match(): terms found in the whole scanned text. */
export function calculateKeywordMatch(md: string, job: JobKeywords): KeywordMatch {
  const text = scanParts(scanResume(md)).map((p) => p.text).join(" ");
  const terms = jobKeywordTerms(job).map((t) => t.term);
  if (!terms.length) return { percent: 0, found: [], missing: [] };
  const found = terms.filter((t) => keywordInText(t, text));
  const missing = terms.filter((t) => !found.includes(t));
  return { percent: (found.length / terms.length) * 100, found, missing };
}

/** Scanned text per 03_cv.md line (several parts of one line are joined with a space). */
export function scannedTextByLine(md: string): Map<number, string> {
  const byLine = new Map<number, string[]>();
  for (const p of scanParts(scanResume(md))) {
    const list = byLine.get(p.line) ?? [];
    list.push(p.text);
    byLine.set(p.line, list);
  }
  return new Map([...byLine].map(([k, v]) => [k, v.join(" ")]));
}

/** [start, end) ranges of a term in a text under the same whole-word rule (for highlighting). */
export function termRanges(term: string, text: string): [number, number][] {
  const kw = lower(pyStrip(term));
  if (!kw) return [];
  const t = lower(text);
  // Lowercasing can change length for a few code points; fall back to no highlight then.
  if (t.length !== text.length) return [];
  const re = CJK_RE.test(kw)
    ? new RegExp(escapeRegExp(kw), "gu")
    : new RegExp(`(?:(?<!${W})|(?<=${CJK_CLASS}))${escapeRegExp(kw)}(?:(?!${W})|(?=${CJK_CLASS}))`, "gu");
  const out: [number, number][] = [];
  for (const m of t.matchAll(re)) out.push([m.index!, m.index! + m[0].length]);
  return out;
}
