import { useCallback, useEffect, useRef, useState } from "react";
import type { BoardEvent, ClaudeTarget, RunBoard, TargetLibrary } from "../shared/types";
import { ApplicationsView } from "./components/ApplicationsView";
import { PipelineView, type PipelineTab } from "./components/PipelineView";
import { CvView } from "./components/CvView";
import { MockView } from "./components/MockView";
import { ClaudeDrawer, type DrawerState } from "./components/ClaudeDrawer";
import { api } from "./api";

type Route =
  | { name: "home" }
  | { name: "app"; jd: string }
  | { name: "pipeline"; tab: PipelineTab }
  | { name: "mock"; jd: string | null; id: string | null };

function parseHash(): Route {
  const mk = /^#\/mock(?:\/([^/?#]+)\/([^/?#]+))?\/?$/.exec(window.location.hash);
  if (mk) return { name: "mock", jd: mk[1] ? decodeURIComponent(mk[1]) : null, id: mk[2] ? decodeURIComponent(mk[2]) : null };
  const p = /^#\/pipeline(?:\/(daily|interest|submissions))?\/?$/.exec(window.location.hash);
  if (p) return { name: "pipeline", tab: (p[1] as PipelineTab | undefined) ?? "daily" };
  const m = /^#\/app\/([^/?#]+)/.exec(window.location.hash);
  return m ? { name: "app", jd: decodeURIComponent(m[1]) } : { name: "home" };
}

type Theme = "system" | "light" | "dark";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("cvd-theme") as Theme) || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("cvd-theme", theme);
    } catch {
      /* storage unavailable */
    }
  }, [theme]);
  const cycle = () => setTheme((t) => (t === "system" ? "light" : t === "light" ? "dark" : "system"));
  return [theme, cycle];
}

export function App() {
  const [route, setRoute] = useState<Route>(parseHash);
  const [theme, cycleTheme] = useTheme();
  const [drawer, setDrawer] = useState<DrawerState | null>(null);
  const [board, setBoard] = useState<RunBoard | null>(null);
  /** Bumped when a run ends and the candidate reloads; CvView refetches. */
  const [reloadKey, setReloadKey] = useState(0);
  const [changedLines, setChangedLines] = useState<string[]>([]);

  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const routeRef = useRef(route);
  routeRef.current = route;

  // Shared run board: queue/running state for every page, and a refresh of the open CV when a
  // run (any run, not only one started here) changed it.
  useEffect(() => {
    const es = new EventSource(api.eventsUrl);
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data) as BoardEvent;
      if (ev.type === "board") setBoard(ev.board);
      else {
        const r = routeRef.current;
        if (r.name === "app" && ev.jds.includes(r.jd)) {
          setChangedLines(ev.changedLines[r.jd] ?? []);
          setReloadKey((k) => k + 1);
        }
      }
    };
    return () => es.close();
  }, []);

  const ask = useCallback(
    (jd: string, target: ClaudeTarget, opts?: { instruction?: string; library?: TargetLibrary }) =>
      setDrawer({ tab: "run", jd, target, runId: null, preset: opts?.instruction, library: opts?.library }),
    [],
  );
  const openHistory = () => setDrawer((d) => ({ ...(d ?? { jd: null, target: null, runId: null }), tab: "history" }));

  const appTitle = route.name === "app" ? route.jd : null;

  return (
    <>
      <header className="topbar">
        <a className="brand" href="#/">
          <span className="brand-mark" aria-hidden /> <span className="brand-name">CV dashboard</span>
        </a>
        <nav className="top-nav" aria-label="Pages">
          <a href="#/" aria-current={route.name === "home" ? "page" : undefined}>
            Applications
          </a>
          <a href="#/pipeline" aria-current={route.name === "pipeline" ? "page" : undefined}>
            Pipeline
          </a>
          <a href="#/mock" aria-current={route.name === "mock" ? "page" : undefined}>
            模擬面試
          </a>
        </nav>
        {appTitle && (
          <nav className="crumbs" aria-label="Breadcrumb">
            <a href="#/">Applications</a>
            <span aria-hidden>/</span>
            <span>{appTitle}</span>
          </nav>
        )}
        <span className="spacer" />
        {board && board.running + board.queued > 0 && (
          <button className="btn-ghost run-pill" onClick={openHistory}>
            <span className="dot pulse" aria-hidden /> {board.running} running
            {board.queued > 0 && ` · ${board.queued} queued`}
          </button>
        )}
        <button className="btn-sm" onClick={openHistory}>
          Claude runs
        </button>
        <button className="btn-sm btn-ghost" onClick={cycleTheme} title="Theme: system → light → dark" aria-label={`Theme: ${theme}`}>
          {theme === "system" ? "◐ Auto" : theme === "light" ? "☀ Light" : "☾ Dark"}
        </button>
      </header>
      <main>
        {route.name === "home" ? (
          <ApplicationsView />
        ) : route.name === "mock" ? (
          <MockView key={`${route.jd}/${route.id}`} jd={route.jd} id={route.id} />
        ) : route.name === "pipeline" ? (
          <PipelineView tab={route.tab} onOpenRun={(j, runId) => setDrawer({ tab: "run", jd: j, target: null, runId })} />
        ) : (
          <CvView key={route.jd} jd={route.jd} reloadKey={reloadKey} changedLines={changedLines} onAsk={ask} onOpenRun={(j, runId) => setDrawer({ tab: "run", jd: j, target: null, runId })} />
        )}
      </main>
      {drawer && (
        <ClaudeDrawer
          state={drawer}
          setState={setDrawer}
          onClose={() => setDrawer(null)}
          board={board}
          onReload={(jd, newLines) => {
            setChangedLines(newLines);
            setReloadKey((k) => k + 1);
            if (route.name !== "app" || route.jd !== jd) window.location.hash = `#/app/${encodeURIComponent(jd)}`;
          }}
        />
      )}
    </>
  );
}
