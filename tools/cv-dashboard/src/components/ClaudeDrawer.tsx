import { useEffect, useRef, useState } from "react";
import type { BoardRun, ClaudeRun, ClaudeRunSummary, ClaudeTarget, RunBoard, RunEvent, RunStatus, TargetLibrary } from "../../shared/types";
import { api } from "../api";
import { unwatchRun, useRunEvents, useWatchedRuns, watchRun } from "../runStreams";
import { Badge } from "./ui";
import { LibraryPanel } from "./LibraryBits";

export interface DrawerState {
  tab: "run" | "history";
  jd: string | null;
  target: ClaudeTarget | null;
  runId: string | null;
  /** Instruction to pre-fill (a "Use" / "Swap in" button). */
  preset?: string;
  /** Notion links and variants for the target, from the CV view. */
  library?: TargetLibrary;
}

interface Props {
  state: DrawerState;
  setState: (s: DrawerState) => void;
  onClose: () => void;
  board: RunBoard | null;
  onReload: (jd: string, newLines: string[]) => void;
}

const STATUS_TONE: Record<string, string> = {
  queued: "badge-info",
  running: "badge-warn",
  succeeded: "badge-ok",
  failed: "badge-bad",
  cancelled: "badge-neutral",
  timeout: "badge-bad",
};

function targetLabel(t: ClaudeTarget | null): string {
  if (!t) return "";
  if (t.kind === "term") return `JD term “${t.term}”`;
  return [t.kind.replace("_", " "), t.code].filter(Boolean).join(" · ");
}

function duration(a: string | null, b: string | null): string {
  if (!a) return "not started";
  const ms = (b ? Date.parse(b) : Date.now()) - Date.parse(a);
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function isLive(s: RunStatus | undefined): boolean {
  return s === "queued" || s === "running";
}

function statusText(status: RunStatus, position: number | null): string {
  return status === "queued" && position ? `queued #${position}` : status;
}

export function ClaudeDrawer({ state, setState, onClose, board, onReload }: Props) {
  const [instruction, setInstruction] = useState("");
  const [run, setRun] = useState<ClaudeRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [history, setHistory] = useState<ClaudeRunSummary[] | null>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const logEnd = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const jd = state.jd ?? run?.jd ?? null;
  const target = state.target ?? run?.target ?? null;

  // Esc closes; focus the instruction box when composing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector(".modal-backdrop")) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // A "Use" button opened the drawer: pre-fill (each click gives a new target object).
  useEffect(() => {
    if (state.preset != null && !state.runId) setInstruction(state.preset);
  }, [state.preset, state.target]);

  function insertAtCursor(text: string) {
    const el = textRef.current;
    const start = el?.selectionStart ?? instruction.length;
    const end = el?.selectionEnd ?? instruction.length;
    const next = instruction.slice(0, start) + text + instruction.slice(end);
    setInstruction(next);
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      el.setSelectionRange(start + text.length, start + text.length);
    });
  }

  useEffect(() => {
    if (state.tab === "run" && !state.runId) textRef.current?.focus();
    else panelRef.current?.focus();
  }, [state.tab, state.runId]);

  const events = useRunEvents(state.runId);
  const watched = useWatchedRuns();
  const boardRows = new Map<string, BoardRun>((board?.runs ?? []).map((r) => [r.id, r]));
  const live = state.runId ? boardRows.get(state.runId) : undefined;
  const exit = events.find((e): e is Extract<RunEvent, { type: "exit" }> => e.type === "exit");
  const status: RunStatus | undefined = exit?.status ?? live?.status ?? run?.status;
  const running = Boolean(state.runId) && isLive(status);

  // Follow the selected run; its stream keeps going when another run is selected.
  useEffect(() => {
    if (state.runId) watchRun(state.runId);
  }, [state.runId]);

  // (Re)load the selected run when it starts or ends: the prompt exists only once it started,
  // cvChanges only once it ended.
  useEffect(() => {
    if (!state.runId) {
      setRun(null);
      return;
    }
    let alive = true;
    api
      .run(state.runId)
      .then((r) => {
        if (!alive) return;
        setRun(r);
        setInstruction(r.instruction);
      })
      .catch((e) => alive && setError(String(e.message ?? e)));
    return () => {
      alive = false;
    };
  }, [state.runId, status]);

  useEffect(() => {
    logEnd.current?.scrollIntoView({ block: "end" });
  }, [events.length]);

  // History: refetch whenever a run is added, starts or ends.
  const boardKey = (board?.runs ?? []).map((r) => `${r.id}:${r.status}`).join(",");
  useEffect(() => {
    if (state.tab !== "history") return;
    api.runs().then((r) => setHistory(r.runs)).catch((e) => setError(String(e.message ?? e)));
  }, [state.tab, boardKey]);

  async function send(ins = instruction) {
    if (!jd || !target || !ins.trim() || sending) return;
    setSending(true);
    setError(null);
    try {
      const { runId } = await api.startRun(jd, target, ins.trim());
      watchRun(runId);
      setState({ ...state, tab: "run", jd, target, runId });
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setSending(false);
    }
  }

  async function cancel(id = state.runId) {
    if (!id) return;
    try {
      await api.cancelRun(id);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  }

  const changes = run && !isLive(run.status) ? run.cvChanges : [];
  const others = (board?.runs ?? []).filter((r) => isLive(r.status) && r.id !== state.runId);

  return (
    <>
      <div className="drawer-backdrop" onMouseDown={onClose} />
      <aside className="drawer" role="dialog" aria-label="Ask Claude Code" tabIndex={-1} ref={panelRef}>
        <div className="drawer-head">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={state.tab === "run"} onClick={() => setState({ ...state, tab: "run" })}>
              Ask Claude Code
            </button>
            <button role="tab" aria-selected={state.tab === "history"} onClick={() => setState({ ...state, tab: "history" })}>
              History
            </button>
          </div>
          <span style={{ flex: 1 }} />
          <button className="btn-sm" onClick={onClose} aria-label="Close drawer">
            Close <kbd>Esc</kbd>
          </button>
        </div>

        {state.tab === "history" ? (
          <div className="drawer-body">
            {!history && <div className="loading">Loading runs…</div>}
            {history && !history.length && <div className="empty">No Claude runs yet.</div>}
            <div className="history">
              {history?.map((h) => {
                const b = boardRows.get(h.id);
                const st = b?.status ?? h.status;
                return (
                <div
                  key={h.id}
                  role="button"
                  tabIndex={0}
                  className="history-item"
                  onClick={() => setState({ tab: "run", jd: h.jd, target: h.target, runId: h.id })}
                  onKeyDown={(e) => e.key === "Enter" && setState({ tab: "run", jd: h.jd, target: h.target, runId: h.id })}
                >
                  <div className="h-top">
                    <span>
                      <b>{h.jd}</b> <span className="muted">· {targetLabel(h.target)}</span>
                    </span>
                    <span>
                      {isLive(st) && (
                        <button
                          className="btn-sm btn-ghost"
                          onClick={(e) => {
                            e.stopPropagation();
                            cancel(h.id);
                          }}
                        >
                          {st === "queued" ? "Remove" : "Cancel"}
                        </button>
                      )}{" "}
                      <Badge tone={STATUS_TONE[st]}>{statusText(st, b?.position ?? h.position)}</Badge>
                    </span>
                  </div>
                  <div className="h-ins">{h.instruction}</div>
                  {b && b.status === "running" && b.lastLines.length > 0 && <div className="h-last">{b.lastLines.at(-1)}</div>}
                  <div className="faint" style={{ fontSize: 11.5 }}>
                    {new Date(h.startedAt ?? h.queuedAt ?? 0).toLocaleString()} · {duration(h.startedAt, h.endedAt)}
                    {h.exitCode != null && ` · exit ${h.exitCode}`}
                    {h.costUsd != null && ` · $${h.costUsd.toFixed(3)}`}
                    {h.cvChanges.length > 0 && ` · ${h.cvChanges.length} CV line${h.cvChanges.length > 1 ? "s" : ""} changed`}
                    {(b?.filesChanged ?? h.filesChanged ?? []).length > 0 && ` · ${(b?.filesChanged ?? h.filesChanged ?? []).length} files edited`}
                  </div>
                </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div className="drawer-body">
            {watched.length > 0 && (
              <div className="watched" role="tablist" aria-label="Watched runs">
                {watched.map((id) => {
                  const b = boardRows.get(id);
                  return (
                    <span key={id} className={`watched-chip ${id === state.runId ? "on" : ""}`}>
                      <button
                        role="tab"
                        aria-selected={id === state.runId}
                        onClick={() => setState({ tab: "run", jd: b?.jd ?? null, target: b?.target ?? null, runId: id })}
                        title={b?.instruction}
                      >
                        {b && isLive(b.status) && <span className="dot pulse" aria-hidden />} {b?.jd ?? id.slice(0, 8)}
                        {b && <span className="faint"> · {statusText(b.status, b.position)}</span>}
                      </button>
                      <button
                        className="x"
                        aria-label="Stop watching"
                        onClick={() => {
                          unwatchRun(id);
                          if (id === state.runId) setState({ ...state, runId: null });
                        }}
                      >
                        ×
                      </button>
                    </span>
                  );
                })}
                <button className="btn-sm btn-ghost" onClick={() => setState({ ...state, runId: null })} disabled={!state.runId}>
                  + New request
                </button>
              </div>
            )}
            {!jd || !target ? (
              <div className="empty">Pick a CV block or a missing JD term and press “Ask Claude”.</div>
            ) : (
              <>
                <div className="target-card">
                  <div className="muted" style={{ fontSize: 12 }}>
                    Application <b>{jd}</b> · target
                  </div>
                  <div>
                    <b>{targetLabel(target)}</b>
                  </div>
                  {target.text && <div className="text">{target.text}</div>}
                </div>

                {state.library && (state.library.item || state.library.keyword) && (
                  <LibraryPanel lib={state.library} onInsert={insertAtCursor} disabled={Boolean(state.runId)} />
                )}

                <div>
                  <label htmlFor="instruction" className="muted" style={{ fontSize: 12.5 }}>
                    What should change? (any language)
                  </label>
                  <textarea
                    id="instruction"
                    ref={textRef}
                    className="instruction"
                    value={instruction}
                    readOnly={Boolean(state.runId)}
                    placeholder="e.g. 把 lead 改成更貼近 JD 的 'model deployment'，不要改數字"
                    onChange={(e) => setInstruction(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                        e.preventDefault();
                        if (!state.runId) send();
                      }
                    }}
                  />
                </div>

                <div className="row-actions">
                  {!state.runId && (
                    <button className="btn-primary" onClick={() => send()} disabled={!instruction.trim() || sending}>
                      {sending ? "Starting…" : "Send"} <kbd>Ctrl</kbd>+<kbd>Enter</kbd>
                    </button>
                  )}
                  {running && (
                    <button className="btn-danger" onClick={() => cancel()}>
                      {status === "queued" ? "Remove from queue" : "Cancel run"}
                    </button>
                  )}
                  {state.runId && !running && (
                    <>
                      <button onClick={() => send(run?.instruction ?? instruction)} disabled={sending}>
                        Re-run
                      </button>
                      <button onClick={() => setState({ ...state, runId: null })}>New request</button>
                    </>
                  )}
                  <span className="spacer" />
                  {state.runId && status && <Badge tone={STATUS_TONE[status]}>{statusText(status, live?.position ?? null)}</Badge>}
                </div>

                {error && <div className="error">{error}</div>}

                {!state.runId && (
                  <p className="faint" style={{ fontSize: 12, margin: 0 }}>
                    Runs <code>claude -p</code> in the repo root with Read, Edit, Write, Glob, Grep and the tailor-cv scripts only
                    (acceptEdits). The skill’s truth rules apply; up to {board?.maxConcurrent ?? 3} runs at once (one per
                    application), the rest wait in a queue; 15-minute limit. Runs see each other on the shared run board and take
                    a lock for the library seed, CONF file, DB and Notion.
                  </p>
                )}

                {others.length > 0 && (
                  <div className="others">
                    <div className="muted" style={{ fontSize: 12 }}>Other runs</div>
                    {others.map((o) => (
                      <button key={o.id} className="other-run" onClick={() => setState({ tab: "run", jd: o.jd, target: o.target, runId: o.id })}>
                        <Badge tone={STATUS_TONE[o.status]}>{statusText(o.status, o.position)}</Badge> <b>{o.jd}</b>{" "}
                        <span className="muted">· {o.instruction}</span>
                        {o.lastLines.length > 0 && <div className="h-last">{o.lastLines.at(-1)}</div>}
                      </button>
                    ))}
                  </div>
                )}

                {state.runId && (
                  <div className="log" aria-live="polite">
                    {events.length === 0 && <div className="ev-system">Waiting for output…</div>}
                    {status === "queued" && (
                      <div className="ev-system">
                        Waiting in the queue{live?.position ? ` (position ${live.position})` : ""}. It starts when a slot is free
                        {" "}and no other run is working on {jd}.
                      </div>
                    )}
                    {events.map((ev, i) => (
                      <EventLine key={i} ev={ev} />
                    ))}
                    {running && (
                      <div className="ev-system">
                        <span className="dot pulse" style={{ color: "var(--warn)" }} /> running…
                      </div>
                    )}
                    <div ref={logEnd} />
                  </div>
                )}

                {run && !isLive(run.status) && jd && (
                  <div>
                    <div className="row-actions" style={{ marginBottom: 8 }}>
                      <h3>CV changes (03_cv.md)</h3>
                      <span className="spacer" />
                      <button className="btn-primary btn-sm" onClick={() => onReload(jd, changes.map((c) => c.new).filter((x): x is string => x != null))}>
                        Reload CV
                      </button>
                    </div>
                    {changes.length === 0 ? (
                      <div className="muted" style={{ fontSize: 13 }}>No line in 03_cv.md changed during this run.</div>
                    ) : (
                      <div className="changes">
                        {changes.map((c, i) => (
                          <div className="change" key={i}>
                            {c.old != null && <div className="old">{c.old}</div>}
                            {c.new != null && <div className="new">{c.new}</div>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {run && run.prompt && (
                  <details>
                    <summary>Prompt and command sent</summary>
                    <pre className="prompt">{run.prompt}</pre>
                    <pre className="prompt">{run.argv.map((a) => (a.includes(" ") ? JSON.stringify(a) : a)).join(" ")}</pre>
                  </details>
                )}
              </>
            )}
          </div>
        )}
      </aside>
    </>
  );
}

function EventLine({ ev }: { ev: RunEvent }) {
  switch (ev.type) {
    case "system":
      return <div className="ev-system">{ev.text}</div>;
    case "text":
      return <div className="ev-text">{ev.text}</div>;
    case "tool":
      return (
        <div className="ev-tool">
          › {ev.name} {ev.summary}
        </div>
      );
    case "tool_result":
      return <div className={`ev-tool-result ${ev.isError ? "err" : ""}`}>  ↳ {ev.summary || "(empty)"}</div>;
    case "stderr":
      return <div className="ev-stderr">{ev.text}</div>;
    case "result":
      return (
        <div className="ev-result">
          <b>{ev.isError ? "Result (error)" : "Result"}</b>
          {ev.durationMs != null && <span className="faint"> · {Math.round(ev.durationMs / 1000)}s</span>}
          {ev.turns != null && <span className="faint"> · {ev.turns} turns</span>}
          {ev.costUsd != null && <span className="faint"> · ${ev.costUsd.toFixed(3)}</span>}
          {"\n"}
          {ev.text}
        </div>
      );
    case "exit":
      return (
        <div className="ev-exit">
          Exit code {String(ev.code)}
          {ev.signal ? ` (${ev.signal})` : ""} · {ev.status}
        </div>
      );
  }
}
