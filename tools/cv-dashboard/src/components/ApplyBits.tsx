// Auto apply in the CV view: "Auto apply" queues a job-apply fill run that stops at the review page;
// the "Apply status" panel shows its screenshots, field table and blockers (works from a phone over
// Tailscale); "Confirm & submit" (two confirmations) is the only way the application gets submitted.
import { useCallback, useEffect, useState } from "react";
import type { ApplyField, ApplyInfo, RunStatus } from "../../shared/types";
import { api } from "../api";
import { LiveBrowser } from "./LiveBrowser";
import { Badge, Modal } from "./ui";

type Info = ApplyInfo & { runId: string | null; runStatus: RunStatus | null };

const base = (p: string | null) => (p ? p.split("/").pop() : null);

export function useApplyInfo(jd: string, reloadKey: number) {
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api
      .applyInfo(jd)
      .then((d) => {
        setInfo(d);
        setError(null);
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, [jd]);
  useEffect(load, [load, reloadKey]);
  // poll while a fill run is queued/running or a submit is in progress
  const live = info?.runStatus === "running" || info?.runStatus === "queued" || info?.submitting;
  useEffect(() => {
    if (!live) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [live, load]);
  return { info, error, reload: load };
}

export function ApplyButton({ jd, company, info, onStarted }: { jd: string; company: string; info: Info | null; onStarted: (runId: string) => void }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const live = info?.runStatus === "running" || info?.runStatus === "queued";
  const submitted = info?.status?.state === "submitted";

  const start = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.startApply(jd, url.trim());
      setOpen(false);
      onStarted(r.runId);
    } catch (e) {
      setErr(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        onClick={() => {
          setUrl(info?.url ?? "");
          setErr(null);
          setOpen(true);
        }}
        disabled={!info || live || !info.pluginRoot || info.submitting}
        title={!info?.pluginRoot ? "未安裝 job-apply 外掛" : live ? "Auto apply 正在執行" : "自動填寫應徵表單;送出前會停下"}
      >
        {live ? "填表中…" : "Auto apply(自動填表)"}
      </button>
      {open && info && (
        <Modal
          title={`Auto apply(自動填表)· ${company}`}
          onClose={() => setOpen(false)}
          closeLabel="關閉"
          actions={
            <button className="btn-primary" onClick={start} disabled={busy || !url.trim()}>
              {busy ? "啟動中…" : "填寫表單(不送出)"}
            </button>
          }
        >
          {submitted && (
            <p className="badge-warn" style={{ padding: 6 }}>
              這份申請已在 {new Date(info.status!.submittedAt ?? info.status!.finishedAt).toLocaleString()} 送出。再填一次不會送出任何東西,但請三思。
            </p>
          )}
          <p>
            Claude 會在你電腦上的投遞瀏覽器打開這個職缺,用你的 CV 填表。它<b>停在確認頁,絕不送出</b>。薪資、簽證、同意條款等自訂問題會留白。
            完成後你在這裡檢查截圖與欄位,再自己按<b>確認並送出</b>。
          </p>
          <label className="muted" style={{ display: "block", marginTop: 10 }}>
            職缺/申請表連結{info.urlSource ? <span className="faint">(來自 {info.urlSource})</span> : null}
          </label>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            style={{ width: "100%", marginTop: 4 }}
            placeholder="https://…(最好是公司自己的申請表)"
          />
          <ul className="muted" style={{ marginTop: 10, paddingLeft: 18 }}>
            <li>CV:{base(info.cvPdf) ?? <b>缺少</b>}</li>
            <li>
              Cover letter:{" "}
              {info.coverLetter?.available ? (
                <a href={api.applyCoverLetterUrl(jd)} target="_blank" rel="noreferrer">
                  {info.coverLetter.pdf}
                </a>
              ) : (
                "還沒有。Claude 會先用 cover-letter skill 寫一封(需要幾分鐘)"
              )}
            </li>
            <li>遇到登入、CAPTCHA 或 MFA 會停下;這些要你在電腦前完成。</li>
          </ul>
          {err && <p className="badge-bad" style={{ padding: 6 }}>{err}</p>}
        </Modal>
      )}
    </>
  );
}

const STATE_TONE: Record<string, string> = {
  filled_awaiting_confirm: "badge-info",
  submitting: "badge-info",
  submitted: "badge-ok",
  blocked_login: "badge-warn",
  blocked_captcha: "badge-warn",
  blocked_mfa: "badge-warn",
  submit_uncertain: "badge-warn",
  failed: "badge-bad",
  submit_failed: "badge-bad",
};
const STATE_LABEL: Record<string, string> = {
  filled_awaiting_confirm: "已填好 · 等你確認",
  submitting: "送出中…",
  submitted: "已送出",
  blocked_login: "卡住:需要登入",
  blocked_captcha: "卡住:CAPTCHA",
  blocked_mfa: "卡住:MFA",
  submit_uncertain: "已送出?(沒看到確認訊息)",
  failed: "填表失敗",
  submit_failed: "送出失敗",
};
const SOURCE_LABEL: Record<string, string> = { profile: "個人資料", answers: "既有回答", generated: "Claude 撰寫", inferred: "推斷", blank: "空白" };
const RUN_LABEL: Record<string, string> = { queued: "排隊中", running: "執行中" };
const SOURCE_TONE: Record<ApplyField["source"], string> = {
  profile: "badge-neutral",
  answers: "badge-neutral",
  generated: "badge-warn",
  inferred: "badge-warn",
  blank: "badge-bad",
};

export function ApplyStatusPanel({ jd, info, error, onChanged }: { jd: string; info: Info | null; error: string | null; onChanged: () => void }) {
  const [modal, setModal] = useState<null | "summary" | { shot: string } | "confirm1" | "confirm2">(null);
  const [liveOpen, setLiveOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  /** label → edited value (only rows the candidate changed). */
  const [edits, setEdits] = useState<Record<string, string>>({});
  if (error) return <div className="faint">Apply status: {error}</div>;
  if (!info) return null;
  const s = info.status;
  const live = info.runStatus === "running" || info.runStatus === "queued";
  const liveButton = (
    <button className="btn-sm" onClick={() => setLiveOpen(true)} title="查看並操作你電腦上的投遞瀏覽器(手機也能用)">
      即時瀏覽器
    </button>
  );
  const liveView = liveOpen && <LiveBrowser jd={jd} preferTab={info.status?.targetId ?? null} onClose={() => setLiveOpen(false)} onChanged={onChanged} />;
  if (!s && !live && !info.screenshots.length)
    return (
      <div className="apply-panel" style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center" }}>
        <span className="label muted">投遞瀏覽器</span>
        {liveButton}
        {liveView}
      </div>
    );
  const shotUrl = (name: string, mtime: number) => api.applyScreenshotUrl(jd, name, mtime);
  const fields = info.fields ?? [];
  const blankRequired = fields.filter((f) => f.source === "blank" && f.required).length;
  const toConfirm = fields.filter((f) => f.source === "generated" || f.source === "inferred");
  const editable = s?.state === "filled_awaiting_confirm";
  const nEdited = fields.filter((f) => f.label in edits && edits[f.label] !== f.value).length;

  const submit = async () => {
    if (!s) return;
    setBusy(true);
    setErr(null);
    try {
      const changed = fields.filter((f) => f.label in edits && edits[f.label] !== f.value).map((f) => ({ label: f.label, value: edits[f.label] }));
      const r = await api.submitApply(jd, s.url, changed);
      if (r.error) setErr(r.error);
      setModal(null);
    } catch (e) {
      setErr(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  return (
    <div className="apply-panel" style={{ marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span className="label muted">投遞狀態</span>
        {live && <Badge tone="badge-info">填表執行 {RUN_LABEL[info.runStatus ?? ""] ?? info.runStatus}</Badge>}
        {s && !live && <Badge tone={STATE_TONE[s.state] ?? "badge-neutral"}>{STATE_LABEL[s.state] ?? s.state}</Badge>}
        {s && !live && (
          <span className="muted" style={{ fontSize: 12 }}>
            {s.formType ? `${s.formType} · ` : ""}
            {s.pendingCount != null ? `${s.pendingCount} 個空白` : ""}
            {blankRequired ? `(${blankRequired} 個必填)` : ""}
            {s.finishedAt ? ` · ${new Date(s.submittedAt ?? s.finishedAt).toLocaleString()}` : ""}
          </span>
        )}
        <span style={{ flex: 1 }} />
        {liveButton}
        {info.summary && (
          <button className="btn-sm" onClick={() => setModal("summary")}>
            摘要
          </button>
        )}
        {s && (s.state === "filled_awaiting_confirm" || s.state === "submitting") && (
          <button
            className="btn-primary btn-sm"
            disabled={!info.canSubmit || busy || live}
            onClick={() => setModal("confirm1")}
            title="在你電腦上的投遞瀏覽器送出已填好的表單"
          >
            {info.submitting || busy ? "送出中…" : "確認並送出"}
          </button>
        )}
      </div>

      {(s?.blocker || info.help || s?.submitError || s?.notes) && (
        <div style={{ marginTop: 6, fontSize: 13 }}>
          {s?.blocker && <div className="badge-warn" style={{ padding: "4px 6px" }}>卡住原因:{s.blocker}</div>}
          {s?.submitError && <div className="badge-bad" style={{ padding: "4px 6px", marginTop: 4 }}>{s.submitError}</div>}
          {info.help && <div className="muted" style={{ marginTop: 4 }}>在電腦前:{info.help}</div>}
          {s?.notes && <div className="faint" style={{ marginTop: 4 }}>備註:{s.notes}</div>}
          {s?.confirmationText && <div className="muted" style={{ marginTop: 4 }}>看到的確認訊息:「{s.confirmationText}」</div>}
        </div>
      )}
      {err && <div className="badge-bad" style={{ padding: "4px 6px", marginTop: 4 }}>{err}</div>}
      {(info.coverLetter || s?.coverLetter) && (
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Cover letter:{" "}
          {info.coverLetter?.source === "generated" || s?.coverLetter?.source === "generated" ? <b>這次新產生</b> : info.coverLetter ? "既有" : "無"}
          {info.coverLetter && info.coverLetter.scored === false ? <Badge tone="badge-warn">未評分</Badge> : null}
          {s?.coverLetter?.verdict ? ` · Codex ${s.coverLetter.verdict}` : ""}
          {s?.coverLetter ? ` · ${s.coverLetter.uploaded ? "已上傳" : s.coverLetter.pasted ? "已貼上" : "表單沒用到"}` : ""}
          {s?.coverLetter?.error ? ` · ${s.coverLetter.error}` : ""}
          {info.coverLetter?.available && (
            <>
              {" · "}
              <a href={api.applyCoverLetterUrl(jd)} target="_blank" rel="noreferrer">
                看 PDF ↗
              </a>
            </>
          )}
        </div>
      )}
      {info.sheet && (
        <div className="faint" style={{ fontSize: 12, marginTop: 4 }} title={info.sheet.output}>
          Google Sheet:{info.sheet.ok ? (info.sheet.updated ? "該列已設為 履歷送出" : "已新增一列,狀態 履歷送出") : `未同步(${info.sheet.output})`}
        </div>
      )}

      {info.screenshots.length > 0 && (
        <div style={{ display: "flex", gap: 6, overflowX: "auto", marginTop: 8, paddingBottom: 4 }}>
          {info.screenshots.map((sh) => (
            <button key={sh.name} className="btn-ghost" style={{ padding: 0, flex: "0 0 auto", textAlign: "center" }} onClick={() => setModal({ shot: sh.name })}>
              <img
                src={shotUrl(sh.name, sh.mtime)}
                alt={sh.name}
                style={{ height: 72, width: 96, objectFit: "cover", objectPosition: "top", borderRadius: 4, border: "1px solid var(--border)", display: "block" }}
              />
              <span className="faint" style={{ fontSize: 11 }}>{sh.name.replace(".png", "")}</span>
            </button>
          ))}
        </div>
      )}

      {fields.length > 0 && (
        <div style={{ overflowX: "auto", marginTop: 6 }}>
          <table className="apply-fields" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr className="muted" style={{ textAlign: "left" }}>
                <th style={{ padding: "2px 6px" }}>欄位</th>
                <th style={{ padding: "2px 6px" }}>值</th>
                <th style={{ padding: "2px 6px" }}>來源</th>
              </tr>
            </thead>
            <tbody>
              {fields.map((f, i) => (
                <tr
                  key={i}
                  style={{
                    background: f.source === "blank" ? "var(--bad-soft)" : f.source === "inferred" || f.source === "generated" ? "var(--warn-soft)" : undefined,
                    borderTop: "1px solid var(--border)",
                  }}
                >
                  <td style={{ padding: "3px 6px", verticalAlign: "top" }}>
                    {f.label}
                    {f.required ? <b title="必填"> *</b> : null}
                  </td>
                  <td style={{ padding: "3px 6px", verticalAlign: "top", overflowWrap: "anywhere" }}>
                    {editable && (f.source === "generated" || f.source === "inferred") ? (
                      <>
                        <div className="faint" style={{ fontWeight: 600 }}>請確認{f.edited ? "(已修改)" : ""}</div>
                        <textarea
                          value={edits[f.label] ?? f.value}
                          onChange={(e) => setEdits((m) => ({ ...m, [f.label]: e.target.value }))}
                          rows={f.value.length > 80 ? 5 : 1}
                          style={{ width: "100%", fontSize: 12 }}
                          aria-label={`修改 ${f.label}`}
                        />
                        {f.source === "generated" && (
                          <div className="faint">{(edits[f.label] ?? f.value).trim().split(/\s+/).filter(Boolean).length} 字(英文最多 120 字)</div>
                        )}
                      </>
                    ) : (
                      f.value || <span className="faint">—</span>
                    )}
                    {f.note ? <div className="faint">{f.note}</div> : null}
                  </td>
                  <td style={{ padding: "3px 6px", verticalAlign: "top" }}>
                    <Badge tone={SOURCE_TONE[f.source]}>{SOURCE_LABEL[f.source] ?? f.source}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {liveView}
      {modal === "summary" && info.summary && (
        <Modal title="投遞摘要" onClose={() => setModal(null)} closeLabel="關閉">
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{info.summary}</pre>
        </Modal>
      )}
      {modal && typeof modal === "object" && (
        <Modal
          title={modal.shot.replace(".png", "")}
          onClose={() => setModal(null)}
          closeLabel="關閉"
          actions={
            <a className="btn btn-sm" href={shotUrl(modal.shot, Date.now())} target="_blank" rel="noreferrer">
              原尺寸 ↗
            </a>
          }
        >
          <img src={shotUrl(modal.shot, info.screenshots.find((x) => x.name === modal.shot)?.mtime ?? 0)} alt={modal.shot} style={{ width: "100%" }} />
        </Modal>
      )}
      {modal === "confirm1" && s && (
        <Modal
          title="要送出這份申請嗎?"
          onClose={() => setModal(null)}
          closeLabel="關閉"
          actions={
            <button className="btn-primary" onClick={() => setModal("confirm2")}>
              繼續…
            </button>
          }
        >
          <p>
            這會在你電腦上的投遞瀏覽器,於 <span className="mono" style={{ overflowWrap: "anywhere" }}>{s.url}</span> 按下<b>「{s.submitControl?.text ?? "Submit"}」</b>。送出後無法撤回。
          </p>
          {blankRequired > 0 && (
            <p className="badge-bad" style={{ padding: 6 }}>
              還有 {blankRequired} 個必填欄位是空白,網站很可能會拒收。請先在電腦前填好。
            </p>
          )}
          {toConfirm.length > 0 && (
            <p className="badge-warn" style={{ padding: 6 }}>
              有 {toConfirm.length} 個值是 Claude 產生或推斷的,請檢查黃色的列。
              {nEdited > 0 ? `你修改的 ${nEdited} 個值會在送出前寫進表單。` : ""}
            </p>
          )}
          <p className="muted">繼續之前,請檢查確認頁截圖和上面每個欄位。</p>
        </Modal>
      )}
      {modal === "confirm2" && s && (
        <Modal
          title="最後確認"
          onClose={() => setModal(null)}
          closeLabel="關閉"
          actions={
            <button className="btn-primary" onClick={submit} disabled={busy}>
              {busy ? "送出中…" : `是,送出到 ${s.formType ?? "公司"}`}
            </button>
          }
        >
          <p>
            <b>最後機會。</b>按下後申請會寄給公司,Google Sheet 該列的狀態會設為 履歷送出(沒有該列就新增)。
          </p>
        </Modal>
      )}
    </div>
  );
}
