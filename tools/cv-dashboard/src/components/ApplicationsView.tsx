import { useEffect, useMemo, useState } from "react";
import type { ApplicationRow } from "../../shared/types";
import { api } from "../api";
import { AtsBar, Badge, layoutTone, verdictTone } from "./ui";

type SortKey = "company" | "title" | "atsOverall" | "keywords" | "concepts" | "layoutStatus" | "hr" | "hm" | "verdict" | "date";

const COLUMNS: { key: SortKey; label: string; num?: boolean; cls?: string }[] = [
  { key: "company", label: "Company" },
  { key: "title", label: "Role" },
  { key: "atsOverall", label: "ATS" },
  { key: "keywords", label: "Keywords", num: true },
  { key: "concepts", label: "Concepts", num: true },
  { key: "layoutStatus", label: "Layout" },
  { key: "hr", label: "HR", num: true },
  { key: "hm", label: "HM", num: true },
  { key: "verdict", label: "Verdict" },
  { key: "date", label: "Date", cls: "hide-sm" },
];

function sortValue(r: ApplicationRow, k: SortKey): string | number {
  if (k === "concepts") {
    const c = r.concepts;
    const n = c ? c.covered + c.sot + c.gap + c.doNotClaim : 0;
    return c && n ? c.covered / n : -1;
  }
  if (k === "keywords") return r.keywordsTotal ? (r.keywordsFound ?? 0) / r.keywordsTotal : -1;
  const v = r[k as keyof ApplicationRow];
  return typeof v === "number" ? v : String(v ?? "").toLowerCase();
}

export function ApplicationsView() {
  const [rows, setRows] = useState<ApplicationRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "atsOverall", dir: -1 });

  useEffect(() => {
    api.applications().then(setRows).catch((e) => setError(String(e.message ?? e)));
  }, []);

  const sorted = useMemo(() => {
    if (!rows) return [];
    return [...rows].sort((a, b) => {
      const x = sortValue(a, sort.key);
      const y = sortValue(b, sort.key);
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  }, [rows, sort]);

  const toggle = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: (s.dir * -1) as 1 | -1 } : { key, dir: key === "company" || key === "title" ? 1 : -1 }));

  const open = (jd: string) => (window.location.hash = `#/app/${encodeURIComponent(jd)}`);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Applications</h1>
          <div className="muted">Tailored CVs recorded in the library DB. Pick one to see its library items and JD keyword coverage.</div>
        </div>
        {rows && <div className="muted">{rows.length} applications</div>}
      </div>
      {error && <div className="error">Could not load applications: {error}</div>}
      <div className="panel table-wrap">
        {!rows && !error ? (
          <div className="loading">Loading…</div>
        ) : (
          <table className="apps">
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th
                    key={c.key}
                    className={[c.num ? "num" : "", c.cls ?? ""].join(" ")}
                    aria-sort={sort.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                  >
                    <button onClick={() => toggle(c.key)}>
                      {c.label}
                      <span className="arrow" aria-hidden>
                        {sort.key === c.key ? (sort.dir === 1 ? "▲" : "▼") : "↕"}
                      </span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr key={r.jd} onClick={() => open(r.jd)}>
                  <td className="company">
                    <a href={`#/app/${encodeURIComponent(r.jd)}`} onClick={(e) => e.stopPropagation()}>
                      {r.company}
                    </a>
                    {r.company !== r.jd && <div className="sub mono">{r.jd}</div>}
                  </td>
                  <td>
                    <div>{r.title}</div>
                    <div className="sub">{r.headline.split("|").slice(1).join("|").trim() || r.headline} · {r.country}</div>
                  </td>
                  <td>
                    <AtsBar value={r.atsOverall} />
                  </td>
                  <td className="num nowrap">
                    {r.keywordsFound ?? "—"}
                    <span className="muted">/{r.keywordsTotal ?? "—"}</span>
                  </td>
                  <td className="num nowrap">
                    {r.concepts ? (
                      <span
                        title={`${r.concepts.covered} covered · ${r.concepts.sot} SoT-supported · ${r.concepts.gap} gap · ${r.concepts.doNotClaim} do not claim${r.concepts.unresolved ? ` · ${r.concepts.unresolved} unresolved terms` : ""}`}
                      >
                        <span style={{ color: "var(--ok)" }}>{r.concepts.covered}</span>
                        <span className="muted"> / </span>
                        <span style={{ color: "var(--info)" }}>{r.concepts.sot}</span>
                        <span className="muted"> / </span>
                        <span style={{ color: "var(--warn)" }}>{r.concepts.gap + r.concepts.doNotClaim}</span>
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="nowrap">
                    <Badge tone={layoutTone(r.layoutStatus)}>{r.layoutStatus ?? "—"}</Badge>{" "}
                    <span className="muted">{r.utilisation ?? ""}</span>
                  </td>
                  <td className="num">{r.hr ?? "—"}</td>
                  <td className="num">{r.hm ?? "—"}</td>
                  <td>
                    <Badge tone={verdictTone(r.verdict)}>{r.verdict ?? "—"}</Badge>
                  </td>
                  <td className="hide-sm nowrap muted">{r.date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
