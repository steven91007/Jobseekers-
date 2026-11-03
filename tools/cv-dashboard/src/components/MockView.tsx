// 模擬面試: practise the prepared interview questions by typing or speaking (Web Speech API), with follow-ups and
// rubric scores from the server (interview-prep mock.py). Reference answers appear only after a question is scored.
import { useCallback, useEffect, useRef, useState } from "react";
import type { MockApp, MockFramework, MockItemView, MockSessionView, MockStage } from "../../shared/types";
import { api, ApiError } from "../api";
import { Badge } from "./ui";

const STAGES: { v: MockStage; label: string }[] = [
  { v: "mixed", label: "綜合(hiring manager + 工程師)" },
  { v: "tech", label: "技術面" },
  { v: "hm", label: "Hiring manager" },
  { v: "hr", label: "HR / 動機" },
];
const KIND_LABEL: Record<string, string> = {
  intro: "開場", behavioural: "行為", project: "專案", technical: "技術", "system-design": "系統設計", gap: "缺口", motivation: "動機",
};
const RUBRIC_LABEL: Record<string, string> = {
  structure: "結構", evidence: "證據", relevance: "相關性", honesty: "誠實度", clarity: "清晰度", followups: "追問",
};

const errText = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

// ---------------------------------------------------------------- speech
type SR = any;
function getSR(): (new () => SR) | null {
  const w = window as any;
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

function useSpeech(lang: string, onFinal: (t: string) => void) {
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);
  const recRef = useRef<SR | null>(null);
  const wantRef = useRef(false);
  const finalRef = useRef(onFinal);
  finalRef.current = onFinal;
  const supported = !!getSR();

  const stop = useCallback(() => {
    wantRef.current = false;
    setListening(false);
    setInterim("");
    try {
      recRef.current?.stop();
    } catch {
      /* already stopped */
    }
  }, []);

  const start = useCallback(() => {
    const Ctor = getSR();
    if (!Ctor) return;
    setError(null);
    const rec = new Ctor();
    rec.lang = lang;
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (e: any) => {
      let tmp = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalRef.current(r[0].transcript.trim());
        else tmp += r[0].transcript;
      }
      setInterim(tmp);
    };
    rec.onerror = (e: any) => {
      if (e.error === "no-speech" || e.error === "aborted") return;
      setError(e.error === "not-allowed" ? "瀏覽器沒有麥克風權限(網址列左邊的鎖頭 → 允許麥克風)" : `語音辨識錯誤:${e.error}`);
      wantRef.current = false;
      setListening(false);
    };
    // Chrome ends a session after a pause: restart while the candidate still wants to talk
    rec.onend = () => {
      if (wantRef.current) {
        try {
          rec.start();
        } catch {
          setListening(false);
        }
      } else setListening(false);
    };
    recRef.current = rec;
    wantRef.current = true;
    try {
      rec.start();
      setListening(true);
    } catch (e) {
      setError(errText(e));
    }
  }, [lang]);

  useEffect(() => () => stop(), [stop]);
  return { supported, listening, interim, error, start, stop };
}

function speak(text: string, on: boolean) {
  if (!on || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-US";
  u.rate = 0.97;
  const v = window.speechSynthesis.getVoices().find((x) => x.lang.startsWith("en") && /Natural|Online|Google/.test(x.name));
  if (v) u.voice = v;
  window.speechSynthesis.speak(u);
}

// ---------------------------------------------------------------- page
export function MockView({ jd, id }: { jd: string | null; id: string | null }) {
  if (jd && id) return <MockSession jd={jd} id={id} />;
  return <MockHome />;
}

function MockHome() {
  const [apps, setApps] = useState<MockApp[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [stage, setStage] = useState<MockStage>("mixed");
  const [n, setN] = useState(5);
  const [fu, setFu] = useState(2);

  useEffect(() => {
    api.mockApps().then(setApps, (e) => setErr(errText(e)));
  }, []);

  const start = async (jd: string) => {
    setBusy(jd);
    setErr(null);
    try {
      const s = await api.mockStart(jd, stage, n, fu);
      window.location.hash = `#/mock/${encodeURIComponent(jd)}/${encodeURIComponent(s.id)}`;
    } catch (e) {
      setErr(errText(e));
      setBusy(null);
    }
  };

  return (
    <div className="mock-page">
      <h1 className="mock-title">模擬面試</h1>
      <p className="muted">
        從每份申請的面試準備(10_interview.json)出題;公開面經題(reported)優先,再來是沒練過、分數低的題目。面試官用英文問,你可以打字或用麥克風回答,
        每題最多追問 {fu} 次,之後依六項 rubric 評分並給修改建議與改進版答案(只用你資料庫裡的事實)。
      </p>
      <div className="panel mock-settings">
        <div className="panel-body mock-row">
          <label>
            關卡{" "}
            <select value={stage} onChange={(e) => setStage(e.target.value as MockStage)}>
              {STAGES.map((s) => (
                <option key={s.v} value={s.v}>{s.label}</option>
              ))}
            </select>
          </label>
          <label>
            題數 <input type="number" min={1} max={10} value={n} onChange={(e) => setN(Number(e.target.value))} style={{ width: 56 }} />
          </label>
          <label>
            每題追問 <input type="number" min={0} max={3} value={fu} onChange={(e) => setFu(Number(e.target.value))} style={{ width: 56 }} />
          </label>
        </div>
      </div>
      {err && <div className="mock-error">{err}</div>}
      {!apps ? (
        <p className="muted">載入中…</p>
      ) : apps.length === 0 ? (
        <p className="muted">還沒有任何申請做過面試準備。先對一份申請跑 /interview-prep。</p>
      ) : (
        <div className="mock-apps">
          {apps.map((a) => (
            <div key={a.jd} className="panel">
              <div className="panel-head">
                <div>
                  <strong>{a.company}</strong> <span className="muted">· {a.title}</span>
                  <div className="muted small">{a.questions} 題準備好的題目</div>
                </div>
                <button className="btn-primary" disabled={!!busy} onClick={() => start(a.jd)}>
                  {busy === a.jd ? "準備中…" : "開始模擬面試"}
                </button>
              </div>
              {a.sessions.length > 0 && (
                <div className="panel-body">
                  <div className="muted small">過去的練習</div>
                  <ul className="mock-sessions">
                    {a.sessions.slice(0, 6).map((s) => (
                      <li key={s.id}>
                        <a href={`#/mock/${encodeURIComponent(a.jd)}/${encodeURIComponent(s.id)}`}>{s.started.replace("T", " ").slice(0, 16)}</a>{" "}
                        <span className="muted">
                          · {STAGES.find((x) => x.v === s.stage)?.label ?? s.stage} · {s.scored}/{s.total} 題
                          {s.average !== null && ` · 平均 ${s.average.toFixed(2)}`}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function scoreTone(v: number | null): string {
  if (v === null) return "badge-neutral";
  return v >= 4 ? "badge-ok" : v >= 3 ? "badge-warn" : "badge-bad";
}

function MockSession({ jd, id }: { jd: string; id: string }) {
  const [s, setS] = useState<MockSessionView | null>(null);
  const [cur, setCur] = useState<number>(1);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<null | "thinking" | "scoring" | "other">(null);
  const [err, setErr] = useState<string | null>(null);
  const [lang, setLang] = useState("en-US");
  const [tts, setTts] = useState<boolean>(() => {
    try {
      return localStorage.getItem("mock-tts") === "1";
    } catch {
      return false;
    }
  });
  const spokenRef = useRef<string>("");
  const speech = useSpeech(lang, (t) => setText((x) => (x ? `${x} ${t}` : t)));

  useEffect(() => {
    api.mockSession(jd, id).then((v) => {
      setS(v);
      const open = v.items.find((it) => it.status !== "scored" && it.status !== "skipped");
      setCur(open?.n ?? v.items[v.items.length - 1].n);
    }, (e) => setErr(errText(e)));
  }, [jd, id]);

  useEffect(() => {
    try {
      localStorage.setItem("mock-tts", tts ? "1" : "0");
    } catch {
      /* storage unavailable */
    }
  }, [tts]);

  const item: MockItemView | undefined = s?.items.find((it) => it.n === cur);
  const lastInterviewer = item ? [...item.chat].reverse().find((m) => m.role === "interviewer")?.text ?? "" : "";

  // read the interviewer's newest line aloud once
  useEffect(() => {
    if (!item || item.status === "scored" || item.status === "skipped") return;
    const key = `${cur}:${lastInterviewer}`;
    if (lastInterviewer && spokenRef.current !== key) {
      spokenRef.current = key;
      speak(lastInterviewer, tts);
    }
  }, [cur, lastInterviewer, item, tts]);

  const act = async (kind: "thinking" | "scoring" | "other", f: () => Promise<MockSessionView>) => {
    setBusy(kind);
    setErr(null);
    try {
      setS(await f());
      return true;
    } catch (e) {
      setErr(errText(e));
      return false;
    } finally {
      setBusy(null);
    }
  };

  const submit = async (finish = false) => {
    if (!item) return;
    speech.stop();
    const t = text.trim();
    if (t) {
      const willScore = finish || item.followupsAsked >= (s?.maxFollowups ?? 0);
      const ok = await act(willScore ? "scoring" : "thinking", () => api.mockAnswer(jd, id, item.n, t, finish));
      if (ok) setText("");
    } else if (finish) {
      await act("scoring", () => api.mockFinish(jd, id, item.n));
    }
  };

  if (err && !s) return <div className="mock-page"><div className="mock-error">{err}</div></div>;
  if (!s || !item) return <div className="mock-page"><p className="muted">載入中…</p></div>;

  const done = s.items.every((it) => it.status === "scored" || it.status === "skipped");
  const next = s.items.find((it) => it.n > cur && it.status !== "scored" && it.status !== "skipped")
    ?? s.items.find((it) => it.status !== "scored" && it.status !== "skipped");
  const scoredItems = s.items.filter((x) => x.overall !== null);
  const avg = scoredItems.length ? scoredItems.reduce((a, x) => a + (x.overall ?? 0), 0) / scoredItems.length : null;
  const answering = item.status === "open" || item.status === "answering";

  return (
    <div className="mock-page">
      <div className="mock-head">
        <div>
          <a href="#/mock" className="muted small">← 模擬面試</a>
          <h1 className="mock-title">{s.company} · {s.title}</h1>
          <div className="muted small">
            {STAGES.find((x) => x.v === s.stage)?.label} · 開始於 {s.started.replace("T", " ").slice(0, 16)}
            {avg !== null && ` · 目前平均 ${avg.toFixed(2)}`}
          </div>
        </div>
        <div className="mock-row">
          <label className="small">
            <input type="checkbox" checked={tts} onChange={(e) => setTts(e.target.checked)} /> 朗讀問題
          </label>
          <label className="small">
            語音{" "}
            <select value={lang} onChange={(e) => { speech.stop(); setLang(e.target.value); }}>
              <option value="en-US">English (US)</option>
              <option value="en-GB">English (UK)</option>
              <option value="zh-TW">中文(台灣)</option>
            </select>
          </label>
        </div>
      </div>

      <div className="mock-layout">
        <nav className="mock-list" aria-label="Questions">
          {s.items.map((it) => (
            <button key={it.n} className={`mock-q ${it.n === cur ? "active" : ""}`} onClick={() => { speech.stop(); setCur(it.n); setText(""); }}>
              <span className="mock-q-n">Q{it.n}</span>
              <span className="mock-q-kind">{KIND_LABEL[it.kind] ?? it.kind}{it.reported && " · 面經"}</span>
              <span className="mock-q-st">
                {it.status === "scored" ? <Badge tone={scoreTone(it.overall)}>{it.overall?.toFixed(1)}</Badge>
                  : it.status === "skipped" ? <Badge>跳過</Badge>
                  : it.status === "answering" ? <Badge tone="badge-info">作答中</Badge> : null}
              </span>
            </button>
          ))}
          <button className="btn-sm" disabled={!!busy || scoredItems.length === 0} onClick={() => act("other", () => api.mockReport(jd, id))}>
            產生報告
          </button>
        </nav>

        <section className="mock-main">
          <div className="mock-chat panel">
            <div className="panel-body">
              {item.chat.map((m, i) => (
                <div key={i} className={`mock-msg ${m.role}`}>
                  <div className="mock-who">{m.role === "interviewer" ? "Interviewer" : "You"}</div>
                  <div className="mock-bubble">{m.text}</div>
                  {m.role === "interviewer" && (
                    <button className="btn-ghost btn-sm" title="朗讀" onClick={() => speak(m.text, true)}>🔊</button>
                  )}
                </div>
              ))}
              {busy === "thinking" && <div className="mock-msg interviewer"><div className="mock-bubble muted">面試官思考中…</div></div>}
              {busy === "scoring" && <div className="mock-msg interviewer"><div className="mock-bubble muted">評分中(檢查數字與缺口、寫改進版答案,約 20–60 秒)…</div></div>}
            </div>
          </div>

          {err && <div className="mock-error">{err}</div>}

          {answering && item.frameworks.length > 0 && <FrameworkHint frameworks={item.frameworks} />}

          {answering && (
            <div className="panel mock-answer">
              <div className="panel-body">
                <textarea
                  value={text + (speech.interim ? (text ? " " : "") + speech.interim : "")}
                  onChange={(e) => setText(e.target.value)}
                  placeholder={speech.supported ? "按 🎤 開始說,或直接打字(建議用英文回答,60–150 字 / 30–90 秒)" : "直接打字回答(這個瀏覽器不支援語音輸入,請用 Chrome 或 Edge)"}
                  rows={7}
                  disabled={!!busy}
                />
                <div className="mock-row mock-actions">
                  {speech.supported && (
                    <button className={speech.listening ? "btn-danger" : ""} disabled={!!busy} onClick={() => (speech.listening ? speech.stop() : speech.start())}>
                      {speech.listening ? "⏹ 停止錄音" : "🎤 語音回答"}
                    </button>
                  )}
                  {speech.listening && <span className="mock-rec"><span className="dot pulse" aria-hidden /> 聆聽中</span>}
                  <span className="muted small">{text.trim() ? `${text.trim().split(/\s+/).length} 字` : ""}</span>
                  <span className="spacer" />
                  <button disabled={!!busy} onClick={() => act("other", () => api.mockSkip(jd, id, item.n))}>跳過</button>
                  <button disabled={!!busy || (!text.trim() && item.status !== "answering")} onClick={() => submit(true)}>結束這題並評分</button>
                  <button className="btn-primary" disabled={!!busy || !text.trim()} onClick={() => submit(false)}>送出回答</button>
                </div>
                {speech.error && <div className="mock-error small">{speech.error}</div>}
                <div className="muted small">
                  追問 {item.followupsAsked}/{s.maxFollowups}。送出後面試官可能追問;追問用完或按「結束這題並評分」就會評分。
                </div>
              </div>
            </div>
          )}

          {item.status === "scored" && <ScoreCard item={item} rubric={s.rubric} />}
          {item.status === "skipped" && item.reference && (
            <details className="panel mock-ref"><summary className="panel-body">參考答案(已跳過)</summary><div className="panel-body">{item.reference}</div></details>
          )}

          {!answering && (
            <div className="mock-row mock-actions">
              {item.status === "scored" && <button disabled={!!busy} onClick={() => act("other", () => api.mockRetry(jd, id, item.n))}>重答這題</button>}
              <span className="spacer" />
              {next ? (
                <button className="btn-primary" onClick={() => { setCur(next.n); setText(""); }}>下一題 →</button>
              ) : done && !s.reportMd ? (
                <button className="btn-primary" disabled={!!busy} onClick={() => act("other", () => api.mockReport(jd, id))}>完成,產生報告</button>
              ) : null}
            </div>
          )}

          {s.reportMd && (
            <details className="panel mock-report" open={done}>
              <summary className="panel-body"><strong>練習報告</strong></summary>
              <pre className="panel-body mock-pre">{s.reportMd}</pre>
            </details>
          )}
        </section>
      </div>
    </div>
  );
}

function FrameworkHint({ frameworks }: { frameworks: MockFramework[] }) {
  const [open, setOpen] = useState(false);
  const f = frameworks[0];
  return (
    <div className="panel mock-hint">
      <div className="panel-body">
        <button className="btn-sm" onClick={() => setOpen((x) => !x)}>
          💡 {open ? "收起建議架構" : `建議架構:${f.name}`}
        </button>
        {open && (
          <>
            <ol className="mock-hint-list">
              {f.chunks.map((c, i) => (
                <li key={i}><strong>{c.label}</strong> — {c.goal}</li>
              ))}
            </ol>
            {frameworks.length > 1 && (
              <div className="muted small">也可以用:{frameworks.slice(1).map((x) => `${x.name}(${x.chunks.map((c) => c.label).join(" → ")})`).join("、")}</div>
            )}
            <div className="muted small">先在腦中排好這幾塊再開口,每塊 1–2 句。評分後會把你的回答對到每一塊,告訴你哪塊缺了、怎麼補。</div>
          </>
        )}
      </div>
    </div>
  );
}

function ScoreCard({ item, rubric }: { item: MockItemView; rubric: Record<string, string> }) {
  const a = item.auto;
  const flags = a ? [
    ...a.unverified_numbers.map((x) => `資料庫找不到的數字:${x}`),
    ...a.gap_claims.map((x) => `像在宣稱缺口技能:${x}`),
    ...a.traps.map((x) => `陷阱:${x}`),
    ...a.flags,
  ] : [];
  return (
    <div className="panel mock-score">
      <div className="panel-head">
        <strong>評分 <Badge tone={scoreTone(item.overall)}>{item.overall?.toFixed(2)}</Badge></strong>
        {a && <span className="muted small">{a.words} 字 · 約 {a.seconds} 秒 · 命中概念 {a.concepts_hit.length}/{a.concepts_hit.length + a.concepts_missed.length}</span>}
      </div>
      <div className="panel-body">
        <div className="mock-scores">
          {Object.entries(item.scores ?? {}).map(([k, v]) => (
            <div key={k} className="mock-score-cell" title={rubric[k]}>
              <div className="muted small">{RUBRIC_LABEL[k] ?? k}</div>
              <Badge tone={scoreTone(v)}>{v}</Badge>
            </div>
          ))}
        </div>
        {flags.length > 0 && (
          <ul className="mock-flags">
            {flags.map((f, i) => <li key={i}>{f}</li>)}
          </ul>
        )}
        {a && a.library_only_numbers.length > 0 && (
          <div className="muted small">資料庫有、但不在這份 CV 引用證據裡的數字:{a.library_only_numbers.join(", ")}(被問到要能說明)</div>
        )}
        <h3 className="mock-h">修改建議</h3>
        <div className="mock-pre">{item.feedback}</div>
        {item.chunks && item.chunks.length > 0 && (
          <>
            <h3 className="mock-h">
              架構拆解:{item.frameworks.find((f) => f.key === item.framework)?.name ?? item.framework}
            </h3>
            <div className="mock-chunks">
              {item.chunks.map((c, i) => {
                const goal = item.frameworks.find((f) => f.key === item.framework)?.chunks[i]?.goal;
                return (
                  <div key={i} className="mock-chunk">
                    <div className="mock-chunk-label">
                      <span className="mock-chunk-n">{i + 1}</span> {c.label}
                      {goal && <span className="muted small"> · {goal}</span>}
                    </div>
                    <div className="mock-chunk-grid">
                      <div><div className="muted small">你說的</div>{c.yours}</div>
                      <div><div className="muted small">怎麼改</div>{c.tip}</div>
                    </div>
                    <div className="mock-better">{c.better}</div>
                  </div>
                );
              })}
            </div>
          </>
        )}
        <h3 className="mock-h">改進版答案(完整)</h3>
        <div className="mock-better">
          {item.better}
          <button className="btn-ghost btn-sm" title="朗讀" onClick={() => speak(item.better, true)}>🔊</button>
        </div>
        {item.scoreWarnings.length > 0 && <div className="muted small">檢查提醒:{item.scoreWarnings.join(" · ")}</div>}
        {item.reference && (
          <details className="mock-ref">
            <summary>面試準備的參考答案</summary>
            <div>{item.reference}</div>
          </details>
        )}
      </div>
    </div>
  );
}
