// Live browser: full-screen view of a tab of the apply browser on the PC (CDP screencast over SSE).
// Tap = click at the mapped page point, wheel / two-finger drag / ↑↓ = scroll, a text box types into
// the focused field, and a page dialog (the submit guard's confirm() included) opens as a sheet.
// Answering the guard with Accept here submits the application: it is the candidate's own OK.
import { useCallback, useEffect, useRef, useState } from "react";
import { frameToPage, type LiveDialog, type LiveEvent, type LiveFrameMeta, type LiveInput, type LiveTab } from "../../shared/live";
import { api } from "../api";
import { Badge } from "./ui";

type Conn = "connecting" | "live" | "reconnecting" | "closed";
const CONN_LABEL: Record<Conn, string> = { connecting: "連線中", live: "即時", reconnecting: "重新連線中", closed: "已中斷" };

export function LiveBrowser({ jd, preferTab, onClose, onChanged }: { jd: string; preferTab?: string | null; onClose: () => void; onChanged: () => void }) {
  const [tabs, setTabs] = useState<LiveTab[] | null>(null);
  const [tab, setTab] = useState<string | null>(null);
  const [tabsErr, setTabsErr] = useState<string | null>(null);
  const [conn, setConn] = useState<Conn>("connecting");
  const [page, setPage] = useState<{ url: string; title: string } | null>(null);
  const [dialog, setDialog] = useState<LiveDialog | null>(null);
  const [promptText, setPromptText] = useState("");
  const [fps, setFps] = useState(0);
  const [lag, setLag] = useState<number | null>(null);
  const [hasFrame, setHasFrame] = useState(false);
  const [text, setText] = useState("");
  const [note, setNote] = useState<{ tone: string; msg: string } | null>(null);
  const [tap, setTap] = useState<{ x: number; y: number; k: number } | null>(null);
  const [retry, setRetry] = useState(0);
  const imgRef = useRef<HTMLImageElement>(null);
  const metaRef = useRef<LiveFrameMeta | null>(null);
  const frameTimes = useRef<number[]>([]);
  const sentAt = useRef<number | null>(null);

  const loadTabs = useCallback(() => {
    api
      .liveTabs()
      .then((t) => {
        setTabs(t);
        setTabsErr(null);
        setTab((cur) => (cur && t.some((x) => x.id === cur) ? cur : (t.find((x) => x.id === preferTab) ?? t.find((x) => x.jd === jd) ?? t[0])?.id ?? null));
      })
      .catch((e) => setTabsErr(String(e.message ?? e)));
  }, [jd, preferTab]);
  useEffect(loadTabs, [loadTabs]);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const esc = (e: KeyboardEvent) => e.key === "Escape" && !(e.target instanceof HTMLInputElement) && onClose();
    window.addEventListener("keydown", esc);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", esc);
    };
  }, [onClose]);

  // the stream
  useEffect(() => {
    if (!tab) return;
    setConn("connecting");
    setHasFrame(false);
    setDialog(null);
    metaRef.current = null;
    const es = new EventSource(api.liveStreamUrl(tab));
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data) as LiveEvent;
      if (ev.type === "frame") {
        if (imgRef.current) imgRef.current.src = `data:image/jpeg;base64,${ev.data}`;
        metaRef.current = ev.meta;
        frameTimes.current.push(performance.now());
        if (sentAt.current !== null) {
          setLag(Math.round(performance.now() - sentAt.current));
          sentAt.current = null;
        }
        setHasFrame(true);
        setConn("live");
      } else if (ev.type === "hello") {
        setConn("live");
        setPage({ url: ev.url, title: ev.title });
        setDialog(ev.dialog);
      } else if (ev.type === "page") setPage({ url: ev.url, title: ev.title });
      else if (ev.type === "dialog") {
        setDialog(ev.dialog);
        setPromptText(ev.dialog.defaultPrompt ?? "");
      } else if (ev.type === "dialog-closed") setDialog(null);
      else if (ev.type === "status") {
        setNote(
          ev.state === "submitted"
            ? { tone: "badge-ok", msg: `已送出:頁面顯示「${ev.confirmationText}」。Sheet 該列已設為 履歷送出。` }
            : { tone: "badge-bad", msg: ev.error ?? `狀態:${ev.state}` },
        );
        onChanged();
      } else if (ev.type === "closed") {
        setConn("closed");
        setNote({ tone: "badge-warn", msg: ev.reason });
        es.close();
        loadTabs();
      }
    };
    es.onerror = () => setConn(es.readyState === EventSource.CLOSED ? "closed" : "reconnecting");
    return () => es.close();
  }, [tab, retry, loadTabs, onChanged]);

  useEffect(() => {
    const t = setInterval(() => {
      const now = performance.now();
      frameTimes.current = frameTimes.current.filter((x) => now - x < 2000);
      setFps(frameTimes.current.length / 2);
    }, 500);
    return () => clearInterval(t);
  }, []);

  const send = useCallback(
    async (inp: LiveInput) => {
      if (!tab) return;
      sentAt.current = performance.now();
      try {
        await api.liveInput(tab, inp);
      } catch (e) {
        sentAt.current = null;
        setNote({ tone: "badge-bad", msg: String((e as Error).message ?? e) });
      }
    },
    [tab],
  );

  const toPage = (clientX: number, clientY: number) => {
    const img = imgRef.current;
    const meta = metaRef.current;
    if (!img || !meta) return null;
    const r = img.getBoundingClientRect();
    return { ...frameToPage(clientX - r.left, clientY - r.top, r.width, r.height, meta), dx: clientX - r.left, dy: clientY - r.top };
  };

  const onTap = (e: React.MouseEvent<HTMLImageElement>) => {
    const p = toPage(e.clientX, e.clientY);
    if (!p || dialog) return;
    setTap({ x: p.dx, y: p.dy, k: Date.now() });
    void send({ type: "click", x: p.x, y: p.y });
  };

  // wheel (desktop) and two-finger vertical drag (phone) → page scroll; pinch still zooms the image
  const scrollAcc = useRef({ dy: 0, x: 0, y: 0, timer: 0 as number | 0 });
  const queueScroll = useCallback(
    (deltaY: number, clientX: number, clientY: number) => {
      const a = scrollAcc.current;
      const p = toPage(clientX, clientY);
      a.dy += deltaY;
      if (p) Object.assign(a, { x: p.x, y: p.y });
      if (a.timer) return;
      a.timer = window.setTimeout(() => {
        const dy = Math.max(-5000, Math.min(5000, Math.round(a.dy)));
        a.dy = 0;
        a.timer = 0;
        if (dy) void send({ type: "scroll", deltaY: dy, x: a.x, y: a.y });
      }, 90);
    },
    [send],
  );
  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    const wheel = (e: WheelEvent) => {
      if (e.ctrlKey) return; // trackpad pinch = zoom
      e.preventDefault();
      const scale = metaRef.current ? metaRef.current.deviceWidth / img.getBoundingClientRect().width : 1;
      queueScroll((e.deltaMode === 1 ? e.deltaY * 40 : e.deltaY) * Math.min(scale, 2), e.clientX, e.clientY);
    };
    let start: { mid: number; dist: number; mode: "?" | "scroll" | "pinch" } | null = null;
    const pts = (t: TouchList) => ({ mid: (t[0].clientY + t[1].clientY) / 2, dist: Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY), x: (t[0].clientX + t[1].clientX) / 2 });
    const ts = (e: TouchEvent) => {
      start = e.touches.length === 2 ? { ...pts(e.touches), mode: "?" } : null;
    };
    const tm = (e: TouchEvent) => {
      if (!start || e.touches.length !== 2) return;
      const p = pts(e.touches);
      if (start.mode === "?") {
        if (Math.abs(p.dist - start.dist) / start.dist > 0.12) start.mode = "pinch";
        else if (Math.abs(p.mid - start.mid) > 12) start.mode = "scroll";
      }
      if (start.mode !== "scroll") return;
      e.preventDefault();
      const scale = metaRef.current ? metaRef.current.deviceWidth / img.getBoundingClientRect().width : 1;
      queueScroll((start.mid - p.mid) * scale, p.x, p.mid);
      start.mid = p.mid;
    };
    const te = () => (start = null);
    img.addEventListener("wheel", wheel, { passive: false });
    img.addEventListener("touchstart", ts, { passive: true });
    img.addEventListener("touchmove", tm, { passive: false });
    img.addEventListener("touchend", te);
    return () => {
      img.removeEventListener("wheel", wheel);
      img.removeEventListener("touchstart", ts);
      img.removeEventListener("touchmove", tm);
      img.removeEventListener("touchend", te);
    };
  }, [queueScroll]);

  const page60 = () => Math.round((metaRef.current?.deviceHeight ?? 800) * 0.6);
  const answer = async (accept: boolean) => {
    if (!tab || !dialog) return;
    try {
      await api.liveDialog(tab, accept, dialog.type === "prompt" ? promptText : undefined);
      setDialog(null);
    } catch (e) {
      setNote({ tone: "badge-bad", msg: String((e as Error).message ?? e) });
    }
  };
  const current = tabs?.find((t) => t.id === tab);

  return (
    <div className="live" role="dialog" aria-label="即時瀏覽器">
      <div className="live-bar">
        <button className="btn-sm" onClick={onClose} aria-label="關閉即時瀏覽器">
          ✕
        </button>
        <select value={tab ?? ""} onChange={(e) => setTab(e.target.value)} aria-label="分頁" className="live-tabs">
          {!tabs?.length && <option value="">{tabsErr ? "瀏覽器沒有在執行" : "沒有分頁"}</option>}
          {tabs?.map((t) => (
            <option key={t.id} value={t.id}>
              {t.jd ? `[${t.jd}] ` : ""}
              {t.title || t.url}
            </option>
          ))}
        </select>
        <button className="btn-sm" onClick={loadTabs} title="重新載入分頁清單">
          ↻
        </button>
        <Badge tone={conn === "live" ? "badge-ok" : conn === "closed" ? "badge-bad" : "badge-warn"}>{CONN_LABEL[conn]}</Badge>
        <span className="faint live-stats">
          {fps.toFixed(1)} fps{lag !== null ? ` · 點擊→畫面 ${lag} ms` : ""}
        </span>
        {conn === "closed" && tab && (
          <button className="btn-sm" onClick={() => setRetry((n) => n + 1)}>
            重新連線
          </button>
        )}
      </div>
      {page && (
        <div className="live-url mono" title={page.url}>
          {page.url}
        </div>
      )}
      {tabsErr && <div className="badge-bad live-note">{tabsErr}</div>}
      {note && (
        <div className={`${note.tone} live-note`} onClick={() => setNote(null)}>
          {note.msg}
        </div>
      )}

      <div className="live-stage">
        <div className="live-frame">
          <img ref={imgRef} alt={current ? `即時畫面:${current.title}` : "即時畫面"} onClick={onTap} draggable={false} style={{ display: hasFrame ? "block" : "none" }} />
          {tap && <span key={tap.k} className="live-tap" style={{ left: tap.x, top: tap.y }} />}
        </div>
        {!hasFrame && <div className="loading">{tab ? "等待第一個畫面…" : "請選一個分頁。"}</div>}
      </div>

      <div className="live-tools">
        <button onClick={() => void send({ type: "scroll", deltaY: -page60() })} aria-label="向上捲動">
          ↑
        </button>
        <button onClick={() => void send({ type: "scroll", deltaY: page60() })} aria-label="向下捲動">
          ↓
        </button>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="要輸入到目前欄位的文字"
          aria-label="要輸入的文字"
          onKeyDown={(e) => {
            if (e.key === "Enter" && text) {
              void send({ type: "text", text });
              setText("");
            }
          }}
        />
        <button
          className="btn-primary"
          disabled={!text}
          onClick={() => {
            void send({ type: "text", text });
            setText("");
          }}
        >
          輸入
        </button>
        <button onClick={() => void send({ type: "key", key: "Backspace" })} aria-label="退格">
          ⌫
        </button>
        <button onClick={() => void send({ type: "key", key: "Tab" })}>Tab</button>
        <button onClick={() => void send({ type: "key", key: "Enter" })}>Enter</button>
      </div>

      {dialog && (
        <div className="live-sheet" role="alertdialog" aria-label="網頁對話框">
          <div className="label muted">{dialog.guard ? "送出防護" : `網頁 ${dialog.type} 對話框`}</div>
          <p className="live-msg">{dialog.message}</p>
          {dialog.guard && (
            <p className="badge-warn" style={{ padding: 6 }}>
              <b>按「接受」會立刻送出這份申請。</b>請先檢查上面的表單。按「取消」則不送出。
            </p>
          )}
          {dialog.type === "prompt" && <input value={promptText} onChange={(e) => setPromptText(e.target.value)} style={{ width: "100%" }} aria-label="回答" />}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 8 }}>
            {dialog.type !== "alert" && <button onClick={() => void answer(false)}>取消</button>}
            <button className="btn-primary" onClick={() => void answer(true)}>
              {dialog.type === "alert" ? "確定" : dialog.guard ? "接受:送出" : "接受"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
