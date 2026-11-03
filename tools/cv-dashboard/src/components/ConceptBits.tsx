// Concept layer UI: keyword panel grouped by concept, the Gaps panel and the Align modal.
import { useEffect, useState, type ReactNode } from "react";
import type {
  AlignResult,
  AlignRow,
  ClaudeTarget,
  ConceptEvidence,
  ConceptGroup,
  CvItem,
  GapConcept,
  GapsResult,
  KeywordRow,
  SotLeadsResult,
  TargetLibrary,
} from "../../shared/types";
import { api } from "../api";
import { Badge, Modal } from "./ui";
import { NotionLink } from "./LibraryBits";

type Ask = (target: ClaudeTarget, opts?: { instruction?: string; library?: TargetLibrary }) => void;

export function claimTone(claim: string): string {
  const c = claim.toLowerCase();
  if (c === "covered") return "badge-ok";
  if (c.startsWith("sot")) return "badge-info";
  if (c === "gap") return "badge-warn";
  if (c === "do not claim") return "badge-bad";
  return "badge-neutral";
}

const CLAIM_SHORT: Record<string, string> = { "SoT-supported wording": "SoT" };

export function ClaimBadge({ claim }: { claim: string }) {
  return (
    <Badge tone={claimTone(claim)} title={`claim: ${claim}`}>
      {CLAIM_SHORT[claim] ?? claim}
    </Badge>
  );
}

function EvidenceList({ evidence }: { evidence: ConceptEvidence[] }) {
  if (!evidence.length) return <span className="faint">none</span>;
  return (
    <span className="ev-list">
      {evidence.map((e, i) => (
        <span key={`${e.kind}-${e.code}-${i}`} className={`ev ev-${e.strength}`} title={e.note || `${e.kind} · ${e.strength}`}>
          <span className="faint">{e.kind}</span> {e.notionUrl ? <a href={e.notionUrl} target="_blank" rel="noreferrer">{e.code}</a> : e.code}{" "}
          <span className={`ev-tag ${e.strength}`}>{e.strength}</span>
        </span>
      ))}
    </span>
  );
}

// ---------------------------------------------------------------- keyword panel by concept

export function ConceptKeywordGroups({
  concepts,
  keywords,
  filter,
  chip,
}: {
  concepts: ConceptGroup[];
  keywords: KeywordRow[];
  filter: string;
  chip: (k: KeywordRow) => ReactNode;
}) {
  const kw = new Map(keywords.map((k) => [k.term, k]));
  const f = filter.trim().toLowerCase();
  const show = (t: string) => !f || t.toLowerCase().includes(f);
  const unresolved = keywords.filter((k) => !k.concept && show(k.term));
  let shown = 0;
  return (
    <>
      {concepts.map((c) => {
        const terms = c.jdTerms.filter((t) => show(t.term) || (f && (c.name.toLowerCase().includes(f) || c.code.toLowerCase().includes(f))));
        if (!terms.length) return null;
        shown++;
        const found = c.jdTerms.filter((t) => t.found).length;
        return (
          <div className={`concept-group claim-${claimTone(c.claim)}`} key={c.code}>
            <div className="concept-head">
              <span className="concept-name">{c.name}</span>
              <ClaimBadge claim={c.claim} />
              <span className="badge badge-neutral" title="Weight: 3 required / 2 preferred / 1 keyword (max over its JD terms)">w{c.weight}</span>
              <span className="faint concept-count" title="JD terms found on this CV">
                {found}/{c.jdTerms.length}
              </span>
            </div>
            <div className="chips">{terms.map((t) => (kw.has(t.term) ? chip(kw.get(t.term)!) : null))}</div>
            <details className="concept-details">
              <summary>
                <span className="mono">{c.code}</span> · {c.kind} · {c.evidence.length} evidence
              </summary>
              <dl>
                {c.definition && (
                  <>
                    <dt>Definition</dt>
                    <dd>{c.definition}</dd>
                  </>
                )}
                <dt>Evidence</dt>
                <dd>
                  <EvidenceList evidence={c.evidence} />
                </dd>
                <dt>Aliases</dt>
                <dd>{c.aliases.length ? c.aliases.join(" · ") : <span className="faint">none</span>}</dd>
                {c.relations.length > 0 && (
                  <>
                    <dt>Relations</dt>
                    <dd className="mono">
                      {c.relations.map((r, i) => (
                        <div key={i}>
                          {r.src} {r.rel} {r.dst}
                        </div>
                      ))}
                    </dd>
                  </>
                )}
                {c.notes && (
                  <>
                    <dt>Notes</dt>
                    <dd>{c.notes}</dd>
                  </>
                )}
              </dl>
              {c.notionUrl && <NotionLink url={c.notionUrl} label="Concept" />}
            </details>
          </div>
        );
      })}
      {unresolved.length > 0 && (
        <div className="concept-group" key="__unresolved">
          <div className="concept-head">
            <span className="concept-name">Unresolved</span>
            <span className="faint concept-count">{unresolved.length}</span>
          </div>
          <div className="hint">No concept yet: classify from the Gaps tab (library_db.py resolve / set-concept).</div>
          <div className="chips">{unresolved.map(chip)}</div>
        </div>
      )}
      {!shown && !unresolved.length && <div className="empty">No term matches “{filter}”.</div>}
    </>
  );
}

// ---------------------------------------------------------------- instructions

export function sotInstruction(c: GapConcept, jd: string): string {
  const terms = c.jdTerms.map((t) => `"${t.term}"`).join(", ");
  const implied = c.evidence.filter((e) => e.strength === "implied");
  const bullets = (implied.length ? implied : c.evidence).map((e) => e.code).join(", ") || "no bullet yet";
  return (
    `Library mode for concept ${c.code} (${c.name}): the JD asks for ${terms}; evidence is implied in ${bullets}. ` +
    "Propose a lead or bullet variant that says the JD noun, ask me to confirm the facts (CONF) before writing, then " +
    `init, selftest, export-md, rebuild ${jd}`
  );
}

export function sotLeadInstruction(code: string, terms: string[], body: string, jd: string): string {
  return (
    `Library mode: write ONE natural lead variant (4–8 words, a duty phrase, not a term list) for ${code} that names ` +
    `${terms.map((t) => `"${t}"`).join(", ")} and is true to its body "${body}"; show it to me for approval, then append it ` +
    "to the seed leads, run init + selftest + export-md, mirror the Notion Bullets row (notion-update-page \"Lead variants\"), " +
    `and rebuild ${jd} with that lead.`
  );
}

function SotLeads({ jd, reloadKey, items, onAsk }: { jd: string; reloadKey: number; items: CvItem[]; onAsk: Ask }) {
  const [data, setData] = useState<SotLeadsResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setError(null);
    api
      .sotLeads(jd)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e.message ?? e)));
    return () => {
      alive = false;
    };
  }, [jd, reloadKey]);
  const entries = Object.entries(data?.bullets ?? {});
  return (
    <div className="kw-group group-sot">
      <h3>
        SoT lead proposals <span className="count">{entries.length}</span>
      </h3>
      <div className="hint">Per picked bullet: the JD's missing SoT-supported terms. Claude writes one natural lead; you approve it.</div>
      {error && <div className="error">{error}</div>}
      {!data && !error && <div className="loading">Running library_db.py sot-leads…</div>}
      {data && data.exitCode !== 0 && <div className="error">sot-leads exited with code {String(data.exitCode)}. {data.stderr}</div>}
      {data && !entries.length && data.exitCode === 0 && <div className="faint" style={{ fontSize: 12.5 }}>{data.raw.trim() && data.raw.trim() !== "{}" ? data.raw : "No proposals."}</div>}
      {entries.map(([code, b]) => {
        const it = items.find((x) => x.pick?.code === code && (x.kind === "bullet" || x.kind === "other_bullet"));
        return (
          <div className="gap-card" key={code}>
            <div className="concept-head">
              <span className="badge badge-code">{code}</span>
              <span className="concept-name">{it?.lead ?? <span className="faint">lead not on this CV</span>}</span>
            </div>
            <div className="chips">
              {b.terms.map((t) => (
                <span key={t} className="chip small">{t}</span>
              ))}
            </div>
            <div className="sot-draft">
              <span className="sot-draft-label">auto-draft (term list only)</span> {b.draft}
            </div>
            <button
              className="btn-ask btn-sm"
              onClick={() =>
                onAsk(
                  { kind: it?.kind ?? "bullet", code, text: it?.text },
                  { instruction: sotLeadInstruction(code, b.terms, b.body, jd), library: it?.library ? { item: it.library } : undefined },
                )
              }
            >
              Write lead
            </button>
          </div>
        );
      })}
    </div>
  );
}

export function classifyInstruction(term: string): string {
  return (
    `Run library_db.py resolve "${term}" and set-concept "${term}" <CODE> with the right concept ` +
    "(create a new concept in the seed only if truly new); then init, selftest"
  );
}

function leadText(items: CvItem[], code: string, ord: number): string | null {
  for (const it of items) {
    if (it.pick?.code !== code || !it.library) continue;
    const v = it.library.variants.find((x) => x.kind === "lead" && x.ord === ord);
    if (v) return v.text;
  }
  return null;
}

export function alignInstruction(r: AlignRow, items: CvItem[], jd: string): string | null {
  switch (r.actionKind) {
    case "use_lead":
      return `Switch ${r.line} to lead variant ${r.leadOrd}: "${leadText(items, r.line, r.leadOrd!) ?? "?"}"`;
    case "spelling":
      return r.skillRow
        ? `In Skills row "${r.skillRow}" spell ${r.current} as "${r.variant ?? r.jdPhrase}"`
        : `In ${r.line} write "${r.variant ?? r.jdPhrase}" (spelling recorded for "${r.jdPhrase}", concept ${r.concept}); body otherwise unchanged`;
    case "summary":
      return `Re-run the Codex Summary so it names "${r.jdPhrase}" once (concept ${r.concept}); keep every other sentence`;
    case "new_lead":
      return (
        `Library mode: add lead variant "${r.proposal}" to ${r.line} (concept ${r.concept}); ask me for the CONF ` +
        `confirmation before writing; then init, selftest, export-md, rebuild ${jd}`
      );
    default:
      return null;
  }
}

function alignTarget(r: AlignRow, items: CvItem[]): { target: ClaudeTarget; library?: TargetLibrary } {
  if (r.skillRow) {
    const it = items.find((x) => x.kind === "skill_row" && (x.pick?.code === r.skillRow || x.label === r.skillRow));
    return { target: { kind: "skill_row", code: it?.pick?.code ?? r.skillRow, text: it?.text }, library: it?.library ? { item: it.library } : undefined };
  }
  if (r.line === "Summary" || r.actionKind === "summary") return { target: { kind: "summary" } };
  const it = items.find((x) => x.pick?.code === r.line);
  return { target: { kind: it?.kind ?? "bullet", code: r.line, text: it?.text }, library: it?.library ? { item: it.library } : undefined };
}

const ACTION_TONE: Record<string, string> = {
  keep: "badge-neutral",
  use_lead: "badge-warn",
  spelling: "badge-info",
  summary: "badge-info",
  new_lead: "badge-bad",
};

// ---------------------------------------------------------------- align modal

export function AlignModal({ jd, items, onClose, onAsk }: { jd: string; items: CvItem[]; onClose: () => void; onAsk: Ask }) {
  const [data, setData] = useState<AlignResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showKeep, setShowKeep] = useState(false);

  useEffect(() => {
    api.align(jd).then(setData).catch((e) => setError(String(e.message ?? e)));
  }, [jd]);

  const ask = (r: AlignRow) => {
    const instruction = alignInstruction(r, items, jd);
    if (!instruction) return;
    const { target, library } = alignTarget(r, items);
    onClose();
    onAsk(target, { instruction, library });
  };
  const swaps = data?.rows.filter((r) => r.actionKind === "use_lead").length ?? 0;
  const rows = data?.rows.filter((r) => showKeep || r.actionKind !== "keep") ?? [];

  return (
    <Modal
      title={`Align — library_db.py align --jd ${jd}`}
      onClose={onClose}
      actions={
        data && (
          <label className="muted" style={{ fontSize: 12.5, display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={showKeep} onChange={(e) => setShowKeep(e.target.checked)} /> show keep rows
          </label>
        )
      }
    >
      {error && <div className="error">{error}</div>}
      {!data && !error && <div className="loading">Running library_db.py align…</div>}
      {data && (
        <>
          {data.exitCode !== 0 && <div className="error">align exited with code {String(data.exitCode)}. {data.stderr}</div>}
          {[...data.notes, ...(data.stderr ? [data.stderr.trim()] : [])].filter(Boolean).map((n, i) => (
            <div key={i} className="faint" style={{ fontSize: 12 }}>{n}</div>
          ))}
          <p className="muted" style={{ marginTop: 0 }}>{data.summary}</p>
          {data.rows.length === 0 && !data.raw.includes("|") ? (
            <pre className="raw">{data.raw}</pre>
          ) : (
            <div className="table-scroll">
              <table className="plain align-table">
                <thead>
                  <tr>
                    <th>Line</th>
                    <th>Concept</th>
                    <th>JD phrase</th>
                    <th>Current wording</th>
                    <th>Action</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className={`act-${r.actionKind}`}>
                      <td className="mono">{r.line}</td>
                      <td className="mono">
                        {r.concept}
                        {r.weight != null && <span className="faint"> w{r.weight}</span>}
                      </td>
                      <td>{r.jdPhrase}</td>
                      <td>{r.current}</td>
                      <td>
                        <Badge tone={ACTION_TONE[r.actionKind]}>{r.actionKind.replace("_", " ")}</Badge> <span className="faint">{r.action}</span>
                      </td>
                      <td>
                        {r.actionKind !== "keep" && (
                          <button className="btn-ask btn-sm" onClick={() => ask(r)}>
                            Ask Claude
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {!rows.length && (
                    <tr>
                      <td colSpan={6} className="faint">Nothing to change{data.rows.length ? " (keep rows hidden)" : ""}.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          <h3 style={{ margin: "16px 0 6px" }}>Picks with lead ords applied{data.picksUnchanged ? " (unchanged)" : ""}</h3>
          <pre className="raw">{data.picks || "—"}</pre>
          {swaps > 0 && !data.picksUnchanged && data.picks && (
            <button
              className="btn-primary"
              onClick={() => {
                onClose();
                onAsk({ kind: "other", code: "picks" }, { instruction: `Apply this pick string and rebuild: ${data.picks}` });
              }}
            >
              Apply all lead swaps ({swaps})
            </button>
          )}
        </>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- gaps panel

const GAP_GROUPS: { id: keyof GapsResult["groups"]; title: string; cls: string; hint: string }[] = [
  { id: "sotSupported", title: "SoT-supported", cls: "group-sot", hint: "True per the SoT, evidence implied: write a lead or bullet in library mode (CONF)." },
  { id: "gap", title: "Gap", cls: "group-gap", hint: "No evidence: learn or omit. Never on the CV." },
  { id: "doNotClaim", title: "Do not claim", cls: "group-dnc", hint: "Misleading or padding. Never on the CV." },
  { id: "coveredNotClaimable", title: "Covered concept, term not claimable", cls: "group-covered", hint: "The concept is covered, but these exact terms are not." },
];

export function GapsPanel({ jd, reloadKey, items, onAsk }: { jd: string; reloadKey: number; items: CvItem[]; onAsk: Ask }) {
  const [data, setData] = useState<GapsResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setError(null);
    api
      .gaps(jd)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e.message ?? e)));
    return () => {
      alive = false;
    };
  }, [jd, reloadKey]);

  if (error) return <div className="error">{error}</div>;
  if (!data) return <div className="loading">Running library_db.py gaps…</div>;
  const total = Object.values(data.groups).reduce((n, g) => n + g.length, 0);
  return (
    <>
      {data.exitCode !== 0 && <div className="error">gaps exited with code {String(data.exitCode)}. {data.stderr}</div>}
      {total === 0 && data.exitCode === 0 && <pre className="raw">{data.raw || "No gaps."}</pre>}
      {GAP_GROUPS.map((g) => {
        const list = data.groups[g.id];
        if (!list.length) return null;
        return (
          <div className={`kw-group ${g.cls}`} key={g.id}>
            <h3>
              {g.title} <span className="count">{list.length}</span>
            </h3>
            <div className="hint">{g.hint}</div>
            {list.map((c) => (
              <div className="gap-card" key={c.code}>
                <div className="concept-head">
                  <span className="concept-name">{c.name || c.code}</span>
                  <span className="mono faint">{c.code}</span>
                  {c.weight != null && <span className="badge badge-neutral">w{c.weight}</span>}
                </div>
                <div className="chips">
                  {c.jdTerms.map((t) => (
                    <span key={t.term} className={`chip small ${t.type === "required" ? "req" : ""}`}>
                      {t.type && <span className="t">{t.type === "required" ? "req" : t.type === "preferred" ? "pref" : "kw"}</span>}
                      {t.term}
                      {t.status && <span className="n">{t.status}</span>}
                    </span>
                  ))}
                </div>
                {g.id !== "coveredNotClaimable" && (
                  <div className="gap-ev">
                    <span className="muted">evidence:</span> <EvidenceList evidence={c.evidence} />
                  </div>
                )}
                {c.notes && <div className="faint gap-notes">{c.notes}</div>}
                {g.id === "sotSupported" ? (
                  <button
                    className="btn-ask btn-sm"
                    onClick={() =>
                      onAsk(
                        { kind: "term", term: c.jdTerms[0]?.term, code: c.evidence.find((e) => e.kind === "bullet" && e.strength === "implied")?.code },
                        { instruction: sotInstruction(c, jd) },
                      )
                    }
                  >
                    Write lead / bullet
                  </button>
                ) : g.id !== "coveredNotClaimable" ? (
                  <div className="never">never on the CV</div>
                ) : null}
              </div>
            ))}
          </div>
        );
      })}
      <SotLeads jd={jd} reloadKey={reloadKey} items={items} onAsk={onAsk} />
      <div className="kw-group">
        <h3>
          Unresolved keywords <span className="count">{data.unresolved.length}</span>
        </h3>
        <div className="hint">JD terms with no concept (keywords.concept_code is NULL or the term is not in the map).</div>
        {data.unresolved.length === 0 ? (
          <div className="faint" style={{ fontSize: 12.5 }}>Every term of this JD resolves to a concept.</div>
        ) : (
          <ul className="variants">
            {data.unresolved.map((u) => (
              <li key={u.term}>
                <div className="v-row">
                  <span className="v-kind mono">{u.type}</span>
                  <span className="v-text">
                    {u.term}
                    {!u.inKeywordMap && <span className="faint"> · not in the keyword map</span>}
                  </span>
                  <button className="btn-sm" onClick={() => onAsk({ kind: "term", term: u.term }, { instruction: classifyInstruction(u.term) })}>
                    Classify
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
