// Live logs of the runs the candidate is watching. Each stream stays open (and keeps its events)
// while the drawer switches between runs or is closed; it closes itself on the run's exit event.
import { useSyncExternalStore } from "react";
import type { RunEvent } from "../shared/types";
import { api } from "./api";

interface Stream {
  events: RunEvent[];
  es: EventSource | null;
}

const streams = new Map<string, Stream>();
let order: string[] = [];
const listeners = new Set<() => void>();
const EMPTY: RunEvent[] = [];

function notify(): void {
  order = [...streams.keys()];
  for (const l of listeners) l();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

/** Start following a run (no-op if already followed). The server replays past events first. */
export function watchRun(id: string, onExit?: (ev: Extract<RunEvent, { type: "exit" }>) => void): void {
  if (streams.has(id)) return;
  const s: Stream = { events: [], es: new EventSource(api.streamUrl(id)) };
  streams.set(id, s);
  s.es!.onmessage = (m) => {
    const ev = JSON.parse(m.data) as RunEvent;
    s.events = [...s.events, ev];
    if (ev.type === "exit") {
      s.es?.close();
      s.es = null;
      onExit?.(ev);
    }
    notify();
  };
  // The server ends the stream after replaying a finished run; nothing to retry then.
  s.es!.onerror = () => {
    s.es?.close();
    s.es = null;
    notify();
  };
  notify();
}

export function unwatchRun(id: string): void {
  streams.get(id)?.es?.close();
  streams.delete(id);
  notify();
}

export function useRunEvents(id: string | null): RunEvent[] {
  return useSyncExternalStore(subscribe, () => (id ? streams.get(id)?.events ?? EMPTY : EMPTY));
}

/** Ids of the watched runs, in the order they were opened. */
export function useWatchedRuns(): string[] {
  return useSyncExternalStore(subscribe, () => order);
}
