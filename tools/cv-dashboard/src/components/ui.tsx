import { useEffect, useRef, type ReactNode } from "react";
import { termRanges } from "../../shared/match";

export function layoutTone(status: string | null): string {
  if (!status) return "badge-neutral";
  if (/pass/i.test(status)) return "badge-ok";
  if (/warn/i.test(status)) return "badge-warn";
  return "badge-bad";
}

export function verdictTone(verdict: string | null): string {
  if (!verdict) return "badge-neutral";
  if (/^(submit|ship|ready|pass|approve)/i.test(verdict)) return "badge-ok";
  if (/revise/i.test(verdict)) return "badge-warn";
  return "badge-bad";
}

export function scoreColor(v: number | null): string {
  if (v == null) return "var(--faint)";
  if (v >= 75) return "var(--ok)";
  if (v >= 60) return "var(--accent)";
  return "var(--warn)";
}

export function AtsBar({ value }: { value: number | null }) {
  return (
    <div className="atsbar" aria-label={`ATS ${value ?? "n/a"}`}>
      <span className="val">{value ?? "—"}</span>
      <div className="track" aria-hidden>
        <div className="fill" style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%`, background: scoreColor(value) }} />
      </div>
    </div>
  );
}

export function Badge({ tone = "badge-neutral", children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return (
    <span className={`badge ${tone}`} title={title}>
      {children}
    </span>
  );
}

export function statusTone(status: string | null | undefined): string {
  if (!status) return "badge-neutral";
  if (/^poc/i.test(status)) return "badge-warn";
  if (/^production/i.test(status)) return "badge-ok";
  return "badge-neutral";
}

/** Text with every JD term in `terms` marked; `active` gets the strong highlight. */
export function Highlight({ text, terms, active }: { text: string; terms: string[]; active: string | null }) {
  if (!terms.length || !text) return <>{text}</>;
  const marks: { s: number; e: number; term: string }[] = [];
  // Longer terms first so "REST API development" wins over "REST APIs"-style overlaps.
  for (const term of [...terms].sort((a, b) => b.length - a.length)) {
    for (const [s, e] of termRanges(term, text)) {
      if (!marks.some((m) => s < m.e && e > m.s)) marks.push({ s, e, term });
    }
  }
  if (!marks.length) return <>{text}</>;
  marks.sort((a, b) => a.s - b.s);
  const out: ReactNode[] = [];
  let pos = 0;
  marks.forEach((m, i) => {
    if (m.s > pos) out.push(text.slice(pos, m.s));
    out.push(
      <mark key={i} className={m.term === active ? "active" : undefined} title={m.term}>
        {text.slice(m.s, m.e)}
      </mark>,
    );
    pos = m.e;
  });
  if (pos < text.length) out.push(text.slice(pos));
  return <>{out}</>;
}

export function Modal({ title, onClose, children, actions, closeLabel = "Close" }: { title: string; onClose: () => void; children: ReactNode; actions?: ReactNode; closeLabel?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal panel" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <div className="panel-head">
          <h2>{title}</h2>
          <div className="row-actions">
            {actions}
            <button className="btn-sm" onClick={onClose} aria-label={closeLabel}>
              {closeLabel} <kbd>Esc</kbd>
            </button>
          </div>
        </div>
        <div className="panel-body">{children}</div>
      </div>
    </div>
  );
}

/** Minimal markdown for the JD brief / review: **bold** only, whitespace kept. */
export function BoldText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) => (p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : p))}
    </>
  );
}
