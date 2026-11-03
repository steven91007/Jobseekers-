// Pipeline page (#/pipeline/<tab>): the Google Sheet tabs 每日職缺 / 興趣職缺 / 投遞表單 through the
// Python backend (push_to_sheet.py → Sheets API). Decide 是/否 and write 備註 on the daily jobs, paste links of jobs you want,
// and see the submissions with their CV view and the 表單問答 button. Built for the phone too.
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import type { DailyRow, Decision, InterestAppendResult, PipelineList, Referral, SubmissionRow } from "../../shared/pipeline";
import { api } from "../api";
import { FormAnswersButton } from "./FormAnswers";
import { Badge } from "./ui";

export type PipelineTab = "daily" | "interest" | "submissions";

const TABS: { id: PipelineTab; label: string }[] = [
  { id: "daily", label: "每日職缺" },
  { id: "interest", label: "興趣職缺" },
  { id: "submissions", label: "投遞表單" },
];

const errText = (e: unknown) => String((e as Error)?.message ?? e);

function useList<T>(load: () => Promise<PipelineList<T>>, reloadKey: number) {
  const [data, setData] = useState<PipelineList<T> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    load()
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(errText(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [load, reloadKey]);
  return { data, setData, error, loading };
}

/** sheet_url + "#gid=…&range=A3" (the 投遞表單列 link points inside the spreadsheet). */
function sheetLink(sheetUrl: string | null, href: string | undefined): string | null {
  if (!href) return null;
  if (/^https?:\/\//i.test(href)) return href;
  if (href.startsWith("#") && sheetUrl) return sheetUrl.split("#")[0] + href;
  return null;
}

function scoreBand(score: number | ""): { cls: string; label: string } {
  if (score === "" || score == null) return { cls: "band-none", label: "未評分" };
  if (score >= 8) return { cls: "band-apply", label: "建議投" };
  if (score >= 5) return { cls: "band-maybe", label: "可考慮" };
  return { cls: "band-skip", label: "略過" };
}

function typeTone(type: string): string {
  if (type === "AI agent") return "badge-info";
  if (type === "ML learning") return "badge-ok";
  if (type === "automation") return "badge-warn";
  return "badge-neutral";
}

function stateTone(state: string): string {
  if (state === "已投遞") return "badge-ok";
  if (state === "已產 CV" || state === "待產 CV") return "badge-info";
  if (state === "失敗") return "badge-bad";
  return "badge-neutral";
}

function statusTone(status: string): string {
  if (/offer/i.test(status)) return "badge-ok";
  if (/拒絕/.test(status)) return "badge-bad";
  if (/不考慮/.test(status)) return "badge-neutral";
  if (/準備中/.test(status)) return "badge-warn";
  return status ? "badge-info" : "badge-neutral";
}

const byDateThenScore = (a: { date: string; score?: number | "" }, b: { date: string; score?: number | "" }) =>
  b.date.localeCompare(a.date) || (typeof b.score === "number" ? b.score : -1) - (typeof a.score === "number" ? a.score : -1);

function Score({ score }: { score: number | "" }) {
  const band = scoreBand(score);
  return (
    <div className={`pipe-score ${band.cls}`} title={`評分 ${score === "" ? "—" : `${score}/10`}:${band.label}`}>
      <span className="n">{score === "" ? "—" : score}</span>
      <span className="l">{band.label}</span>
    </div>
  );
}

function Place({ r }: { r: { date: string; city: string; country: string; mode: string } }) {
  const where = [r.city, r.country].filter(Boolean).join(", ");
  return <div className="pipe-meta muted">{[r.date, where, r.mode].filter(Boolean).join(" · ")}</div>;
}

function JobLink({ url, company, title }: { url: string; company: string; title: string }) {
  const label = company || title ? [company, title].filter(Boolean).join(" · ") : url;
  return (
    <a className="pipe-job" href={url} target="_blank" rel="noreferrer">
      {label} ↗
    </a>
  );
}

function RowLinks({ r, sheetUrl }: { r: { notion_url?: string; submission_row: string; submission_row_url?: string }; sheetUrl: string | null }) {
  const sub = sheetLink(sheetUrl, r.submission_row_url);
  return (
    <>
      {r.notion_url && (
        <a className="notion-link" href={r.notion_url} target="_blank" rel="noreferrer">
          Notion ↗
        </a>
      )}
      {r.submission_row &&
        (sub ? (
          <a className="notion-link" href={sub} target="_blank" rel="noreferrer">
            投遞表單 {r.submission_row} ↗
          </a>
        ) : (
          <span className="faint">投遞表單 {r.submission_row}</span>
        ))}
    </>
  );
}

// ---------- 每日職缺 ----------

type DecisionFilter = Decision | "全部";
const FILTERS: DecisionFilter[] = ["未確認", "是", "否", "全部"];
const decisionOf = (r: DailyRow): Decision => (r.decision === "是" || r.decision === "否" ? r.decision : "未確認");

function NoteEdit({ value, onSave }: { value: string; onSave: (note: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(value);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await onSave(text.trim());
      setEditing(false);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };
  if (!editing)
    return (
      <button
        className={`pipe-note btn-ghost${value ? "" : " empty-note"}`}
        onClick={() => {
          setText(value);
          setErr(null);
          setEditing(true);
        }}
        title="點一下編輯備註"
      >
        {value ? (
          <>
            <span className="label">備註</span> {value}
          </>
        ) : (
          "+ 備註"
        )}
      </button>
    );
  return (
    <div className="pipe-note-edit">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        autoFocus
        aria-label="備註"
        placeholder="例如:強調 RAG 專案、先問簽證"
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void save();
          if (e.key === "Escape") setEditing(false);
        }}
      />
      <div className="row-actions">
        <button className="btn-primary btn-sm" onClick={save} disabled={busy}>
          {busy ? "儲存中…" : "儲存"}
        </button>
        <button className="btn-sm" onClick={() => setEditing(false)} disabled={busy}>
          取消
        </button>
      </div>
      {err && <div className="error">備註沒有存到:{err}</div>}
    </div>
  );
}

function DailyTab({ reloadKey, onSheet }: { reloadKey: number; onSheet: (u: string | null) => void }) {
  const { data, setData, error, loading } = useList(api.pipelineDaily, reloadKey);
  const [filter, setFilter] = useState<DecisionFilter>("未確認");
  /** Cards changed here stay visible under the current filter until the next refresh. */
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const [rowErr, setRowErr] = useState<Record<string, string>>({});
  useEffect(() => onSheet(data?.sheetUrl ?? null), [data, onSheet]);
  useEffect(() => setTouched(new Set()), [reloadKey]);

  const patch = useCallback(
    (url: string, p: Partial<DailyRow>) => setData((d) => (d ? { ...d, rows: d.rows.map((r) => (r.url === url ? { ...r, ...p } : r)) } : d)),
    [setData],
  );
  const counts = useMemo(() => {
    const c: Record<DecisionFilter, number> = { 未確認: 0, 是: 0, 否: 0, 全部: 0 };
    for (const r of data?.rows ?? []) {
      c[decisionOf(r)]++;
      c.全部++;
    }
    return c;
  }, [data]);
  const shown = useMemo(
    () => (data?.rows ?? []).filter((r) => filter === "全部" || decisionOf(r) === filter || touched.has(r.url)).sort(byDateThenScore),
    [data, filter, touched],
  );

  const decide = async (r: DailyRow, d: Decision) => {
    const prev = r.decision;
    setTouched((t) => new Set(t).add(r.url));
    setRowErr(({ [r.url]: _, ...rest }) => rest);
    patch(r.url, { decision: d });
    try {
      await api.pipelineDecision(r.url, d);
    } catch (e) {
      patch(r.url, { decision: prev });
      setRowErr((m) => ({ ...m, [r.url]: `決定沒有存到:${errText(e)}` }));
    }
  };

  if (loading && !data) return <div className="loading">載入中…</div>;
  if (error && !data) return <div className="error">無法載入每日職缺:{error}</div>;
  return (
    <>
      {error && <div className="error">重新整理失敗:{error}</div>}
      <div className="pipe-filter" role="group" aria-label="依決定篩選">
        <span className="muted">決定</span>
        {FILTERS.map((f) => (
          <button key={f} className="btn-sm" aria-pressed={filter === f} onClick={() => setFilter(f)}>
            {f} <span className="faint">{counts[f]}</span>
          </button>
        ))}
      </div>
      {shown.length === 0 ? (
        <div className="empty panel">{counts.全部 ? `沒有「${filter}」的職缺` : "每日職缺目前是空的"}</div>
      ) : (
        <div className="pipe-list">
          {shown.map((r) => {
            const dec = decisionOf(r);
            return (
              <article key={r.url} className={`pipe-card panel dec-${dec === "是" ? "yes" : dec === "否" ? "no" : "open"}`}>
                <Score score={r.score} />
                <div className="pipe-main">
                  <div className="pipe-head">
                    {r.type && <Badge tone={typeTone(r.type)}>{r.type}</Badge>}
                    <JobLink url={r.url} company={r.company} title={r.title} />
                  </div>
                  <Place r={r} />
                  {r.reasons && <div className="pipe-reasons">{r.reasons}</div>}
                  <NoteEdit
                    value={r.note}
                    onSave={async (note) => {
                      await api.pipelineNote(r.url, note);
                      patch(r.url, { note });
                    }}
                  />
                  <div className="pipe-actions">
                    <Badge tone={stateTone(r.state)}>{r.state || "—"}</Badge>
                    <RowLinks r={r} sheetUrl={data?.sheetUrl ?? null} />
                    <span className="spacer" />
                    {(["是", "否"] as const).map((d) => (
                      <button
                        key={d}
                        className={`pipe-dec ${d === "是" ? "yes" : "no"}`}
                        aria-pressed={dec === d}
                        onClick={() => decide(r, dec === d ? "未確認" : d)}
                        title={dec === d ? "再按一次改回未確認" : d === "是" ? "要投:每日流程會產 CV" : "不投"}
                      >
                        {d}
                      </button>
                    ))}
                  </div>
                  {rowErr[r.url] && <div className="error">{rowErr[r.url]}</div>}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </>
  );
}

// ---------- 興趣職缺 ----------

function InterestTab({ reloadKey, onSheet, onAdded }: { reloadKey: number; onSheet: (u: string | null) => void; onAdded: () => void }) {
  const { data, error, loading } = useList(api.pipelineInterest, reloadKey);
  const [url, setUrl] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: string; text: string } | null>(null);
  useEffect(() => onSheet(data?.sheetUrl ?? null), [data, onSheet]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const u = url.trim();
    if (!/^https?:\/\/\S+$/i.test(u)) {
      setResult({ tone: "badge-bad", text: "請貼上 http(s) 開頭的職缺連結" });
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      const r: InterestAppendResult = await api.pipelineAddInterest(u, note.trim());
      if (r.added.length) {
        setResult({ tone: "badge-ok", text: `已加入興趣職缺(第 ${r.added[0].row} 列),下一次每日流程會評分並產 CV` });
        setUrl("");
        setNote("");
        onAdded();
      } else {
        const s = r.skipped[0];
        setResult({
          tone: "badge-warn",
          text: s?.reason === "duplicate" ? `已經在「${s.tab ?? "Sheet"}」裡,沒有重複加入` : `沒有加入:${s?.reason ?? "未知原因"}`,
        });
      }
    } catch (err) {
      setResult({ tone: "badge-bad", text: `沒有加入:${errText(err)}` });
    } finally {
      setBusy(false);
    }
  };

  const rows = useMemo(() => [...(data?.rows ?? [])].sort(byDateThenScore), [data]);
  return (
    <>
      <form className="pipe-paste panel" onSubmit={submit}>
        <label htmlFor="pipe-url">
          <b>貼連結</b> <span className="muted">想投但每日流程沒找到的職缺</span>
        </label>
        <input id="pipe-url" type="url" inputMode="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" required />
        <input type="text" value={note} onChange={(e) => setNote(e.target.value)} placeholder="備註(選填)" aria-label="備註(選填)" />
        <button className="btn-primary" type="submit" disabled={busy || !url.trim()}>
          {busy ? "加入中…" : "加入"}
        </button>
        {result && (
          <div className={`badge ${result.tone} pipe-result`} role="status">
            {result.text}
          </div>
        )}
      </form>
      {loading && !data ? (
        <div className="loading">載入中…</div>
      ) : error && !data ? (
        <div className="error">無法載入興趣職缺:{error}</div>
      ) : (
        <>
          {error && <div className="error">重新整理失敗:{error}</div>}
          {rows.length === 0 ? (
            <div className="empty panel">還沒有興趣職缺。貼上一個連結試試。</div>
          ) : (
            <div className="pipe-list">
              {rows.map((r) => (
                <article key={r.url} className="pipe-card panel">
                  <Score score={r.score} />
                  <div className="pipe-main">
                    <div className="pipe-head">
                      {r.type && <Badge tone={typeTone(r.type)}>{r.type}</Badge>}
                      <JobLink url={r.url} company={r.company} title={r.title} />
                    </div>
                    <Place r={r} />
                    {r.note && (
                      <div className="pipe-note-text">
                        <span className="label">備註</span> {r.note}
                      </div>
                    )}
                    {r.reasons && <div className="pipe-reasons">{r.reasons}</div>}
                    <div className="pipe-actions">
                      <Badge tone={stateTone(r.state)}>{r.state || "尚未處理(等每日流程)"}</Badge>
                      <RowLinks r={r} sheetUrl={data?.sheetUrl ?? null} />
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

// ---------- 投遞表單 ----------

function LinkOrText({ label, text, url }: { label: string; text: string; url?: string }) {
  if (!text && !url) return null;
  return (
    <span className="pipe-kv">
      <span className="muted">{label}</span>{" "}
      {url ? (
        <a href={url} target="_blank" rel="noreferrer">
          {text || "開啟"} ↗
        </a>
      ) : (
        <span className="mono">{text}</span>
      )}
    </span>
  );
}

function SubmissionApply({ jd, company, onOpenRun }: { jd: string; company: string; onOpenRun?: (jd: string, runId: string) => void }) {
  return <FormAnswersButton jd={jd} company={company} onStarted={(runId) => onOpenRun?.(jd, runId)} />;
}

const GROUP_LABEL: Record<string, string> = { team: "同團隊", manager: "主管", taiwanese: "台灣人" };

function referralTone(status: string): string {
  if (status.includes("✅")) return "badge-ok";
  if (status.includes("❌")) return "badge-bad";
  if (status.includes("⚠")) return "badge-warn";
  return "badge-neutral";
}

/** Clipboard API needs a secure context (localhost or the https Tailscale URL); fall back to a hidden textarea. */
async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch {
    /* fall through */
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  if (!ok) throw new Error("瀏覽器不允許複製");
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "ok" | "err">("idle");
  return (
    <button
      className="btn-sm"
      onClick={() =>
        copyText(text).then(
          () => setState("ok"),
          () => setState("err"),
        ).finally(() => setTimeout(() => setState("idle"), 1800))
      }
    >
      {state === "ok" ? "已複製 ✓" : state === "err" ? "複製失敗" : label}
    </button>
  );
}

function ReferralList({ r, onSent }: { r: SubmissionRow; onSent: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  const list = r.referrals ?? [];
  if (!list.length) return null;
  const markSent = async (p: Referral) => {
    setBusy(p.name);
    setErr(null);
    try {
      await api.pipelineReferralSent(r.url, p.name);
      setSent(p.name);
      onSent();
    } catch (e) {
      setErr(`沒有標記成功:${errText(e)}`);
    } finally {
      setBusy(null);
    }
  };
  return (
    <details className="pipe-refs" open={!r.referral_status}>
      <summary>
        內推候選 {list.length} 位{r.referral_status ? ` · ${r.referral_status}` : ""}
      </summary>
      <ul>
        {list.map((p, i) => (
          <li key={`${p.name}-${i}`} className={i === 0 ? "first" : undefined}>
            <div className="pipe-head">
              {i === 0 && <Badge tone="badge-ok">推薦</Badge>}
              {p.group && <Badge tone="badge-neutral">{GROUP_LABEL[p.group] ?? p.group}</Badge>}
              {p.url ? (
                <a className="pipe-job" href={p.url} target="_blank" rel="noreferrer">
                  {p.name}
                  {p.title ? ` · ${p.title}` : ""} ↗
                </a>
              ) : (
                <b>
                  {p.name}
                  {p.title ? ` · ${p.title}` : ""}
                </b>
              )}
            </div>
            {p.why && <div className="muted pipe-why">{p.why}</div>}
            <div className="pipe-actions">
              {p.full && <CopyButton text={p.full} label="複製完整訊息" />}
              {p.note && <CopyButton text={p.note} label={`複製邀請附言(${[...p.note].length} 字)`} />}
              <button className="btn-sm" onClick={() => markSent(p)} disabled={busy !== null} title="內推狀態改成 ⚠️等待回覆,並在求職過程記一行">
                {busy === p.name ? "標記中…" : sent === p.name ? "已標記 ✓" : "標記已送出"}
              </button>
            </div>
          </li>
        ))}
      </ul>
      {err && <div className="error">{err}</div>}
    </details>
  );
}

function SubmissionsTab({ reloadKey, onSheet, onOpenRun }: { reloadKey: number; onSheet: (u: string | null) => void; onOpenRun?: (jd: string, runId: string) => void }) {
  const [localKey, setLocalKey] = useState(0);
  const { data, error, loading } = useList(api.pipelineSubmissions, reloadKey + localKey);
  useEffect(() => onSheet(data?.sheetUrl ?? null), [data, onSheet]);
  const rows = useMemo(() => [...(data?.rows ?? [])].sort((a: SubmissionRow, b: SubmissionRow) => b.date.localeCompare(a.date)), [data]);
  if (loading && !data) return <div className="loading">載入中…</div>;
  if (error && !data) return <div className="error">無法載入投遞表單:{error}</div>;
  return (
    <>
      {error && <div className="error">重新整理失敗:{error}</div>}
      {rows.length === 0 ? (
        <div className="empty panel">投遞表單目前是空的</div>
      ) : (
        <div className="pipe-list">
          {rows.map((r) => (
            <article key={`${r.row}-${r.url}`} className="pipe-card pipe-sub panel">
              <div className="pipe-main">
                <div className="pipe-head">
                  {r.type && <Badge tone={typeTone(r.type)}>{r.type}</Badge>}
                  {r.source && <Badge tone={r.source === "興趣" ? "badge-warn" : "badge-neutral"}>{r.source}</Badge>}
                  <JobLink url={r.url} company={r.company} title={r.title} />
                </div>
                <Place r={r} />
                <div className="pipe-kvs">
                  <Badge tone={statusTone(r.status)}>{r.status || "—"}</Badge>
                  {r.referral_status && (
                    <Badge tone={referralTone(r.referral_status)} title="內推狀態">
                      內推 {r.referral_status}
                    </Badge>
                  )}
                  {r.ats !== "" && r.ats != null && (
                    <span className="pipe-kv">
                      <span className="muted">ATS</span> <b>{r.ats}</b>
                    </span>
                  )}
                  <LinkOrText label="CV" text={r.cv} url={r.cv_url} />
                  <LinkOrText label="Cover letter" text={r.cover_letter} url={r.cover_letter_url} />
                  {r.notion_url && (
                    <a className="notion-link" href={r.notion_url} target="_blank" rel="noreferrer">
                      Notion ↗
                    </a>
                  )}
                  {r.resume_version && <LinkOrText label="履歷版本" text={r.resume_version} url={r.resume_version_url} />}
                </div>
                {r.process && (
                  <div className="pipe-process">
                    <span className="muted">求職過程</span>
                    <div>{r.process}</div>
                  </div>
                )}
                <ReferralList r={r} onSent={() => setLocalKey((k) => k + 1)} />
                {r.app_dir && (
                  <div className="pipe-dir">
                    <span className="muted">應用資料夾</span>{" "}
                    {r.jd ? (
                      <a className="mono" href={`#/app/${encodeURIComponent(r.jd)}`} title="開啟這份 CV">
                        {r.app_dir}
                      </a>
                    ) : (
                      <span className="mono faint" title="dashboard 的 applications 表裡沒有這個資料夾">
                        {r.app_dir}
                      </span>
                    )}
                  </div>
                )}
                {r.jd && (
                  <div className="pipe-actions">
                    <a className="btn btn-sm" href={`#/app/${encodeURIComponent(r.jd)}`}>
                      CV 檢視
                    </a>
                    <SubmissionApply jd={r.jd} company={r.company} onOpenRun={onOpenRun} />
                  </div>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}

// ---------- page ----------

export function PipelineView({ tab, onOpenRun }: { tab: PipelineTab; onOpenRun?: (jd: string, runId: string) => void }) {
  const [reloadKey, setReloadKey] = useState(0);
  const [sheetUrl, setSheetUrl] = useState<string | null>(null);
  const refresh = () => setReloadKey((k) => k + 1);
  const label = TABS.find((t) => t.id === tab)?.label ?? "";
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Pipeline</h1>
          <div className="muted">Google Sheet 的每日職缺、興趣職缺與投遞表單。決定與備註會直接寫回 Sheet。</div>
        </div>
        <div className="row-actions">
          {sheetUrl && (
            <a className="btn btn-sm" href={sheetUrl} target="_blank" rel="noreferrer" title={`在 Google Sheet 開啟(「${label}」分頁)`}>
              Google Sheet:{label} ↗
            </a>
          )}
          <button className="btn-sm" onClick={refresh}>
            重新整理
          </button>
        </div>
      </div>
      <div className="tabs pipe-tabs" role="tablist" aria-label="Pipeline 分頁">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => {
              window.location.hash = `#/pipeline/${t.id}`;
            }}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" aria-label={label}>
        {tab === "daily" && <DailyTab reloadKey={reloadKey} onSheet={setSheetUrl} />}
        {tab === "interest" && <InterestTab reloadKey={reloadKey} onSheet={setSheetUrl} onAdded={refresh} />}
        {tab === "submissions" && <SubmissionsTab reloadKey={reloadKey} onSheet={setSheetUrl} onOpenRun={onOpenRun} />}
      </div>
    </>
  );
}
