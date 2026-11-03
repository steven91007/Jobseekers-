// 表單問答: paste an application form's questions, a Claude run (form-answers skill) writes a
// paste-ready answer for each, copy them into your own apply tool. Never fills or submits a form.
// Replaces the Auto apply button (2026-11-03).
import { useCallback, useEffect, useState } from "react";
import type { FormAnswer, FormAnswersInfo } from "../../shared/types";
import { api } from "../api";
import { Badge, Modal } from "./ui";

const KIND: Record<FormAnswer["kind"], { label: string; tone: string }> = {
  needs_you: { label: "需要你決定", tone: "badge-bad" },
  generated: { label: "Claude 撰寫 · 請確認", tone: "badge-warn" },
  standing: { label: "固定回答", tone: "badge-neutral" },
};
const ORDER: FormAnswer["kind"][] = ["needs_you", "generated", "standing"];

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // http over the LAN has no clipboard API: fall back to a hidden textarea
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

function useFormAnswers(jd: string) {
  const [info, setInfo] = useState<FormAnswersInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api
      .formAnswers(jd)
      .then((d) => {
        setInfo(d);
        setError(null);
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, [jd]);
  useEffect(load, [load]);
  const live = info?.runStatus === "running" || info?.runStatus === "queued";
  useEffect(() => {
    if (!live) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [live, load]);
  return { info, error, reload: load, live };
}

function AnswerCard({ a }: { a: FormAnswer }) {
  const [copied, setCopied] = useState(false);
  const k = KIND[a.kind] ?? KIND.generated;
  return (
    <div style={{ borderTop: "1px solid var(--border)", padding: "8px 0" }}>
      <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
        <b style={{ flex: 1, overflowWrap: "anywhere" }}>{a.question}</b>
        <Badge tone={k.tone}>{k.label}</Badge>
      </div>
      {a.answer ? (
        <div style={{ display: "flex", gap: 6, alignItems: "flex-start", marginTop: 4 }}>
          <div style={{ flex: 1, whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 13 }}>{a.answer}</div>
          <button
            className="btn-sm"
            onClick={async () => {
              setCopied(await copyText(a.answer));
              setTimeout(() => setCopied(false), 1500);
            }}
          >
            {copied ? "已複製" : "複製"}
          </button>
        </div>
      ) : (
        <div className="faint" style={{ marginTop: 4 }}>(留給你填)</div>
      )}
      {(a.note || a.words) && (
        <div className="faint" style={{ fontSize: 12, marginTop: 2 }}>
          {a.kind === "generated" && a.words ? `${a.words} 字` : ""}
          {a.kind === "generated" && a.words && a.note ? " · " : ""}
          {a.note}
        </div>
      )}
    </div>
  );
}

export function FormAnswersButton({ jd, company, onStarted }: { jd: string; company: string; onStarted?: (runId: string) => void }) {
  const { info, error, reload, live } = useFormAnswers(jd);
  const [open, setOpen] = useState(false);
  const [questions, setQuestions] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const answers = [...(info?.answers ?? [])].sort((x, y) => ORDER.indexOf(x.kind) - ORDER.indexOf(y.kind));
  const needYou = answers.filter((a) => a.kind === "needs_you").length;

  const start = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.startFormAnswers(jd, questions);
      reload();
      onStarted?.(r.runId);
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
          setQuestions(info?.questions?.trim() ?? "");
          setErr(null);
          setOpen(true);
        }}
        title={error ?? "貼上投遞表單的問題,Claude 產生可直接貼上的回答(不會填表或送出)"}
      >
        {live ? "回答中…" : "表單問答"}
        {!live && answers.length > 0 ? ` (${answers.length})` : ""}
      </button>
      {open && (
        <Modal
          title={`表單問答 · ${company}`}
          onClose={() => setOpen(false)}
          closeLabel="關閉"
          actions={
            <button className="btn-primary" onClick={start} disabled={busy || live || !questions.trim()}>
              {live ? "回答中…" : busy ? "啟動中…" : answers.length ? "重新產生回答" : "產生回答"}
            </button>
          }
        >
          <label className="muted" style={{ display: "block" }}>
            把申請表上的問題貼在這裡(一行一題,或直接整段貼上)
          </label>
          <textarea
            value={questions}
            onChange={(e) => setQuestions(e.target.value)}
            rows={6}
            style={{ width: "100%", marginTop: 4, fontSize: 13 }}
            placeholder={"Why do you want to work at …?\nWhat is your notice period?\nDescribe an LLM system you took to production."}
          />
          <p className="faint" style={{ fontSize: 12, marginTop: 4 }}>
            只用你的履歷、經驗庫和已確認的資料回答。薪資數字、離職通知期、到職日這類只有你能決定的,會標成「需要你決定」。
          </p>
          {err && <p className="badge-bad" style={{ padding: 6 }}>{err}</p>}
          {live && <p className="badge-info" style={{ padding: 6 }}>{info?.runStatus === "queued" ? "排隊中…" : "Claude 正在寫回答,通常 1–3 分鐘…"}</p>}
          {answers.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div className="muted" style={{ fontSize: 12 }}>
                {answers.length} 題{needYou ? ` · ${needYou} 題需要你決定` : ""}
                {info?.generatedAt ? ` · ${new Date(info.generatedAt).toLocaleString()}` : ""}
              </div>
              {answers.map((a, i) => (
                <AnswerCard key={i} a={a} />
              ))}
              {info?.skipped?.length ? <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>略過(聯絡資料/上傳):{info.skipped.join("、")}</div> : null}
            </div>
          )}
        </Modal>
      )}
    </>
  );
}
