// Live browser view: SSE events from GET /api/apply/live/:targetId/stream and the frame → page
// coordinate mapping shared by the client and the tests.

/** Page.screencastFrame metadata (CSS px of the tab's viewport). */
export interface LiveFrameMeta {
  deviceWidth: number;
  deviceHeight: number;
  offsetTop: number;
  pageScaleFactor: number;
  scrollOffsetX: number;
  scrollOffsetY: number;
  timestamp?: number;
}

export interface LiveDialog {
  type: "alert" | "confirm" | "prompt" | "beforeunload";
  message: string;
  defaultPrompt?: string;
  /** The page guard's confirm() in front of a final submit (scripts/apply-guard.js). */
  guard: boolean;
  url?: string;
}

export interface LiveTab {
  id: string;
  title: string;
  url: string;
  /** Application whose status.json points at this tab (targetId or same URL). */
  jd: string | null;
  viewers: number;
  screencasting: boolean;
}

export interface LiveAppLink {
  jd: string;
  state: string;
  submitControl: string | null;
}

export type LiveEvent =
  | { type: "hello"; targetId: string; url: string; title: string; dialog: LiveDialog | null; app: LiveAppLink | null }
  | { type: "frame"; seq: number; data: string; meta: LiveFrameMeta; at: number }
  | { type: "dialog"; dialog: LiveDialog }
  | { type: "dialog-closed"; accepted: boolean }
  | { type: "page"; url: string; title: string }
  | { type: "status"; jd: string; state: string; confirmationText?: string | null; error?: string | null }
  | { type: "closed"; reason: string };

export type LiveInput =
  | { type: "click"; x: number; y: number }
  | { type: "scroll"; x?: number; y?: number; deltaY: number }
  | { type: "key"; key: string }
  | { type: "text"; text: string };

/** Keys the Live view may send (Input.dispatchKeyEvent). */
export const LIVE_KEYS: Record<string, { code: string; vk: number; text?: string }> = {
  Enter: { code: "Enter", vk: 13, text: "\r" },
  Tab: { code: "Tab", vk: 9 },
  Backspace: { code: "Backspace", vk: 8 },
  Delete: { code: "Delete", vk: 46 },
  Escape: { code: "Escape", vk: 27 },
  Space: { code: "Space", vk: 32, text: " " },
  ArrowUp: { code: "ArrowUp", vk: 38 },
  ArrowDown: { code: "ArrowDown", vk: 40 },
  ArrowLeft: { code: "ArrowLeft", vk: 37 },
  ArrowRight: { code: "ArrowRight", vk: 39 },
  PageUp: { code: "PageUp", vk: 33 },
  PageDown: { code: "PageDown", vk: 34 },
  Home: { code: "Home", vk: 36 },
  End: { code: "End", vk: 35 },
};

/** A point on the displayed frame (CSS px of the <img>, origin top-left) → the tab's viewport CSS px. */
export function frameToPage(dx: number, dy: number, displayW: number, displayH: number, meta: LiveFrameMeta): { x: number; y: number } {
  const clamp = (v: number, max: number) => Math.max(0, Math.min(max, v));
  return {
    x: Math.round(clamp((dx / displayW) * meta.deviceWidth, meta.deviceWidth - 1) * 10) / 10,
    y: Math.round(clamp((dy / displayH) * meta.deviceHeight, meta.deviceHeight - 1) * 10) / 10,
  };
}
