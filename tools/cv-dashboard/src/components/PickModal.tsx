import { useEffect, useState } from "react";
import type { PickSuggestion } from "../../shared/types";
import { api } from "../api";
import { Badge, Modal } from "./ui";

const STATE_TONE: Record<string, string> = {
  same: "badge-neutral",
  changed: "badge-warn",
  "current only": "badge-bad",
  "suggested only": "badge-ok",
};

export function PickModal({ jd, onClose }: { jd: string; onClose: () => void }) {
  const [data, setData] = useState<PickSuggestion | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showSame, setShowSame] = useState(true);

  useEffect(() => {
    api.pick(jd).then(setData).catch((e) => setError(String(e.message ?? e)));
  }, [jd]);

  const diffs = data ? data.diff.filter((d) => d.state !== "same").length : 0;

  return (
    <Modal
      title={`DB suggestion — library_db.py pick --jd ${jd}`}
      onClose={onClose}
      actions={
        data && (
          <label className="muted" style={{ fontSize: 12.5, display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={showSame} onChange={(e) => setShowSame(e.target.checked)} /> show unchanged
          </label>
        )
      }
    >
      {error && <div className="error">{error}</div>}
      {!data && !error && <div className="loading">Running library_db.py pick…</div>}
      {data && (
        <>
          {data.exitCode !== 0 && <div className="error">pick exited with code {String(data.exitCode)}. {data.stderr}</div>}
          <p className="muted" style={{ marginTop: 0 }}>
            {data.header} · <b>{diffs}</b> difference{diffs === 1 ? "" : "s"} vs the current picks
            {data.fitVerdict && (
              <>
                {" "}· fit <Badge tone={/PASS/.test(data.fitVerdict) ? "badge-ok" : "badge-warn"}>{data.fitVerdict}</Badge>
              </>
            )}
          </p>
          <table className="diff">
            <thead>
              <tr>
                <th style={{ width: "40%" }}>Current (this CV)</th>
                <th style={{ width: "40%" }}>DB suggestion</th>
                <th>Diff</th>
              </tr>
            </thead>
            <tbody>
              {data.diff
                .filter((d) => showSame || d.state !== "same")
                .map((d) => (
                  <tr key={d.key + d.state} className={d.state.replace(" ", "-")}>
                    <td className="cur">{d.current ?? ""}</td>
                    <td className="sug">{d.suggested ?? ""}</td>
                    <td>
                      <Badge tone={STATE_TONE[d.state]}>{d.state}</Badge>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          <h3 style={{ margin: "16px 0 6px" }}>Rationale</h3>
          <pre className="raw">{data.rationale.join("\n")}</pre>
          {data.fit.length > 0 && (
            <>
              <h3 style={{ margin: "16px 0 6px" }}>Fit</h3>
              <pre className="raw">{data.fit.join("\n")}</pre>
            </>
          )}
        </>
      )}
    </Modal>
  );
}
