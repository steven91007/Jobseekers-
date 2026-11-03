// Notion links and stored variants behind each picked CV item. Read-only SQL only.
import type { BulletRef, ClaudeTarget, CvItem, ItemLibrary, LibraryVariant, SkillItemLink } from "../shared/types.js";
import { stripMd } from "../shared/match.js";
import { all, get } from "./db.js";

/** The "Personal experiences" root page in Notion. */
export const NOTION_ROOT_URL = "https://www.notion.so/40877152eb3d813b8468d0f508b78789";

export function notionUrl(id: string | null | undefined): string | null {
  const clean = (id ?? "").replace(/-/g, "").trim();
  return /^[0-9a-f]{32}$/i.test(clean) ? `https://www.notion.so/${clean}` : null;
}

const norm = (s: string | null | undefined) =>
  stripMd(s ?? "")
    .replace(/\s+/g, " ")
    .replace(/\s*—\s*$/, "")
    .trim()
    .toLowerCase();

interface BulletRow {
  code: string;
  project_code: string;
  body: string;
  conf: string | null;
  notion_id: string | null;
}

interface Tables {
  bullets: Map<string, BulletRow>;
  leads: Map<string, { lead: string; ord: number }[]>;
  projects: Map<string, { code: string; name: string; notion_id: string | null }>;
  titles: Map<string, { title: string; ord: number }[]>;
  contexts: Map<string, { text: string; conf: string | null; note: string | null; ord: number }[]>;
  scopes: { code: string; text: string; conf: string | null; note: string | null }[];
  skills: { name: string; variants: string | null; notion_id: string | null }[];
  rows: { label: string; variants: string | null }[];
  rowItems: Map<string, { item: string; ord: number; spellings: string[] }[]>;
}

function group<T, K>(rows: T[], key: (r: T) => K): Map<K, T[]> {
  const m = new Map<K, T[]>();
  for (const r of rows) {
    const k = key(r);
    if (!m.has(k)) m.set(k, []);
    m.get(k)!.push(r);
  }
  return m;
}

function loadTables(): Tables {
  const parse = (s: string | null): string[] => {
    try {
      const v = JSON.parse(s ?? "[]");
      return Array.isArray(v) ? v.map(String) : [];
    } catch {
      return [];
    }
  };
  return {
    bullets: new Map(
      all<BulletRow>("SELECT code, project_code, body, conf, notion_id FROM bullets").map((b) => [b.code, b]),
    ),
    leads: group(
      all<{ bullet_code: string; lead: string; ord: number }>("SELECT bullet_code, lead, ord FROM bullet_leads ORDER BY bullet_code, ord"),
      (r) => r.bullet_code,
    ),
    projects: new Map(
      all<{ code: string; name: string; notion_id: string | null }>("SELECT code, name, notion_id FROM projects").map((p) => [p.code, p]),
    ),
    titles: group(
      all<{ project_code: string; title: string; ord: number }>("SELECT project_code, title, ord FROM project_titles ORDER BY project_code, ord"),
      (r) => r.project_code,
    ),
    contexts: group(
      all<{ project_code: string; text: string; conf: string | null; note: string | null; ord: number }>(
        "SELECT project_code, text, conf, note, ord FROM project_contexts ORDER BY project_code, ord",
      ),
      (r) => r.project_code,
    ),
    scopes: all("SELECT code, text, conf, note FROM scope_lines ORDER BY rowid"),
    skills: all("SELECT name, variants, notion_id FROM skills"),
    rows: all("SELECT label, variants FROM skill_rows ORDER BY ord"),
    rowItems: group(
      all<{ row_label: string; item: string; ord: number; spellings: string | null }>(
        "SELECT row_label, item, ord, spellings FROM skill_row_items ORDER BY row_label, ord",
      ).map((r) => ({ row_label: r.row_label, item: r.item, ord: r.ord, spellings: parse(r.spellings) })),
      (r) => r.row_label,
    ) as Tables["rowItems"],
  };
}

function skillUrl(t: Tables, item: string, spellings: string[] = []): string | null {
  const names = [item, ...spellings].map(norm);
  const s =
    t.skills.find((k) => names.includes(norm(k.name))) ??
    t.skills.find((k) => (k.variants ?? "").split(/[|;]/).some((v) => names.includes(norm(v))));
  return notionUrl(s?.notion_id);
}

function projectPart(t: Tables, code: string | undefined): Pick<ItemLibrary, "projectCode" | "projectName" | "projectNotionUrl"> {
  const p = code ? t.projects.get(code) : undefined;
  return p ? { projectCode: p.code, projectName: p.name, projectNotionUrl: notionUrl(p.notion_id) } : {};
}

function bulletLibrary(t: Tables, it: CvItem): ItemLibrary | undefined {
  const code = it.pick!.code;
  const b = t.bullets.get(code);
  if (!b) return undefined;
  const leads = t.leads.get(code) ?? [];
  const cur = it.pick!.variant ?? leads.find((l) => norm(l.lead) === norm(it.lead))?.ord ?? null;
  const variants: LibraryVariant[] = leads.map((l) => ({ kind: "lead", ord: l.ord, text: l.lead, current: l.ord === cur }));
  return { notionUrl: notionUrl(b.notion_id), ...projectPart(t, b.project_code), variants, bodyPreview: b.body };
}

function cardLibrary(t: Tables, it: CvItem): ItemLibrary | undefined {
  const code = it.pick!.code;
  const p = t.projects.get(code);
  if (!p) return undefined;
  const variants: LibraryVariant[] = [
    ...(t.titles.get(code) ?? []).map(
      (x): LibraryVariant => ({ kind: "title", ord: x.ord, text: x.title, current: x.ord === it.pick!.variant }),
    ),
    ...(t.contexts.get(code) ?? []).map(
      (x): LibraryVariant => ({ kind: "context", ord: x.ord, text: x.text, current: x.ord === it.pick!.contextOrd, conf: x.conf, note: x.note }),
    ),
  ];
  return { notionUrl: notionUrl(p.notion_id), ...projectPart(t, code), variants };
}

function scopeLibrary(t: Tables, it: CvItem): ItemLibrary {
  const variants = t.scopes.map(
    (s, i): LibraryVariant => ({ kind: "scope", ord: i, code: s.code, text: s.text, current: s.code === it.pick!.code, conf: s.conf, note: s.note }),
  );
  return { notionUrl: null, variants };
}

function skillRowLibrary(t: Tables, it: CvItem): ItemLibrary | undefined {
  const code = it.pick!.code;
  const row = t.rows.find((r) => r.label === code);
  if (!row) return undefined;
  const labels = [row.label, ...(row.variants ?? "").split("|").filter(Boolean)];
  const onCv = new Set((it.items ?? []).map(norm));
  const variants: LibraryVariant[] = labels.map((l, i) => ({ kind: "label", ord: i, text: l, current: norm(l) === norm(it.label) }));
  const skillItems: SkillItemLink[] = [];
  for (const item of t.rowItems.get(code) ?? []) {
    skillItems.push({ item: item.item, ord: item.ord, notionUrl: skillUrl(t, item.item, item.spellings) });
    if (!item.spellings.length) continue;
    const forms = [item.item, ...item.spellings];
    const cur = forms.findIndex((s) => onCv.has(norm(s)));
    forms.forEach((s, n) => variants.push({ kind: "spelling", ord: n, item: item.item, text: s, current: n === cur }));
  }
  return { notionUrl: null, variants, skillItems };
}

/** Sets `library` on every item that has a pick. */
export function attachLibrary(items: CvItem[]): void {
  const t = loadTables();
  for (const it of items) {
    if (!it.pick) continue;
    let lib: ItemLibrary | undefined;
    if (it.kind === "bullet" || it.kind === "other_bullet") lib = bulletLibrary(t, it);
    else if (it.kind === "card") lib = cardLibrary(t, it);
    else if (it.kind === "scope") lib = scopeLibrary(t, it);
    else if (it.kind === "skill_row") lib = skillRowLibrary(t, it);
    if (lib) it.library = lib;
  }
}

// ---------------------------------------------------------------- keywords

export interface KeywordLinks {
  notionUrl: string | null;
  bulletRefs: BulletRef[];
  skillRefs: { name: string; notionUrl: string | null }[];
}

/** Lowercased term → Notion link of the term, its covering bullets (with leads) and skills. */
export function keywordLinks(): Map<string, KeywordLinks> {
  const t = loadTables();
  const kws = all<{ term: string; notion_id: string | null }>("SELECT term, notion_id FROM keywords");
  const kb = group(all<{ term: string; bullet_code: string }>("SELECT term, bullet_code FROM keyword_bullets"), (r) => r.term);
  const ks = group(all<{ term: string; skill_name: string }>("SELECT term, skill_name FROM keyword_skills"), (r) => r.term);
  const skillByName = new Map(t.skills.map((s) => [s.name, s]));
  const out = new Map<string, KeywordLinks>();
  for (const k of kws) {
    const bulletRefs: BulletRef[] = (kb.get(k.term) ?? []).map(({ bullet_code: code }) => {
      const b = t.bullets.get(code);
      const p = b ? t.projects.get(b.project_code) : undefined;
      return {
        code,
        notionUrl: notionUrl(b?.notion_id),
        projectCode: b?.project_code ?? "",
        projectNotionUrl: notionUrl(p?.notion_id),
        leads: (t.leads.get(code) ?? []).map((l) => ({ ord: l.ord, text: l.lead })),
      };
    });
    const skillRefs = (ks.get(k.term) ?? []).map(({ skill_name: name }) => ({ name, notionUrl: notionUrl(skillByName.get(name)?.notion_id) }));
    out.set(k.term.toLowerCase(), { notionUrl: notionUrl(k.notion_id), bulletRefs, skillRefs });
  }
  return out;
}

export function applicationNotionUrl(jd: string): string | null {
  return notionUrl(get<{ notion_id: string | null }>("SELECT notion_id FROM applications WHERE jd = ?", jd)?.notion_id);
}

// ---------------------------------------------------------------- prompt evidence

/** Notion pages the headless run can fetch for this target (item page first, then its project). */
export function notionEvidence(target: ClaudeTarget): string[] {
  const t = loadTables();
  const urls: (string | null)[] = [];
  const addBullet = (code: string) => {
    const b = t.bullets.get(code);
    if (!b) return false;
    urls.push(notionUrl(b.notion_id), notionUrl(t.projects.get(b.project_code)?.notion_id));
    return true;
  };
  const code = target.code ?? "";
  if (target.kind === "term" && target.term) {
    const k = keywordLinks().get(target.term.toLowerCase());
    if (k) {
      urls.push(k.notionUrl);
      for (const b of k.bulletRefs) urls.push(b.notionUrl, b.projectNotionUrl);
      for (const s of k.skillRefs) urls.push(s.notionUrl);
    }
    if (code) addBullet(code);
  } else if (code && !addBullet(code)) {
    if (t.projects.has(code)) urls.push(notionUrl(t.projects.get(code)!.notion_id));
    else for (const it of t.rowItems.get(code) ?? []) urls.push(skillUrl(t, it.item, it.spellings));
  }
  return [...new Set(urls.filter((u): u is string => Boolean(u)))].slice(0, 12);
}
