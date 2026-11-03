import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ApplicationDetail, ClaudeTarget, CoverageGroup, CvItem, KeywordRow, TargetLibrary } from "../../shared/types";
import { api } from "../api";
import { Badge, BoldText, Highlight, Modal, layoutTone, scoreColor, statusTone, verdictTone } from "./ui";
import { PickModal } from "./PickModal";
import { FormAnswersButton } from "./FormAnswers";
import { AlignModal, ClaimBadge, ConceptKeywordGroups, GapsPanel } from "./ConceptBits";
import { BulletRefList, ItemNotionLink, NotionLink, VariantList, swapInstruction, variantInstruction } from "./LibraryBits";

interface Props {
  jd: string;
  reloadKey: number;
  changedLines: string[];
  onAsk: (jd: string, target: ClaudeTarget, opts?: { instruction?: string; library?: TargetLibrary }) => void;
  /** Show a run that the view started itself (Apply) in the Claude drawer. */
  onOpenRun?: (jd: string, runId: string) => void;
}

const GROUPS: { id: CoverageGroup; title: string; cls: string; hint: string }[] = [
  { id: "found", title: "Found", cls: "group-found", hint: "On this CV (whole-word, as the ATS check counts them)." },
  { id: "covered elsewhere", title: "Missing — covered elsewhere", cls: "group-covered", hint: "The library carries it in another bullet or Skills item: a pick or wording fix." },
  { id: "SoT-supported wording", title: "Missing — SoT-supported wording", cls: "group-sot", hint: "True per the SoT; needs wording in a library item." },
  { id: "gap", title: "Missing — gap", cls: "group-gap", hint: "No evidence in the library." },
  { id: "do not claim", title: "Do not claim", cls: "group-dnc", hint: "Technically true but misleading or padding." },
  { id: "unclassified", title: "Unclassified", cls: "", hint: "New or auto-ingested; classify with library_db.py set-coverage." },
];

const TYPE_SHORT = { required: "req", preferred: "pref", keyword: "kw" } as const;

function groupOf(k: KeywordRow): CoverageGroup {
  return k.found ? "found" : k.library.group;
}

function targetFor(it: CvItem): ClaudeTarget {
  return { kind: it.kind, code: it.pick?.code ?? it.label, text: it.text };
}

function libFor(it: CvItem): TargetLibrary | undefined {
  return it.library ? { item: it.library } : undefined;
}

function keywordLib(k: KeywordRow): TargetLibrary {
  return { keyword: { term: k.term, notionUrl: k.library.notionUrl, bulletRefs: k.library.bulletRefs, skillRefs: k.library.skillRefs } };
}

export function CvView({ jd, reloadKey, changedLines, onAsk, onOpenRun }: Props) {
  const [data, setData] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [term, setTerm] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [modal, setModal] = useState<null | "pick" | "align" | "brief" | "review" | "checks">(null);
  const [view, setView] = useState<"concepts" | "coverage" | "gaps">(() => {
    try {
      const v = localStorage.getItem("cvd-kw-view");
      return v === "coverage" || v === "gaps" ? v : "concepts";
    } catch {
      return "concepts";
    }
  });
  const pickView = (v: "concepts" | "coverage" | "gaps") => {
    setView(v);
    try {
      localStorage.setItem("cvd-kw-view", v);
    } catch {
      /* storage unavailable */
    }
  };
  const filterRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    api
      .application(jd)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e.message ?? e)));
    return () => {
      alive = false;
    };
  }, [jd, reloadKey]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      const typing = el.tagName === "INPUT" || el.tagName === "TEXTAREA";
      if (e.key === "/" && !typing) {
        e.preventDefault();
        filterRef.current?.focus();
      } else if (e.key === "Escape" && !typing && term) setTerm(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [term]);

  const kwByTerm = useMemo(() => new Map((data?.keywords ?? []).map((k) => [k.term, k])), [data]);
  const changed = useMemo(() => new Set(changedLines), [changedLines]);

  const selectTerm = useCallback(
    (t: string) => {
      setTerm((cur) => (cur === t ? null : t));
      const first = data?.hits.byTerm[t]?.[0];
      if (first) requestAnimationFrame(() => document.getElementById(`cv-${first}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
    },
    [data],
  );

  if (error) return <div className="error">Could not load {jd}: {error}</div>;
  if (!data) return <div className="loading">Loading {jd}…</div>;

  const { row, ats, layout, review } = data;
  const litTerms = new Set(hovered ? data.hits.byItem[hovered] ?? [] : []);
  const termItems = new Set(term ? data.hits.byTerm[term] ?? [] : []);
  const selected = term ? kwByTerm.get(term) : undefined;

  // ---------------------------------------------------------------- left pane
  const renderBlock = (it: CvItem) => {
    const cls = [
      "block",
      `kind-${it.kind}`,
      hovered === it.id ? "hovered" : "",
      termItems.has(it.id) ? "selected" : term && termItems.size ? "dimmed" : "",
      changed.has(it.raw) ? "changed" : "",
    ].join(" ");
    const p = it.pick;
    return (
      <div
        key={it.id}
        id={`cv-${it.id}`}
        className={cls}
        tabIndex={0}
        onMouseEnter={() => setHovered(it.id)}
        onMouseLeave={() => setHovered((h) => (h === it.id ? null : h))}
        onFocus={() => setHovered(it.id)}
        onBlur={() => setHovered((h) => (h === it.id ? null : h))}
        onKeyDown={(e) => {
          if (e.key === "Enter" && e.target === e.currentTarget) onAsk(jd, targetFor(it), { library: libFor(it) });
        }}
        aria-label={`${it.kind.replace("_", " ")}${p ? ` ${p.code}` : ""}. Press Enter to ask Claude.`}
      >
        <div className={`block-meta ${p || !it.scanned || changed.has(it.raw) ? "" : "bare"}`}>
          {p && (
            <span className="badge badge-code" title={p.conf ? `Confirmations: ${p.conf}` : undefined}>
              {p.code}
              {p.variantLabel && <span className="v"> · {p.variantLabel}</span>}
            </span>
          )}
          <ItemNotionLink lib={it.library} />
          {p?.status && <Badge tone={statusTone(p.status)}>{p.status}</Badge>}
          {p?.conf && <span className="faint mono" style={{ fontSize: 11 }}>{p.conf}</span>}
          {!it.scanned && it.kind !== "name" && <span className="not-scanned">not scanned by the ATS check</span>}
          {changed.has(it.raw) && <Badge tone="badge-ok">changed</Badge>}
          <span className="spacer" />
          <button className="btn-ask ask" onClick={() => onAsk(jd, targetFor(it), { library: libFor(it) })} tabIndex={-1}>
            Ask Claude
          </button>
        </div>
        <div className="block-text">{blockText(it, term)}</div>
        {it.library && (it.library.variants.length > 1 || (it.library.skillItems ?? []).some((s) => s.notionUrl)) && (
          <details className="variants-box" onKeyDown={(e) => e.stopPropagation()}>
            <summary>Variants ({it.library.variants.length})</summary>
            {(it.library.skillItems ?? []).some((s) => s.notionUrl) && (
              <div className="lib-links">
                {it.library.skillItems!.map((s) => (s.notionUrl ? <NotionLink key={s.ord} url={s.notionUrl} label={s.item} /> : null))}
              </div>
            )}
            <VariantList
              variants={it.library.variants}
              action="Use"
              onPick={(v) => onAsk(jd, targetFor(it), { instruction: variantInstruction(it.pick!.code, v), library: libFor(it) })}
            />
          </details>
        )}
        {it.hits.length > 0 && (
          <div className="chips">
            {it.hits.map((t) => {
              const k = kwByTerm.get(t);
              return (
                <button
                  key={t}
                  className={`chip small ${k?.found ? "found" : ""} ${t === term ? "active" : ""}`}
                  onClick={() => selectTerm(t)}
                  tabIndex={-1}
                  title={k ? `${k.type} · JD ×${k.jdCount}` : t}
                >
                  {t}
                </button>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  const sections: { title: string; items: CvItem[] }[] = [];
  for (const it of data.cv.lines) {
    if (it.kind === "section") sections.push({ title: it.text, items: [] });
    else if (it.kind === "name") sections.push({ title: "Header", items: [it] });
    else sections[sections.length - 1]?.items.push(it);
  }

  const renderSection = (s: { title: string; items: CvItem[] }) => (
    <section className="cv-section" key={s.title}>
      <h3>{s.title}</h3>
      {s.items.map((it) =>
        it.kind === "role" ? (
          <div className="role-head" key={it.id}>
            <span>{it.role}</span>
            <span className="years">{it.years}</span>
          </div>
        ) : it.kind === "name" ? (
          <div key={it.id} className="role-head" style={{ marginTop: 0 }}>
            {it.text}
          </div>
        ) : (
          renderBlock(it)
        ),
      )}
    </section>
  );

  // ---------------------------------------------------------------- right pane
  const f = filter.trim().toLowerCase();
  const visible = data.keywords.filter((k) => !f || k.term.toLowerCase().includes(f));
  const chip = (k: KeywordRow) => (
    <button
      key={k.term}
      className={["chip", k.found ? "found" : "", k.type === "required" ? "req" : "", litTerms.has(k.term) ? "lit" : "", term === k.term ? "active" : ""].join(" ")}
      onClick={() => selectTerm(k.term)}
      aria-pressed={term === k.term}
      title={`${k.type}${k.concept ? ` · ${k.concept.code}` : ""}${k.library.notes ? ` · ${k.library.notes}` : ""}`}
    >
      <span className="t">{TYPE_SHORT[k.type]}</span>
      {k.term}
      {k.jdCount > 1 && <span className="n">×{k.jdCount}</span>}
    </button>
  );

  const sub = Object.entries(ats.subScores);

  return (
    <>
      <div className="panel cv-head">
        <div className="cv-head-top">
          <div style={{ minWidth: 0 }}>
            <h1>
              {row.company} <span className="muted" style={{ fontWeight: 500 }}>· {row.title}</span>
            </h1>
            <div className="headline">{row.headline}</div>
            <div className="faint mono" style={{ marginTop: 2, overflowWrap: "anywhere" }}>{row.dir}</div>
          </div>
          <div className="cv-actions">
            {data.pdfUrl && (
              <a className="btn" href={data.pdfUrl} target="_blank" rel="noreferrer">
                Open PDF
              </a>
            )}
            {data.notion.applicationUrl && (
              <a className="btn" href={data.notion.applicationUrl} target="_blank" rel="noreferrer">
                Application in Notion ↗
              </a>
            )}
            <a className="btn" href={data.notion.rootUrl} target="_blank" rel="noreferrer">
              Personal experiences ↗
            </a>
            <button onClick={() => setModal("pick")}>DB suggestion</button>
            <button onClick={() => setModal("align")} title="library_db.py align: put the JD's noun for each concept on the CV">
              Align
            </button>
            <button onClick={() => setModal("brief")}>JD brief</button>
            <button onClick={() => setModal("review")}>Review</button>
            <FormAnswersButton jd={jd} company={row.company} onStarted={(runId) => onOpenRun?.(jd, runId)} />
          </div>
        </div>
        <div className="metrics">
          <div className="metric">
            <div className="label">ATS overall</div>
            <div className="big" style={{ color: scoreColor(ats.overall) }}>
              {ats.overall ?? "—"}
            </div>
            <div className="subscores">
              {sub.map(([k, v]) => (
                <FragmentRow key={k} label={k.replace(/_/g, " ")} value={v} />
              ))}
            </div>
          </div>
          <div className="metric">
            <div className="label">Keywords</div>
            <div className="big">
              {ats.found}
              <small>/{ats.nTerms}</small>
            </div>
            <div className="muted" style={{ fontSize: 12 }}>
              {ats.keywordPercent ?? "—"}% matched
            </div>
          </div>
          <div className="metric">
            <div className="label">Layout</div>
            <div style={{ marginTop: 6 }}>
              <Badge tone={layoutTone(layout.status)}>{layout.status ?? "—"}</Badge>{" "}
              <span className="muted">{layout.utilisation ?? ""} page</span>
            </div>
            <button className="btn-sm btn-ghost" style={{ marginTop: 4, paddingLeft: 0 }} onClick={() => setModal("checks")}>
              {layout.checks.filter((c) => c.status !== "PASS").length || "No"} non-pass checks →
            </button>
          </div>
          <div className="metric">
            <div className="label">Review</div>
            <div className="big">
              {review.hr ?? "—"}
              <small> HR</small> {review.hm ?? "—"}
              <small> HM</small>
            </div>
            <Badge tone={verdictTone(review.verdict)}>{review.verdict ?? "no verdict"}</Badge>
          </div>
        </div>
      </div>

      <div className="panes">
        <div className="pane-left">{sections.map(renderSection)}</div>

        <aside className="panel pane-right" aria-label="JD keywords">
          <div className="panel-head">
            <h2>JD keywords</h2>
            <span className="muted" style={{ fontSize: 12 }}>
              {data.keywords.length} terms · {data.concepts.length} concepts
            </span>
          </div>
          <div className="tabs kw-tabs" role="tablist" aria-label="Keyword view">
            {(["concepts", "coverage", "gaps"] as const).map((v) => (
              <button key={v} role="tab" aria-selected={view === v} onClick={() => pickView(v)}>
                {v === "concepts" ? "By concept" : v === "coverage" ? "By coverage" : "Gaps"}
              </button>
            ))}
          </div>
          <div className="panel-body">
            <div className="kw-tools">
              <input
                ref={filterRef}
                type="search"
                placeholder="Filter terms  ( / )"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                aria-label="Filter terms"
              />
            </div>
            <div className="legend" style={{ marginBottom: 10 }}>
              <span><b>req</b> required</span>
              <span><b>pref</b> preferred</span>
              <span><b>kw</b> keyword</span>
              <span>×n JD count</span>
            </div>

            {selected && (
              <div className="term-card" role="status">
                <div className="row">
                  <strong>{selected.term}</strong>
                  <div className="row-actions">
                    {!selected.found && (
                      <button
                        className="btn-ask"
                        onClick={() =>
                          onAsk(jd, { kind: "term", term: selected.term, code: selected.library.bullets[0], text: undefined }, { library: keywordLib(selected) })
                        }
                      >
                        Ask Claude
                      </button>
                    )}
                    <button className="btn-sm btn-ghost" onClick={() => setTerm(null)} aria-label="Clear selection">
                      ✕
                    </button>
                  </div>
                </div>
                <div style={{ fontSize: 12.5, marginTop: 2 }}>
                  {selected.items.length ? (
                    <>On this CV in {selected.items.length} block{selected.items.length > 1 ? "s" : ""}.</>
                  ) : selected.found ? (
                    <>Found by the ATS scan, but not inside a single block.</>
                  ) : (
                    <>Not on this CV.</>
                  )}
                </div>
                <dl>
                  <dt>Type</dt>
                  <dd>{selected.type} · JD ×{selected.jdCount}</dd>
                  <dt>Concept</dt>
                  <dd>
                    {selected.concept ? (
                      <>
                        {selected.concept.name} <span className="mono faint">{selected.concept.code}</span> <ClaimBadge claim={selected.concept.claim} />
                      </>
                    ) : (
                      <span className="faint">unresolved</span>
                    )}
                  </dd>
                  <dt>Library</dt>
                  <dd>
                    {selected.library.coverage ?? "not in the keyword map"} <NotionLink url={selected.library.notionUrl} />
                  </dd>
                  {selected.library.bullets.length > 0 && selected.found && (
                    <>
                      <dt>Bullets</dt>
                      <dd className="mono">
                        {selected.library.bulletRefs.map((b, i) => (
                          <span key={b.code}>
                            {i > 0 && ", "}
                            {b.notionUrl || b.projectNotionUrl ? (
                              <a href={(b.notionUrl ?? b.projectNotionUrl)!} target="_blank" rel="noreferrer">
                                {b.code}
                              </a>
                            ) : (
                              b.code
                            )}
                          </span>
                        ))}
                      </dd>
                    </>
                  )}
                  {selected.library.skills.length > 0 && (
                    <>
                      <dt>Skills</dt>
                      <dd>
                        {selected.library.skillRefs.map((sk, i) => (
                          <span key={sk.name}>
                            {i > 0 && ", "}
                            {sk.notionUrl ? (
                              <a href={sk.notionUrl} target="_blank" rel="noreferrer">
                                {sk.name}
                              </a>
                            ) : (
                              sk.name
                            )}
                          </span>
                        ))}
                      </dd>
                    </>
                  )}
                  {selected.library.otherJds.length > 0 && (
                    <>
                      <dt>Other JDs</dt>
                      <dd>{selected.library.otherJds.join(", ")}</dd>
                    </>
                  )}
                  {selected.library.notes && (
                    <>
                      <dt>Notes</dt>
                      <dd>{selected.library.notes}</dd>
                    </>
                  )}
                </dl>
                {!selected.found && selected.library.bulletRefs.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <div className="muted" style={{ fontSize: 12 }}>Covering bullets</div>
                    <BulletRefList
                      refs={selected.library.bulletRefs}
                      action="Swap in"
                      onPick={(b, l) =>
                        onAsk(jd, { kind: "term", term: selected.term, code: b.code }, { instruction: swapInstruction(b, l), library: keywordLib(selected) })
                      }
                    />
                  </div>
                )}
              </div>
            )}

            {view === "gaps" && <GapsPanel jd={jd} reloadKey={reloadKey} items={data.cv.lines} onAsk={(t, o) => onAsk(jd, t, o)} />}
            {view === "concepts" && <ConceptKeywordGroups concepts={data.concepts} keywords={data.keywords} filter={filter} chip={chip} />}
            {view === "coverage" && GROUPS.map((g) => {
              const list = visible.filter((k) => groupOf(k) === g.id);
              if (!list.length) return null;
              return (
                <div className={`kw-group ${g.cls}`} key={g.id}>
                  <h3>
                    {g.title} <span className="count">{list.length}</span>
                  </h3>
                  <div className="hint">{g.hint}</div>
                  <div className="chips">{list.map(chip)}</div>
                </div>
              );
            })}
            {view === "coverage" && !visible.length && <div className="empty">No term matches “{filter}”.</div>}
          </div>
        </aside>
      </div>

      {modal === "pick" && <PickModal jd={jd} onClose={() => setModal(null)} />}
      {modal === "align" && <AlignModal jd={jd} items={data.cv.lines} onClose={() => setModal(null)} onAsk={(t, o) => onAsk(jd, t, o)} />}
      {modal === "brief" && (
        <Modal title={`JD brief — ${row.company}`} onClose={() => setModal(null)}>
          <div className="brief">{data.jdBrief ? <BoldText text={data.jdBrief} /> : "No 01_jd.md in this folder."}</div>
        </Modal>
      )}
      {modal === "review" && (
        <Modal title={`Review — ${row.company}`} onClose={() => setModal(null)}>
          <p style={{ marginTop: 0 }}>{review.headline ?? "No 04_review.md."}</p>
          {review.edits.length > 0 && (
            <table className="plain">
              <thead>
                <tr>{review.editsHeader.map((h) => <th key={h}>{h}</th>)}</tr>
              </thead>
              <tbody>
                {review.edits.map((e, i) => (
                  <tr key={i}>{e.cells.map((c, j) => <td key={j}><BoldText text={c} /></td>)}</tr>
                ))}
              </tbody>
            </table>
          )}
          {ats.parseChecks.length > 0 && (
            <>
              <h3 style={{ margin: "16px 0 6px" }}>ATS parse checks</h3>
              <table className="plain">
                <thead>
                  <tr><th>File</th><th>Extraction + layout</th><th>Content</th><th>Non-pass</th></tr>
                </thead>
                <tbody>
                  {ats.parseChecks.map((p) => (
                    <tr key={p.file}>
                      <td className="mono">{p.file}</td>
                      <td>{p.extractionLayout}</td>
                      <td>{p.content}</td>
                      <td>{p.flagged.join("; ") || "none"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </Modal>
      )}
      {modal === "checks" && (
        <Modal title="Layout checks (06_layout.json)" onClose={() => setModal(null)}>
          <table className="plain">
            <thead>
              <tr><th>Check</th><th>Status</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {layout.checks.map((c) => (
                <tr key={c.check}>
                  <td className="mono">{c.check}</td>
                  <td><Badge tone={layoutTone(c.status)}>{c.status}</Badge></td>
                  <td className="mono" style={{ overflowWrap: "anywhere" }}>{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Modal>
      )}
    </>
  );
}

function FragmentRow({ label, value }: { label: string; value: number }) {
  return (
    <>
      <span className="muted">{label}</span>
      <span className="track" aria-hidden>
        <span className="fill" style={{ width: `${value}%`, display: "block" }} />
      </span>
      <span style={{ fontVariantNumeric: "tabular-nums" }}>{value}</span>
    </>
  );
}

function blockText(it: CvItem, active: string | null) {
  const hl = (s: string | undefined) => <Highlight text={s ?? ""} terms={it.hits} active={active} />;
  switch (it.kind) {
    case "bullet":
    case "other_bullet":
      return it.lead ? (
        <>
          <strong>{hl(it.lead)}</strong> — {hl(it.body)}
        </>
      ) : (
        hl(it.body ?? it.text)
      );
    case "card":
      return (
        <>
          <strong>{hl(it.title)}</strong> — {hl(it.context)}
        </>
      );
    case "skill_row":
      return (
        <>
          <span className="label">{it.label}: </span>
          {hl(it.items?.join(" · "))}
        </>
      );
    case "headline":
      return <strong style={{ letterSpacing: "0.03em" }}>{hl(it.text)}</strong>;
    case "contact":
      return <span className="muted">{it.text}</span>;
    case "other":
      return it.label ? (
        <>
          <span className="label">{it.label}: </span>
          {hl(it.text)}
        </>
      ) : (
        hl(it.text)
      );
    default:
      return hl(it.text);
  }
}
