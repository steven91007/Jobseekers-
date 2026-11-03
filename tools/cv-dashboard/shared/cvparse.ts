// 03_cv.md → typed CV items (structure only; library codes are attached by server/application.ts).
import type { CvItem } from "./types.js";
import { keywordInText, scannedTextByLine, splitLines, stripMd } from "./match.js";

const SECTION = /^\*\*([A-Z][A-Z &/]+)\*\*(.*)$/;
const SKILL_ROW = /^\*\*([^*]+):\*\*\s*(.+)$/;
const CARD = /^\*\*(.+?)\*\*\s+—\s+(.*)$/;
const BULLET = /^- \*\*(.+?)\s*—\s*\*\*\s*(.*)$/;

export function parseCv(md: string, terms: string[] = []): CvItem[] {
  const lines = splitLines(md).map((l) => l.replace(/\r+$/, ""));
  const scanned = scannedTextByLine(md);
  const items: CvItem[] = [];
  let section = "";
  let role: string | undefined;
  let inCard = false;

  lines.forEach((raw, i) => {
    if (!raw.trim()) return;
    const base = { id: `L${i}`, line: i, raw, text: stripMd(raw), section, scanned: scanned.has(i), hits: [] as string[] };
    let item: CvItem;
    const sec = SECTION.exec(raw);
    if (i === 0) item = { ...base, kind: "name" };
    else if (i === 1) item = { ...base, kind: "headline" };
    else if (i === 2) item = { ...base, kind: "contact" };
    else if (sec && !raw.includes("\t") && !raw.includes(":**")) {
      section = sec[1].trim();
      role = undefined;
      inCard = false;
      const rest = sec[2].trim();
      // "**LANGUAGES**  English C1 · …": heading and content on one line.
      if (rest) {
        items.push({ ...base, section, kind: "section", text: section });
        item = { ...base, id: `L${i}c`, section, kind: "other", text: stripMd(rest), label: section };
      } else item = { ...base, section, kind: "section", text: section };
    } else if (section === "PROFESSIONAL PROFILE") item = { ...base, kind: "summary" };
    else if (raw.includes("\t") && raw.startsWith("**")) {
      const [head, years = ""] = raw.split("\t");
      role = stripMd(head).replace(/\s+\|\s+/, " | ");
      inCard = false;
      item = { ...base, kind: "role", role, title: role, years: years.trim() };
    } else if (SKILL_ROW.test(raw) && section !== "PROFESSIONAL EXPERIENCE") {
      const m = SKILL_ROW.exec(raw)!;
      item = { ...base, kind: "skill_row", label: m[1].trim(), items: m[2].split("·").map((s) => s.trim()).filter(Boolean) };
    } else if (section === "PROFESSIONAL EXPERIENCE" && BULLET.test(raw)) {
      const m = BULLET.exec(raw)!;
      item = { ...base, role, kind: inCard ? "bullet" : "other_bullet", lead: m[1].trim(), body: m[2].trim() };
    } else if (section === "PROFESSIONAL EXPERIENCE" && CARD.test(raw)) {
      const m = CARD.exec(raw)!;
      inCard = true;
      item = { ...base, role, kind: "card", title: m[1].trim(), context: m[2].trim() };
    } else if (section === "PROFESSIONAL EXPERIENCE" && !raw.startsWith("**") && !raw.startsWith("- ") && !inCard) {
      item = { ...base, role, kind: "scope" };
    } else if (raw.startsWith("- ")) item = { ...base, role, kind: "other_bullet", body: stripMd(raw.slice(2)) };
    else item = { ...base, kind: "other" };
    items.push(item);
  });

  for (const it of items) {
    if (it.kind === "section") continue;
    // Hits use the text the ATS scan reads for that line; unscanned lines (headline, contact) are
    // still matched on their visible text so the page can show them, flagged as not scanned.
    const hay = it.scanned ? scanned.get(it.line)! : it.text;
    it.hits = terms.filter((t) => keywordInText(t, hay));
  }
  return items;
}
